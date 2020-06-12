from argparse import ArgumentParser
import asyncio
import enum
import glob
import logging
import os
import ssl
import time
from functools import partial
from traceback import StackSummary
from statistics import median
from tempfile import NamedTemporaryFile
from typing import Tuple, List, IO, Set, Dict
from .. import tor_client
from .. import results_logger
from ..tor_ctrl_msg import CoordStartMeas
from .. import msg
import stem  # type: ignore
from stem import CircStatus  # type: ignore
from stem.control import Controller, EventType  # type: ignore
from stem.response.events import CircuitEvent, FFMeasEvent  # type: ignore
from transitions import Machine  # type: ignore


class MeasrProtocol(asyncio.Protocol):
    ''' How we communicate with measurers. Not much should be housed here. Hand
    stuff off to the main state machines to handle received data.
    '''
    transport = None

    def connection_made(self, transport):
        # TODO: log host:port of measurer
        log.debug('Connection from measurer')
        self.transport = transport
        machine.notif_measurer_connected(self)

    def connection_lost(self, exc):
        log.debug('Lost connection with measurer')
        machine.notif_measurer_disconnected(self)
        # TODO: anything else need to be done?

    def data_received(self, data: bytes):
        log.info('Received %d bytes: %s', len(data), data)
        m = msg.FFMsg.deserialize(data)
        machine.recv_measr_msg(self, m)


class MStates(enum.Enum):
    ''' States that a specific measurement can be in '''
    # Starting state. We haven't done anything yet. Nothing happens here.
    START = enum.auto()
    # Coordinator (we) are connecting to the relay
    COORD_CONNECTING = enum.auto()
    # We are sending measurement params to the relay and waiting for them to
    # accept them
    SENDING_PARAMS = enum.auto()
    # Measurers are connecting to the relay
    MEASR_CONNECTING = enum.auto()
    # Measurement is happening
    MEASUREMENT = enum.auto()
    # An error happened. We need to inform everyone
    ERROR = enum.auto()
    # Wrap up time
    CLEANUP = enum.auto()


