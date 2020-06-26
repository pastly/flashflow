from argparse import ArgumentParser
import asyncio
import enum
import glob
import logging
import os
import random
import ssl
import time
from functools import partial
from traceback import StackSummary
from statistics import median
from tempfile import NamedTemporaryFile
from typing import Tuple, List, IO, Set, Dict, Optional
from .. import tor_client
from .. import results_logger
from ..tor_ctrl_msg import CoordStartMeas
from .. import msg
from stem import CircStatus  # type: ignore
from stem.control import Controller, EventType  # type: ignore
from stem.response.events import CircuitEvent, FFMeasEvent  # type: ignore
from transitions import Machine  # type: ignore


def next_meas_id() -> int:
    ''' Generate a new measurement ID '''
    return random.randint(1, 2**32-1)


class MeasrProtocol(asyncio.Protocol):
    ''' How we communicate with measurers.

    Very little should be done here. Parse the bytes then give objects to the
    main state machines to handle.
    '''
    transport: Optional[asyncio.Transport] = None

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
        # XXX: It's possible we receive an incomplete JSON string
        # https://gitlab.torproject.org/pastly/flashflow/-/issues/10
        log.info('Received %d bytes: %s', len(data), data)
        m = msg.FFMsg.deserialize(data)
        machine.notif_measr_msg(self, m)


class CtrlProtocol(asyncio.Protocol):
    ''' Development/debugging control communication protocol '''
    transport: Optional[asyncio.Transport] = None

    def connection_made(self, transport):
        # TODO: log host:port of controller
        log.debug('Connection from controller')
        self.transport = transport
        # machine.notif_measurer_connected(self)

    def connection_lost(self, exc):
        log.debug('Lost connection with controller')
        # machine.notif_measurer_disconnected(self)

    def data_received(self, data: bytes):
        # log.debug('ctrl: %s', data)
        assert self.transport is not None
        success, err_str = machine.notif_ctrl_message(data.decode('utf-8'))
        if success:
            self.transport.write(b'OK')
        else:
            self.transport.write(err_str.encode('utf-8'))
        self.transport.close()


class Measurement:
    ''' State related to a single measurment. '''
    #: Measurement ID
    meas_id: int
    #: The fingerprint of the relay to measure
    relay_fp: str
    #: Our circuit id with the relay. Filled in once we actually have one
    relay_circ: Optional[int]
    #: The measurers participating in this measurement
    measurers: Set[MeasrProtocol]
    #: The measurers that have indicated the are ready to begin active
    #: measurement
    ready_measurers: Set[MeasrProtocol]
    #: The duration, in seconds, that active measurement should last.
    meas_duration: int
    #: The percent of background traffic, as a fraction between 0 and 1, that
    #: the relay should be limiting itself to.
    bg_percent: float
    #: Per-second reports from the relay with the number of the bytes of
    #: background traffic it is carrying. Each tuple is ``(time, sent_bytes,
    #: received_bytes)`` where time is the timestamp at which the report was
    #: received and sent/received are from the relay's perspective.
    bg_reports: List[Tuple[float, int, int]]
    #: Per-second reports from the measurers with the number of bytes of
    #: measurement traffic. Each tuple is ``(time, sent_bytes,
    #: received_bytes)``, where time is the timestamp at which the report was
    #: received and sent/received are from the measurer's perspective.
    measr_reports: Dict[MeasrProtocol, List[Tuple[float, int, int]]]

    def __init__(
            self, meas_id: int, relay_fp: str,
            measurers: Set[MeasrProtocol],
            meas_duration: int, bg_percent: float):
        self.meas_id = meas_id
        self.relay_fp = relay_fp
        self.relay_circ = None  # we build the circ and get a circ id later
        self.measurers = measurers
        self.ready_measurers = set()
        self.meas_duration = meas_duration
        self.bg_percent = bg_percent
        self.bg_reports = []
        self.measr_reports = {m: [] for m in self.measurers}

    def start_and_end(self) -> Tuple[float, float]:
        ''' Return our best idea for what the start and end timestamp of this
        :class:`Measurement` are.

        This method assumes the measurement is finished.

        We currently only consider the timestamps in the background reports;
        these have timestamps that *we* generated, so if we always use only
        ourself as the authority on what time it is, we'll always be
        consistent. Consider a deployment with at lesat two measurers, one with
        a very fast clock and another with a very slow clock. If we instead,
        for example, took the earliest timestamp as the start and the latest
        timestamp as the end, then not only could the measurement "last" more
        than its actual duration, but our accuracy on start/end times would
        change as the set of measureers used for each measurement changes.
        '''
        start_ts = self.bg_reports[0][0]
        end_ts = self.bg_reports[-1][0]
        return start_ts, end_ts