class MStateMachine(Machine):
    ''' We can run many measurements at once. This is the state machine for a
    specific measurement.

    The ideal state flow is:
        1. Coordinator connects to the relay. COORD_CONNECTING
        2. Wait to hear back from tor if the relay accepted the measurement
           params. SENDING_PARAMS
        3. Tell measurers to connect to the relay. MEASR_CONNECTING
        4. Hear back that they all have done so successfully. Start measuring.
           MEASUREMENT
        5. When the measurement is over, cleanup and exit this state machine.
           CLEANUP
    '''
    tor_client: Controller
    measurers: Set[MeasrProtocol]
    ready_measurers: Set[MeasrProtocol]
    relay_fp: str
    relay_circ: int
    meas_duration: int
    # First int is sent, second is received. From that party's perspective.
    bg_reports: List[Tuple[int, int]]
    measr_reports: Dict[MeasrProtocol, Tuple[int, int]]

    def __init__(
            self, tor_client: Controller, relay_fp: str, meas_duration: int,
            measurers: Set[MeasrProtocol]):
        self.tor_client = tor_client
        self.relay_fp = relay_fp
        self.meas_duration = meas_duration
        self.measurers = measurers
        self.ready_measurers = set()
        self.relay_circ = None
        self.bg_reports = []
        self.measr_reports = {m: [] for m in self.measurers}
        super().__init__(
            model=self,
            states=MStates,
            transitions=[
                {
                    'trigger': 'change_state_starting',
                    'source': MStates.START,
                    'dest': MStates.COORD_CONNECTING,
                },
                {
                    'trigger': 'change_state_coord_connected',
                    'source': MStates.COORD_CONNECTING,
                    'dest': MStates.SENDING_PARAMS,
                },
                {
                    'trigger': 'change_state_params_accepted',
                    'source': MStates.SENDING_PARAMS,
                    'dest': MStates.MEASR_CONNECTING,
                },
                {
                    'trigger': 'change_state_measr_connected',
                    'source': MStates.MEASR_CONNECTING,
                    'dest': MStates.MEASUREMENT,
                },
                {
                    'trigger': 'change_state_measurement_done',
                    'source': [MStates.MEASUREMENT, MStates.ERROR],
                    'dest': MStates.CLEANUP,
                },
                {
                    'trigger': 'change_state_error',
                    'source': '*',
                    'dest': MStates.ERROR,
                },
            ],
            initial=MStates.START,
            # Do not create .to_<state>() methods, which allow transition to
            # <state> regardless of current state
            auto_transitions=False,
        )

    def _connect_to_relay(self):
        ''' Main function for COORD_CONNECTING state. Tell our tor client to
        build a circuit to the relay. We block a very short time until we hear
        that the circuit has launched (not built) and save the circuit id for
        later. This is the "control circuit" with the relay for a FlashFlow
        measurement. '''
        # Send the command to our tor client to start a measurement with the
        # given relay
        ret = tor_client.send_msg(
            self.tor_client,
            CoordStartMeas(self.relay_fp, self.meas_duration))
        # Make sure the circuit launch went well. Note it isn't built yet. It's
        # just that tor found nothing obviously wrong with trying to build this
        # circuit.
        if not ret.is_ok():
            self.change_state_error(
                'Failed to start circuit to %s: %s' % (self.relay_fp, ret))
            return
        # We expect to see "250 LAUNCHED <circ_id>", e.g. "250 LAUNCHED 24".
        # Get the circuit id out and save it for later use.
        code, _, content = ret.content()[0]
        assert code == '250'
        parts = content.split(' ')
        if len(parts) != 2 or parts[0] != 'LAUNCHED':
            self.change_state_error(
                'Did not expect body of message to be: %s' % (content,))
            return
        self.relay_circ = int(parts[1])
        log.info('Circ %d is our circuit with the relay', self.relay_circ)
        # That's all for now. We stay in this state until Tor tells us it has
        # finished building the circuit

    def _tell_measr_to_connect(self):
        ''' Main function for MEASR_CONNECTING state. '''
        # TODO: num circuits as a param
        m = msg.ConnectToRelay(self.relay_fp, 10, self.meas_duration)
        for measr in self.measurers:
            measr.transport.write(m.serialize())

    def _cleanup(self):
        ''' Main function for CLEANUP state. '''
        if self.relay_circ:
            log.info('cleanup: closing relay circ %s', self.relay_circ)
            try:
                self.tor_client.close_circuit(self.relay_circ)
            except stem.InvalidArguments:
                # probably unknown circ
                pass
            except Exception as e:
                log.warn('Error closing relay circ: %s %s', type(e), e)
            finally:
                self.relay_circ = None

    def _tell_all_failure(self, f: msg.Failure):
        # TODO: tell tor client too
        log.error(f.desc)
        for measr in self.measurers:
            try:
                measr.transport.write(f.serialize())
            except Exception as e:
                log.warn('Error sending Failure msg to measr: %s', e)
                continue

    def _tell_all_go(self):
        # Tell our tor client
        ret = tor_client.send_msg(
            self.tor_client,
            CoordStartMeas(self.relay_fp, self.meas_duration))
        if not ret.is_ok():
            self.change_state_error(
                "Unable to tell our tor client it\'s time to start "
                "active measurement")
            return
        # Tell the measurers
        for m in self.ready_measurers:
            m.transport.write(msg.Go().serialize())

    def _have_all_reports(self):
        ''' Check if we have at least the expected number of per-second reports
        from the relay and from all of the measurers. '''
        if len(self.bg_reports) < self.meas_duration:
            return False
        for measr_reports in self.measr_reports.values():
            if len(measr_reports) < self.meas_duration:
                return False
        return True

    def _write_measurement_results(self):
        ''' Called when we have successfully completed a measurement and should
        write out measurement results to our file. That's not implemented yet,
        but we can at least log information about the measurment. '''
        # TODO: actual timestamps
        now = int(time.time())
        results_logger.write_begin(self.relay_fp, now)
        # Take the minimum of send/recv from the relay's bg reports for each
        # second. These are untrusted results because the relay may have lied
        # about having a massive amount of background traffic
        bg_report_untrust = [min(s, r) for s, r in self.bg_reports]
        # Always take the recv side of measurer reports since that's the only
        # side that definitely made it back from the relay
        measr_reports = []
        for measr_report in self.measr_reports.values():
            measr_reports.append([r for _, r in measr_report])
            for res in measr_reports[-1]:
                results_logger.write_meas(self.relay_fp, now, res)
        # For each second, cap the amount of claimed bg traffic to the maximum
        # amount we will trust. I.e. if the relay is supposed to reserve no
        # more than 25% of its capacity for bg traffic, make sure the reported
        # background traffic is no more than 25% of all data we have for that
        # second.
        # TODO: make the fraction configurable
        bg_report_trust = []
        for sec_i, bg_untrust in enumerate(bg_report_untrust):
            # The relay is supposed to be throttling its bg traffic such that
            # it is no greater than some fraction of total traffic.
            #     frac = bg / (bg + meas)
            # We know and trust meas. We know frac. Thus we can solve for the
            # maximum allowed bg:
            #     frac * bg + frac * meas = bg
            #     frac * bg - bg          = -frac * meas
            #     bg * (frac - 1)         = -frac * meas
            #     bg                      = (-frac * meas) / (frac - 1)
            #     bg                      = (frac * meas) / (1 - frac)
            frac = 0.25
            meas = sum([
                measr_report[sec_i] for measr_report in measr_reports])
            max_bg = frac * meas / (1 - frac)
            if bg_untrust > max_bg:
                log.warn(
                    'Capping %s\'s reported bg to %d as %d is too much',
                    self.relay_fp, max_bg, bg_untrust)
            bg_report_trust.append(min(bg_untrust, max_bg))
            results_logger.write_bg(self.relay_fp, now, bg_untrust, max_bg)
        # Calculate each second's aggregate bytes
        aggs = [
            sum(sec_i_vals) for sec_i_vals
            in zip(bg_report_trust, *measr_reports)]
        # Calculate the median over all seconds
        res = median(aggs)
        # Log as Mbit/s
        log.info('%s was measured at %.2f Mbit/s', self.relay_fp, res*8/1e6)
        results_logger.write_end(self.relay_fp, now)

    # ########################################################################
    # STATE CHANGE EVENTS. These are called when entering the specified state.
    # ########################################################################

    def on_enter_COORD_CONNECTING(self):
        loop.call_soon(self._connect_to_relay)

    def on_enter_MEASR_CONNECTING(self):
        loop.call_soon(self._tell_measr_to_connect)

    def on_enter_MEASUREMENT(self):
        loop.call_soon(self._tell_all_go)

    def on_enter_ERROR(self, err_str: str):
        loop.call_soon(partial(self._tell_all_failure, msg.Failure(err_str)))
        loop.call_soon(partial(self.change_state_measurement_done, False))

    def on_enter_CLEANUP(self, success: bool):
        if success:
            loop.call_soon(self._write_measurement_results)
        loop.call_soon(self._cleanup)
        loop.call_soon(partial(machine.notif_measurement_done, self, success))

    # ########################################################################
    # MISC EVENTS. These are called from other parts of the coord code.
    # ########################################################################

    def recv_msg_connected_to_relay(
            self, measr: MeasrProtocol, message: msg.ConnectedToRelay):
        ''' Receive a ConnectedToRelay FFMsg from a measurer. The main coord
        state machine has relayed to this because the sending measurer is one
        of ours.
        '''
        assert measr in self.measurers
        if not message.success:
            self._tell_all_failure(msg.Failure(
                'A measurer failed to connect to the relay'))
            return
        self.ready_measurers.add(measr)
        if len(self.ready_measurers) == len(self.measurers):
            log.debug(
                'All %d measurers have connected', len(self.ready_measurers))
            self.change_state_measr_connected()
            return

    def recv_msg_measr_bw_report(
            self, measr: MeasrProtocol, message: msg.BwReport):
        ''' Receive a BwReport FFMsg from a measurer. The main coord
        state machine has relayed to this because the sending measurer is one
        of ours.
        '''
        assert measr in self.measr_reports
        self.measr_reports[measr].append((message.sent, message.recv))
        log.debug(
            'BwReport #%d says %d/%d sent/recv measr bytes',
            len(self.measr_reports[measr]),
            message.sent, message.recv)
        if self._have_all_reports():
            self.change_state_measurement_done(True)
        return

    def notif_circ_event(self, event: CircuitEvent):
        ''' Recieve a CIRC event from our tor client

        The main coord state machine calls this. We didn't set up the
        subscription to these events. The main coord state machine should have
        also ensured we are back in the main thread.

        We want to know about circuit events for the following reasons:
            - When we have recently launched our circuit with the relay and
            want to know when it is built so we can go to the next state
            - TODO failures
            - TOOD other reasons
        '''
        # Make super sure this event is for us
        if int(event.id) != self.relay_circ:
            log.warn(
                'Ignoring CIRC event not for us. %d vs %d. This should ' +
                'have been caught earlier.', self.relay_circ, int(event.id))
            return
        # It's for us, and the circuit has just been built. If we're in the
        # right state for this, continue on to the next state. Otherwise this
        # was unexpected and should error out
        if event.status == CircStatus.BUILT:
            if self.state == MStates.COORD_CONNECTING:
                self.change_state_coord_connected()
            else:
                self.change_state_error(
                    'Found out circ %d is done building, but that '
                    'shouldn\'t happen in state %s' %
                    (int(event.id), self.state))
            return
        # It's for us, and the circuit is still getting built. Don't care.
        # Ignore.
        elif event.status in [CircStatus.LAUNCHED, CircStatus.EXTENDED]:
            # ignore these
            return
        # It's for us, and the circuit has been closed. TODO this might be fine
        # in some case?
        elif event.status == CircStatus.CLOSED:
            log.warn('circ %d with relay closed', self.relay_circ)
            self.change_state_error(
                'Circ %d closed unexpectedly' % (int(event.id),))
            return
        elif event.status == CircStatus.FAILED:
            log.error('circ %d entered failed state: %s', int(event.id), event)
            self.change_state_error(
                'Cird %d failed' % (int(event.id),))
            return
        # It's for us, but don't know how to handle it yet
        log.warn('Not handling CIRC event for us: %s', event)

    def notif_ffmeas_event(self, event: FFMeasEvent):
        ''' Receive a FF_MEAS event from our tor client

        The main coord state machine calls this. We didn't set up the
        subscription to these events. The main coord state machine should have
        also ensured we are back in the main thread.
        '''
        # Make super sure this is for us, even though this should have been
        # done already.
        if event.circ_id != self.relay_circ:
            log.warn(
                'Ignoring FF_MEAS event for different measurement ' +
                '%d vs %d. This should have been caught earlier',
                self.relay_circ, event.circ_id)
            return
        # It's for us, and the meas params cell has been sent to the relay.
        if event.ffmeas_type == 'PARAMS_SENT':
            log.debug(
                'Measurement params have been sent to the relay on circ %s',
                self.relay_circ)
            # and that's it. We expect another notification when the relay has
            # accepted or rejected the parameters
            return
        # It's for us, and the relay has signaled whether or not they are okay
        # with the parameters and getting measured right now.
        elif event.ffmeas_type == 'PARAMS_OK':
            if not event.accepted:
                self.change_state_error('Relay rejected measurement params')
                return
            self.change_state_params_accepted()
            return
        elif event.ffmeas_type == 'BW_REPORT':
            self.bg_reports.append((event.sent, event.recv))
            log.debug(
                'BW_REPORT #%d says %d/%d sent/recv background bytes',
                len(self.bg_reports), event.sent, event.recv)
            if self._have_all_reports():
                self.change_state_measurement_done(True)
            return
        # It's for us, but don't know how to handle it yet
        log.warn('Not handling FF_MEAS event that is for us: %s', event)


class States(enum.Enum):
    ''' States that we, as a FlashFlow coordinator, can be in '''
    START = enum.auto()
    # First "real" state. Open all listening sockets
    ENSURE_LISTEN_SOCKS = enum.auto()
    # Next real state. Launch a tor client and connect to it
    ENSURE_CONN_W_TOR = enum.auto()
    # We're ready to start doing stuff, if we aren't busy already
    READY = enum.auto()
    # There was some sort of error that calls for cleaning everything up and
    # essentially relaunching, but we shouldn't outright die.
    NONFATAL_ERROR = enum.auto()
    # There is a serious error that isn't recoverable. Just cleanup and die.
    FATAL_ERROR = enum.auto()


class StateMachine(Machine):
    ''' State machine and main control flow hub for FlashFlow coordinator.

    change_state_*:
        State transitions are named change_state_* and don't exist here in the
        code. See the analogous docstring in the StateMachine for measurers for
        more information.

    on_enter_*:
        This is how the Machine class finds functions to call upon entering the
        given state. See the analogous docstring in the StateMachine for
        measurers for more information.

    _*:
        Other internal functions. See their documentation for more information
        on them.
    '''
    # conf  # This is set in __init__
    server: asyncio.base_events.Server
    tor_client: Controller
    measurers: List[MeasrProtocol]
    measurements: List[MStateMachine]

    def __init__(self, conf):
        self.conf = conf
        self.measurements = []
        self.measurers = []
        super().__init__(
            model=self,
            states=States,
            transitions=[
                {
                    'trigger': 'change_state_starting',
                    'source': [States.START, States.NONFATAL_ERROR],
                    'dest': States.ENSURE_LISTEN_SOCKS,
                },
                {
                    'trigger': 'change_state_listening',
                    'source': States.ENSURE_LISTEN_SOCKS,
                    'dest': States.ENSURE_CONN_W_TOR,
                },
                {
                    'trigger': 'change_state_connected_to_tor',
                    'source': States.ENSURE_CONN_W_TOR,
                    'dest': States.READY,
                },
                {
                    'trigger': 'change_state_nonfatal_error',
                    'source': '*',
                    'dest': States.NONFATAL_ERROR,
                },
                {
                    'trigger': 'change_state_fatal_error',
                    'source': '*',
                    'dest': States.FATAL_ERROR,
                },
            ],
            initial=States.START,
            # Do not create .to_<state>() methods, which allow transition to
            # <state> regardless of current state
            auto_transitions=False,
        )

    def _ensure_listen_socks(self):
        ''' Main function in the ENSURE_LISTEN_SOCKS state. Open listening
        sockets '''
        # Get (host, port) from "host:port"
        addr_port = self.conf.getaddr('coord', 'listen_addr')
        if addr_port is None:
            log.error('Don\'t know what to listen on')
            self.change_state_fatal_error()
            return
        # Make sure TLS key material exists
        our_key = self.conf.getpath('coord', 'key')
        keydir = self.conf.getpath('coord', 'keydir')
        if not os.path.isfile(our_key):
            log.error('%s does not exist', our_key)
            self.change_state_fatal_error()
            return
        if not os.path.isdir(keydir):
            log.error('%s does not exist', keydir)
            self.change_state_fatal_error()
            return
        # Start building ssl context. This first bit is a helper that takes the
        # measurer certificate files and combines them into one big file
        # listing them all, since that's what python's ssl wants
        _, measr_cert_fname = _gen_concated_measr_cert_file(keydir, our_key)
        ssl_context = ssl.SSLContext()
        # Load our TLS private key and certificate
        ssl_context.load_cert_chain(our_key)
        # Load the certificate of the measurers
        ssl_context.load_verify_locations(measr_cert_fname)
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        # Create the async task of opening this listen socks.
        task = loop.create_task(loop.create_server(
            MeasrProtocol,
            addr_port[0], addr_port[1],
            ssl=ssl_context,
            reuse_address=True,
        ))

        # Callback to find out the result of the attempt to open listen sockets
        def cb(fut):
            exc = fut.exception()
            if exc:
                log.error('Unable to open listen socket(s): %s', exc)
                self.change_state_fatal_error()
                return
            self.server = fut.result()
            for s in self.server.sockets:
                log.info('Listening on %s for measurers', s.getsockname())
            self.change_state_listening()
        # Attach the callback so we find out the results. This will happen
        # asynchronously after we return. And we're returning now.
        task.add_done_callback(cb)

    def _ensure_conn_w_tor(self):
        ''' Main function in the ENSURE_CONN_W_TOR state. Launch a tor client
        and connect to it. Save the Controller object. '''
        assert self.state == States.ENSURE_CONN_W_TOR
        # TODO: what happens if tor client disappears? Exception thrown? What??
        # And what should we do about it? Try to relaunch? Just die? Choose
        # **something**
        c = tor_client.launch(
            self.conf.getpath('tor', 'tor_bin'),
            self.conf.getpath('coord', 'tor_datadir'),
            self.conf.get('tor', 'torrc_extra_lines')
        )
        if not c:
            log.error('Unable to launch and connect to tor client')
            self.change_state_fatal_error()
            return
        c.add_event_listener(self.notif_circ_event, EventType.CIRC)
        c.add_event_listener(self.notif_ffmeas_event, EventType.FF_MEAS)
        self.tor_client = c
        self.change_state_connected_to_tor()

    def _cleanup(self):
        ''' Cleanup all of our state while being very careful to not allow any
        exceptions to bubble up. Use this when in an error state and you want
        to cleanup before starting over or just dying. '''
        if hasattr(self, 'server') and self.server:
            log.info('cleanup: closing listening sockets')
            try:
                self.server.close()
            except Exception as e:
                log.error('Error closing listening sockets: %s', e)
        if hasattr(self, 'tor_client') and self.tor_client:
            log.info('cleanup: closing tor')
            try:
                self.tor_client.close()
            except Exception as e:
                log.error('Error closing tor: %s', e)

    def _die(self):
        ''' End execution of the program. '''
        loop.stop()

    # ########################################################################
    # STATE CHANGE EVENTS. These are called when entering the specified state.
    # ########################################################################

    def on_enter_ENSURE_LISTEN_SOCKS(self):
        loop.call_soon(self._ensure_listen_socks)

    def on_enter_ENSURE_CONN_W_TOR(self):
        loop.call_soon(self._ensure_conn_w_tor)

    def on_enter_READY(self):
        pass

    def on_enter_NONFATAL_ERROR(self, err_msg):
        self._cleanup()
        log.error(err_msg)
        loop.call_soon(self.change_state_starting)

    def on_enter_FATAL_ERROR(self):
        self._cleanup()
        self._die()

    # ########################################################################
    # MEASSAGES FROM MEASRs. These are called when a measurer tells us
    # something.
    # ########################################################################

    def recv_measr_msg(self, measr: MeasrProtocol, message: msg.FFMsg):
        ''' Receive a FFMsg object from one of our measurers '''
        msg_type = type(message)
        state = self.state
        if msg_type == msg.ConnectedToRelay and state == States.READY:
            assert isinstance(message, msg.ConnectedToRelay)  # so mypy knows
            self._recv_msg_connected_to_relay(measr, message)
            return
        elif msg_type == msg.BwReport and state == States.READY:
            assert isinstance(message, msg.BwReport)  # so mypy knows
            self._recv_msg_measr_bw_report(measr, message)
            return
        self.change_state_nonfatal_error(
            'Unexpected %s message received in state %s' %
            (msg_type, state))

    def _recv_msg_connected_to_relay(
            self, measr: MeasrProtocol, message: msg.ConnectedToRelay):
        # TODO: be able to handle multiple measurements at once
        for m in [m for m in self.measurements if measr in m.measurers]:
            m.recv_msg_connected_to_relay(measr, message)

    def _recv_msg_measr_bw_report(
            self, measr: MeasrProtocol, message: msg.BwReport):
        # TODO: be able to handle multiple measurements at once
        for m in [m for m in self.measurements if measr in m.measurers]:
            m.recv_msg_measr_bw_report(measr, message)

    # ########################################################################
    # MISC EVENTS. These are called from other parts of the coord code.
    # ########################################################################

    def notif_sslerror(self, exc: ssl.SSLError, trans):
        ''' Called from the last-chance exception handler to tell us about TLS
        errors. For example, measurer connected to us with a bad client cert
        '''
        log.debug(
            'Someone (%s) failed to TLS handshake with us: %s',
            trans.get_extra_info('peername'), exc)
        trans.close()

    def notif_measurer_connected(self, measurer: MeasrProtocol):
        ''' Called from MeasrProtocol when a connection is successfully made
        from a measurer '''
        self.measurers.append(measurer)
        log.debug('Now have %d measurers', len(self.measurers))
        # start a toy measurement for testing
        m = MStateMachine(
            self.tor_client,
            'relay1',
            self.conf.getint('meas_params', 'meas_duration'),
            [_ for _ in self.measurers])
        m.change_state_starting()
        self.measurements.append(m)

    def notif_measurer_disconnected(self, measurer: MeasrProtocol):
        ''' Called from MeasrProtocol when a connection with a measurer has
        been lost '''
        self.measurers = [m for m in self.measurers if m != measurer]
        log.debug('Measurer lost. Now have %d', len(self.measurers))
        # TODO: need to do error stuff if they were a part of any measurements

    def notif_measurement_done(self, meas: MStateMachine, success: bool):
        ''' Called from an individual measurement's state machine to tell us it
        is done. '''
        log.debug(
            'Learned of a %ssuccessful measurement',
            'non-' if not success else '')
        self.measurements = [m for m in self.measurements if m != meas]
        log.debug('Now have %d saved measurements', len(self.measurements))

    def notif_circ_event(self, event: CircuitEvent):
        ''' Called from stem to tell us about circuit events. We usually don't
        care, but sometimes we are waiting on a circuit to be built with a
        relay.

        These events come from a different thread. We tell the main thread's
        loop (in a threadsafe manner) to handle this event.
        '''
        circ_id = int(event.id)
        for m in [m for m in self.measurements if m.relay_circ == circ_id]:
            loop.call_soon_threadsafe(partial(m.notif_circ_event, event))

    def notif_ffmeas_event(self, event: FFMeasEvent):
        ''' Called from stem to tell us about FF_MEAS events. Pass them off to
        ongoing measurements.

        These events come from a different thread. We tell the main thread's
        loop (in a threadsafe manner) to handle this event.
        '''
        circ_id = event.circ_id
        for m in [m for m in self.measurements if m.relay_circ == circ_id]:
            loop.call_soon_threadsafe(partial(m.notif_ffmeas_event, event))