class States(enum.Enum):
    ''' States that we, as a FlashFlow coordinator, can be in. '''
    #: State in which we are created and to which we return when there's a
    #: non-fatal error.
    START = enum.auto()
    #: First "real" state. Open all listening sockets.
    ENSURE_LISTEN_SOCKS = enum.auto()
    #: Second real state. Launch a tor client and connect to it.
    ENSURE_CONN_W_TOR = enum.auto()
    #: Normal state. We're doing measurements or waiting to decide to do them.
    #: We are usually here.
    READY = enum.auto()
    #: There was some sort of error that calls for cleaning everything up and
    #: essentially relaunching, but we shouldn't outright die.
    NONFATAL_ERROR = enum.auto()
    #: There is a serious error that isn't recoverable. Just cleanup and die.
    FATAL_ERROR = enum.auto()


class StateMachine(Machine):
    ''' State machine and main control flow hub for FlashFlow coordinator.

    change_state_*:
        State transitions are named change_state_* and don't exist here in the
        code. See the analogous docstring in
        :class:`flashflow.cmd.measurer.StateMachine` for more information.

    on_enter_*:
        This is how the :class:`Machine` class finds functions to call upon
        entering the given state. See the analogous docstring in the
        StateMachine for measurers for more information.

    _*:
        Other internal functions. See their documentation for more information
        on them.
    '''
    # conf  # This is set in __init__
    #: Listening sockets for FlashFlow :mod:`flashflow.cmd.measurer`
    #: connections
    meas_server: asyncio.base_events.Server
    #: Listening sockets for FlashFlow :mod:`flashflow.cmd.ctrl` connections
    ctrl_server: asyncio.base_events.Server
    #: Stem controller object we have with our Tor client
    tor_client: Controller
    #: List of all connected measurers by their :class:`MeasrProtocol`
    measurers: List[MeasrProtocol]
    #: Storage of ongoing :class:`Measurement`\s
    measurements: Dict[int, Measurement]

    def __init__(self, conf):
        self.conf = conf
        self.measurements = {}
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
        sockets for measurers as well as for a FlashFlow controller '''
        # Get (host, port) from "host:port"
        measr_addr_port = self.conf.getaddr('coord', 'listen_addr')
        ctrl_addr_port = self.conf.getaddr('coord', 'ctrl_addr')
        if measr_addr_port is None:
            log.error('Don\'t know what to listen on')
            self.change_state_fatal_error()
            return
        if ctrl_addr_port is None:
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
        measr_task = loop.create_task(loop.create_server(
            MeasrProtocol,
            measr_addr_port[0], measr_addr_port[1],
            ssl=ssl_context,
            reuse_address=True,
        ))
        ctrl_task = loop.create_task(loop.create_server(
            CtrlProtocol,
            ctrl_addr_port[0], ctrl_addr_port[1],
            reuse_address=True,
        ))

        # Callback to find out the result of the attempt to open listen sockets
        def measr_cb(fut):
            exc = fut.exception()
            if exc:
                log.error('Unable to open listen socket(s): %s', exc)
                self.change_state_fatal_error()
                return
            self.meas_server = fut.result()
            for s in self.meas_server.sockets:
                log.info('Listening on %s for measurers', s.getsockname())
            self.change_state_listening()

        def ctrl_cb(fut):
            exc = fut.exception()
            if exc:
                log.error('Unable to open listen socket(s): %s', exc)
                self.change_state_fatal_error()
                return
            self.ctrl_server = fut.result()
            for s in self.ctrl_server.sockets:
                log.info('Listening on %s for FF controllers', s.getsockname())
        # Attach the callback so we find out the results. This will happen
        # asynchronously after we return. And we're returning now.
        measr_task.add_done_callback(measr_cb)
        ctrl_task.add_done_callback(ctrl_cb)

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
        if hasattr(self, 'meas_server') and self.meas_server:
            log.info('cleanup: closing listening sockets for measurers')
            try:
                self.meas_server.close()
            except Exception as e:
                log.error('Error closing listening sockets: %s', e)
        if hasattr(self, 'ctrl_server') and self.ctrl_server:
            log.info('cleanup: closing listening sockets for controllers')
            try:
                self.ctrl_server.close()
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

    def _have_all_bw_reports(self, meas: Measurement) -> bool:
        ''' Check if we have the expected number of ``bg_reports`` and
        ``measr_reports`` for the given measurement

        :param meas_id: The measurement ID. If we don't know about it, we
            return ``False``
        :returns: ``True`` if we have the expected number of reports from all
            parties, else ``False`` i.e. because unrecognized ``meas_id`` or
            simply yet-incomplete measurement
        '''
        # For debug logging purposes, gather all the report list lengths here.
        # We could return early as soon as we find one that isn't long enough.
        # But this may help debug.
        #
        # The first item is the number of background reports. All other items
        # are the number of reports from each measurer
        counts = [len(meas.bg_reports)] + [
            len(measr_n_reports) for measr_n_reports
            in meas.measr_reports.values()]
        num_expect = meas.meas_duration
        log.debug(
            'Meas %d has %d/%d bg reports. Num measr reports: %s',
            meas.meas_id, counts[0], num_expect,
            ', '.join([str(_) for _ in counts[1:]]))
        # Count how many of the counts are less than the number needed. If
        # a non-zero number of the counts are too small, not done yet
        if len(['' for c in counts if c < num_expect]):
            log.debug('Meas %d not finished', meas.meas_id)
            return False
        log.info('Meas %d is finished', meas.meas_id)
        return True

    def _finish_measurement(
            self, meas: Measurement, fail_msg: Optional[msg.Failure]):
        ''' We have finished a measurement, successful or not. Write out the
        results we have, tell everyone we are done, and forget about it. '''
        self._write_measurement_results(meas)
        if fail_msg is not None:
            for measr in meas.measurers:
                if measr.transport:
                    measr.transport.write(fail_msg.serialize())
        del self.measurements[meas.meas_id]
        log.info(
            'Meas %d finished.%s Now %d measurements.',
            meas.meas_id,
            '' if not fail_msg else ' Err="' + str(fail_msg) + '".',
            len(self.measurements))

    def _write_measurement_results(self, meas: Measurement):
        ''' We have completed a measurement (maybe successfully) and should
        write out measurement results to our file. '''
        start_ts, end_ts = meas.start_and_end()
        results_logger.write_begin(meas.relay_fp, int(start_ts))
        # Take the minimum of send/recv from the relay's bg reports for each
        # second. These are untrusted results because the relay may have lied
        # about having a massive amount of background traffic
        bg_report_untrust = [(ts, min(s, r)) for ts, s, r in meas.bg_reports]
        # Always take the recv side of measurer reports since that's the only
        # side that definitely made it back from the relay
        measr_reports = []
        for measr_report in meas.measr_reports.values():
            lst = [(ts, r) for ts, _, r in measr_report]
            for ts, r in lst:
                results_logger.write_meas(meas.relay_fp, int(ts), r)
            measr_reports.append([r for _, r in lst])
        # For each second, cap the amount of claimed bg traffic to the maximum
        # amount we will trust. I.e. if the relay is supposed to reserve no
        # more than 25% of its capacity for bg traffic, make sure the reported
        # background traffic is no more than 25% of all data we have for that
        # second.
        # TODO: make the fraction configurable
        bg_report_trust = []
        for sec_i, (ts, bg_untrust) in enumerate(bg_report_untrust):
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
            frac = meas.bg_percent
            measured = sum([
                measr_report[sec_i] for measr_report in measr_reports])
            max_bg = int(frac * measured / (1 - frac))
            if bg_untrust > max_bg:
                log.warn(
                    'Meas %d capping %s\'s reported bg to %d as %d is too '
                    'much', meas.meas_id, meas.relay_fp, max_bg, bg_untrust)
            bg_report_trust.append(min(bg_untrust, max_bg))
            results_logger.write_bg(meas.relay_fp, int(ts), bg_untrust, max_bg)
        # Calculate each second's aggregate bytes
        aggs = [
            sum(sec_i_vals) for sec_i_vals
            in zip(bg_report_trust, *measr_reports)]
        # Calculate the median over all seconds
        res = int(median(aggs))
        # Log as Mbit/s
        log.info(
            'Meas %d %s was measured at %.2f Mbit/s',
            meas.meas_id, meas.relay_fp, res*8/1e6)
        results_logger.write_end(meas.relay_fp, int(end_ts))

    def _notif_circ_event_BUILT(self, meas: Measurement, event: CircuitEvent):
        ''' Received CIRC event with status BUILT.

        Look for a Measurement waiting on this circ_id to be BUILT. '''
        assert event.status == CircStatus.BUILT
        circ_id = int(event.id)
        log.debug(
            'Found meas %d waiting on circ %d to be built. Not doing '
            'anything yet.', meas.meas_id, circ_id)

    def _notif_circ_event_CLOSED(self, meas: Measurement, event: CircuitEvent):
        pass

    def _notif_circ_event_FAILED(self, meas: Measurement, event: CircuitEvent):
        pass

    def _notif_ffmeas_event_PARAMS_SENT(
            self, meas: Measurement, event: FFMeasEvent):
        ''' Received FF_MEAS event with type PARAMS_SENT

        Log about it. There's nothing to do until we learn the relay's
        response, which we get later with PARAMS_OK. '''
        log.info(
            'Meas %d sent params on circ %d to relay %s',
            meas.meas_id, meas.relay_circ, meas.relay_fp)
        return

    def _notif_ffmeas_event_PARAMS_OK(
            self, meas: Measurement, event: FFMeasEvent):
        ''' Received FF_MEAS event with type PARAMS_OK

        Check if the relay accepted them or not. Drop the Measurement if not
        accepted, otherwise continue on to the next stage: telling the
        measurers to connect to the relay.
        '''
        if not event.accepted:
            # TODO: record as failed somehow pastly/flashflow#18
            log.warn(
                'Meas %d params not accepted: %s',
                meas.meas_id, event)
            del self.measurements[meas.meas_id]
            return
        assert event.accepted
        # TODO: num circs as a param pastly/flashflow#11
        m = msg.ConnectToRelay(
            meas.meas_id, meas.relay_fp, 10, meas.meas_duration)
        for measr in meas.measurers:
            assert measr.transport is not None
            measr.transport.write(m.serialize())
        return

    def _notif_ffmeas_event_BW_REPORT(
            self, meas: Measurement, event: FFMeasEvent):
        ''' Received FF_MEAS event with type BW_REPORT '''
        meas.bg_reports.append((time.time(), event.sent, event.recv))
        if self._have_all_bw_reports(meas):
            return self._finish_measurement(meas, None)
        return

    def _notif_measr_msg_ConnectedToRelay(
            self, measr: MeasrProtocol, message: msg.ConnectedToRelay):
        meas_id = message.orig.meas_id
        if meas_id not in self.measurements:
            log.info(
                'Received ConnectedToRelay for unknown meas %d, dropping.',
                meas_id)
            return
        meas = self.measurements[meas_id]
        if message.success:
            meas.ready_measurers.add(measr)
        else:
            # pastly/flashflow#19
            log.error(
                'Measurer was unable to connect to relay. This is a failure '
                'that we should handle, but are just dropping for now.')
            return
        ready_measr = meas.ready_measurers
        all_measr = meas.measurers
        log.debug(
            '%d/%d measurers are ready for meas %d',
            len(ready_measr), len(all_measr), meas_id)
        if len(ready_measr) == len(all_measr):
            log.debug('Sending Go message for meas %d', meas_id)
            ret = tor_client.send_msg(
                self.tor_client, CoordStartMeas(
                    meas.meas_id, meas.relay_fp, meas.meas_duration))
            if not ret.is_ok():
                fail_msg = msg.Failure(
                    msg.FailCode.C_START_ACTIVE_MEAS, meas_id,
                    extra_info=str(ret))
                log.error(fail_msg)
                for measr in ready_measr:
                    assert measr.transport is not None
                    measr.transport.write(fail_msg.serialize())
                del self.measurements[meas_id]
                return
            for measr in ready_measr:
                go_msg = msg.Go(meas_id)
                assert measr.transport is not None
                measr.transport.write(go_msg.serialize())
        return

    def _notif_measr_msg_BwReport(
            self, measr: MeasrProtocol, message: msg.BwReport):
        meas_id = message.meas_id
        if meas_id not in self.measurements:
            log.info(
                'Received BwReport for unknown meas %d, dropping.', meas_id)
            return
        meas = self.measurements[meas_id]
        meas.measr_reports[measr].append((
            message.ts, message.sent, message.recv))
        if self._have_all_bw_reports(meas):
            return self._finish_measurement(meas, None)
        return

    def _connect_to_relay(self, meas: Measurement):
        ''' Start the given measurement off by connecting ourselves to the
        necessary relay '''
        # Sanity: we haven't launched a circuit for this measurement yet
        if meas.relay_circ is not None:
            log.error(
                'Ready to connect to relay, but meas %d already has circ %d',
                meas.meas_id, meas.relay_circ)
            return
        # Tell our tor to launch the circuit
        ret = tor_client.send_msg(
            self.tor_client,
            CoordStartMeas(meas.meas_id, meas.relay_fp, meas.meas_duration))
        # Make sure it is LAUNCHED. Launched just means circuit construction is
        # started. BUILT is when the circuit is successfully built, and comes
        # later.
        if not ret.is_ok():
            # TODO: record the failure somehow pastly/flashflow#18
            log.error(
                'Failed to launch circuit to %s: %s',
                meas.relay_fp, ret)
            del self.measurements[meas.meas_id]
            return
        # We expect to see "250 LAUNCHED <circ_id>", e.g. "250 LAUNCHED 24".
        # Get the circuit id out and save it for later use.
        code, _, content = ret.content()[0]
        assert code == '250'
        parts = content.split()
        if len(parts) != 2 or parts[0] != 'LAUNCHED':
            # TODO: record the failure somehow pastly/flashflow#18
            log.error(
                'Did not expect body of message to be: %s', content)
            del self.measurements[meas.meas_id]
            return
        meas.relay_circ = int(parts[1])
        log.info(
            'Meas %d launched circ %d with relay %s',
            meas.meas_id, meas.relay_circ, meas.relay_fp)
        # That's all for now. We stay in this state until Tor tells us it has
        # finished building the circuit

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

    def notif_measr_msg(self, measr: MeasrProtocol, message: msg.FFMsg):
        ''' Receive a FFMsg object from one of our measurers '''
        msg_type = type(message)
        state = self.state
        if msg_type == msg.ConnectedToRelay and state == States.READY:
            assert isinstance(message, msg.ConnectedToRelay)  # so mypy knows
            return self._notif_measr_msg_ConnectedToRelay(measr, message)
        elif msg_type == msg.BwReport and state == States.READY:
            assert isinstance(message, msg.BwReport)  # so mypy knows
            return self._notif_measr_msg_BwReport(measr, message)
        self.change_state_nonfatal_error(
            'Unexpected %s message received in state %s' %
            (msg_type, state))

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

    def notif_measurer_disconnected(self, measurer: MeasrProtocol):
        ''' Called from MeasrProtocol when a connection with a measurer has
        been lost '''
        self.measurers = [m for m in self.measurers if m != measurer]
        log.debug('Measurer lost. Now have %d', len(self.measurers))
        # TODO: need to do error stuff if they were a part of any measurements

    def notif_ctrl_message(self, msg: str) -> Tuple[bool, str]:
        ''' Called from CtrlProtocol when a controller has given us a command.
        Returns (True, '') if the message seems like a good, actionable
        message.  Otherwise returns False and a human-meaningful string with
        more information.  '''
        words = msg.lower().split()
        if not len(words):
            return False, 'Empty command?'
        command = words[0]
        if command == 'measure':
            # TODO: pastly/flashflow#16 (able to start another while 1 going)
            if self.state != States.READY or len(self.measurements):
                return False, 'Not READY or already measuring'
            log.debug('told to measure %s', words[1])
            meas_id = next_meas_id()
            relay_fp = words[1]
            meas = Measurement(
                meas_id,
                relay_fp,
                {_ for _ in self.measurers},
                self.conf.getint('meas_params', 'meas_duration'),
                self.conf.getfloat('meas_params', 'bg_percent'))
            self.measurements[meas_id] = meas
            loop.call_soon(partial(self._connect_to_relay, meas))
            return True, ''
        return False, 'Unknown ctrl command: ' + msg

    def notif_circ_event(self, event: CircuitEvent):
        ''' Called from stem to tell us about CIRC events. We care about these
        when we are waiting on a circuit to be built with a relay.

        We are currently in a different thread. We tell the main thread's loop
        (in a threadsafe manner) to handle this event.
        '''
        loop.call_soon_threadsafe(partial(self._notif_circ_event, event))

    def _notif_circ_event(self, event: CircuitEvent):
        ''' The real CIRC event handler, in the main thread's loop.

        Receive a CIRC event from our tor client.

        We want to know about circuit events for the following reasons:
            - When we have recently launched our circuit with the relay and
              want to know when it is built so we can go to the next state
            - TODO failures
            - TOOD other reasons
        '''
        # Try to find a Measurement that is interested in this CIRC event based
        # on the circ_id
        matching_meas_ids = [
            meas_id for meas_id, meas in self.measurements.items()
            if meas.relay_circ == int(event.id)]
        # If none, then it's probably our tor client doing irrelevant things.
        # Ignore.
        if not len(matching_meas_ids):
            return
        # If more than one, serious programming issue. Drop.
        if len(matching_meas_ids) != 1:
            log.error(
                'It should not be possible for more than one Measurement to '
                'be using the same circuit id %d. Not handling CIRC event: %s',
                int(event.id), event)
            return
        # Found it. Pass off control based on the type of CIRC event
        meas_id = matching_meas_ids[0]
        meas = self.measurements[meas_id]
        if event.status == CircStatus.BUILT:
            return self._notif_circ_event_BUILT(meas, event)
        elif event.status in [CircStatus.LAUNCHED, CircStatus.EXTENDED]:
            # ignore these
            return
        elif event.status == CircStatus.CLOSED:
            return self._notif_circ_event_CLOSED(meas, event)
        elif event.status == CircStatus.FAILED:
            return self._notif_circ_event_FAILED(meas, event)
        log.warn('Not handling CIRC event: %s', event)

    def notif_ffmeas_event(self, event: FFMeasEvent):
        ''' Called from stem to tell us about FF_MEAS events.

        We are currently in a different thread. We tell the main thread's loop
        (in a threadsafe manner) to handle this event.
        '''
        loop.call_soon_threadsafe(partial(self._notif_ffmeas_event, event))

    def _notif_ffmeas_event(self, event: FFMeasEvent):
        ''' The real FF_MEAS event handler, in the main thread's loop.

        Receive a FF_MEAS event from our tor client.
        '''
        meas_id = event.meas_id
        if meas_id not in self.measurements:
            log.error(
                'Received FF_MEAS event with meas %d we don\'t know about: %s',
                meas_id, event)
            return
        meas = self.measurements[meas_id]
        if event.ffmeas_type == 'PARAMS_SENT':
            return self._notif_ffmeas_event_PARAMS_SENT(meas, event)
        elif event.ffmeas_type == 'PARAMS_OK':
            return self._notif_ffmeas_event_PARAMS_OK(meas, event)
        elif event.ffmeas_type == 'BW_REPORT':
            return self._notif_ffmeas_event_BW_REPORT(meas, event)
        log.warn(
            'Ignoring FF_MEAS event because unrecognized/unhandled type '
            '%s: %s', event.ffmeas_type, event)
        return


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