log = logging.getLogger(__name__)
loop = asyncio.get_event_loop()
machine: StateMachine


def _exception_handler(loop, context):
    ''' Last resort exception handler

    This will only catch exceptions that happen in the main thread. Others very
    well may go entirely unnoticed and unlogged.

    Some exceptions are unexpected, so we end up here. For these we kill
    ourselves after logging about the exception.

    Other exceptions are impossible to catch before we get here. For example, a
    client failing the TLS handshake with us. (ugh what the fuck). For these we
    notify the state machine so it can react.
    '''
    # Check for exceptions that should not be fatal and we should tell other
    # parts of the code about so they can react intelligently
    if 'exception' in context:
        exception_type = type(context['exception'])
        # Check for recoverable TLS errors
        if exception_type == ssl.SSLError:
            if 'transport' in context:
                machine.notif_sslerror(
                    context['exception'], context['transport'])
                return
            else:
                log.warn(
                    'SSLError caught without a transport too. Cannot pass ' +
                    'to state machine to handle gracefully.')
        # Additional recoverable errors would continue here
    # All other exceptions. These are fatal
    log.error('%s', context['message'])
    if 'exception' in context:
        log.error('%s %s', type(context['exception']), context['exception'])
    if 'handle' in context:
        log.error(context['handle'])
    if 'source_traceback' in context:
        log.error('Traceback:')
        summary = StackSummary.from_list(context['source_traceback'])
        for line_super in summary.format():
            # The above line has multiple lines in it
            for line in line_super.split('\n'):
                if len(line):
                    log.error('  %s', line)
    else:
        log.error(
            'Traceback not available. Maybe run with PYTHONASYNCIODEBUG=1')
    machine.change_state_fatal_error()


def _gen_concated_measr_cert_file(
        d: str, coord_fname: str) -> Tuple[IO[str], str]:
    ''' Search for measurer certs in the given directory (being careful to
    ignore any file matching the given coord cert filename). Read them all into
    a new temporary file and return its name. Will always return a filename,
    even if it is empty. '''
    cert_fnames = _measr_cert_files(d, coord_fname)
    # + indicates "updating" AKA reading and writing
    fd = NamedTemporaryFile('w+')
    for cert in cert_fnames:
        with open(cert, 'rt') as fd_in:
            fd.write(fd_in.read())
    fd.seek(0, 0)
    log.debug('Stored %d measurer certs in %s', len(cert_fnames), fd.name)
    return fd, fd.name


def _measr_cert_files(d: str, coord_fname: str) -> List[str]:
    ''' Look in the directory `d` for files ending with '.pem', recursively. If
    any found file matches `coord_fname` by name exactly, then ignore it.
    Return all other files found. If no allowed files are found, returns an
    empty list. '''
    out = []
    for fname in glob.iglob(os.path.join(d, '*.pem'), recursive=True):
        if fname == coord_fname:
            continue
        log.debug('Treating %s as a measurer cert file', fname)
        out.append(fname)
    return out


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow coordinator.'
    p = sub.add_parser('coord', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    global machine
    os.makedirs(conf.getpath('coord', 'datadir'), mode=0o700, exist_ok=True)
    os.makedirs(conf.getpath('coord', 'keydir'), mode=0o700, exist_ok=True)
    machine = StateMachine(conf)
    loop.set_exception_handler(_exception_handler)
    loop.call_soon(machine.change_state_starting)
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    return
