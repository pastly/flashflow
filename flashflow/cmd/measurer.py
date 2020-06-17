from argparse import ArgumentParser
from functools import partial
from traceback import StackSummary
import asyncio
import enum
import logging
import ssl
import os
from stem import CircStatus, InvalidArguments  # type: ignore
from stem.control import Controller, EventType  # type: ignore
from stem.response.events import CircuitEvent, FFMeasEvent  # type: ignore
from transitions import Machine  # type: ignore
from typing import Tuple, Union, Set
from .. import tor_client
from .. import msg
from ..tor_ctrl_msg import MeasrStartMeas


class CoordProtocol(asyncio.Protocol):
    transport = None

    def connection_made(self, transport):
        log.debug('Connected to coord')
        self.transport = transport

    def connection_lost(self, exc):
        machine.change_state_nonfatal_error('Lost connection with coord')
        pass

    def data_received(self, data: bytes):
        ''' Receive data from the coordinator. Parse it into a FFMsg and tell
        other code about the message.

        It's possible that this is called before the entire message is
        received. In that case, we'll need to edit this function to buffer
        bytes until the entire message has arrived.  '''
        log.info('Received %d bytes: %s', len(data), data)
        m = msg.FFMsg.deserialize(data)
        machine.recv_coord_msg(m)


class States(enum.Enum):
    ''' States that we, as a FlashFlow measurer, can be in. '''
    # State we start in. Only ever in this state when first launching
    START = enum.auto()
    # First "real" state. Launch a tor client and connect to it
    ENSURE_CONN_W_TOR = enum.auto()
    # Next real state. Connect to the coordinator
    ENSURE_CONN_W_COORD = enum.auto()
    # We're idle and ready to be told what to do
    READY = enum.auto()
    # Got command from coordinator to connect to a relay. Doing it right now
    CREATE_CONN_W_RELAY = enum.auto()
    # We're connected to the relay and ready to go. Just waiting for the start
    # command from the coordinator
    WAITING_TO_START = enum.auto()
    # We've been told go. It's time to start measuring
    MEASUREMENT = enum.auto()
    # There was some sort of error that calls for cleaning everything up and
    # essentially relaunching, but we shouldn't outright die.
    NONFATAL_ERROR = enum.auto()
    # There is a serious error that isn't recoverable. Just cleanup and die.
    FATAL_ERROR = enum.auto()


class StateMachine(Machine):
    ''' State machine and main control flow hub for FlashFlow measurer.

    change_state_*:
        State transitions are named change_state_* and don't exist here in the
        code. The Machine class takes care of making them based on the triggers
        in the list of possible transitions. For example: change_state_starting
        is named as the trigger for transitions from either START or
        NONFATAL_ERROR into ENSURE_CONN_W_TOR.

    on_enter_*:
        This is how the Machine class finds functions to call upon entering the
        given state. For example, on_enter_NONFATAL_ERROR() is called when we
        are transitioning to the NONFATAL_ERROR state. These functions should
        be kept short. Significant work/logic should be done in other functions
        that these call or schedule for calling later.

    _*:
        Other internal functions. See their documentation for more information
        on them.
    '''
    # conf  # This is set in __init__
    tor_client: Controller
    # how we communicate with the coord
    coord_trans: asyncio.WriteTransport
    coord_proto: CoordProtocol
    # keep track of which circs we've launched, and which of the launched circs
    # have been built so far
    launched_circs: Set[int]
    built_circs: Set[int]
    # keep a copy of ConnectToRelay command so we can send it back to the coord
    # when we're ready to go (or have failed)
    connect_msg: msg.ConnectToRelay

    def __init__(self, conf):
        self.conf = conf
        self.launched_circs = set()
        self.built_circs = set()
        super().__init__(
            model=self,
            states=States,
            transitions=[
                {
                    'trigger': 'change_state_starting',
                    'source': [States.START, States.NONFATAL_ERROR],
                    'dest': States.ENSURE_CONN_W_TOR,
                },
                {
                    'trigger': 'change_state_connected_to_tor',
                    'source': States.ENSURE_CONN_W_TOR,
                    'dest': States.ENSURE_CONN_W_COORD,
                },
                {
                    'trigger': 'change_state_connected_to_coord',
                    'source': States.ENSURE_CONN_W_COORD,
                    'dest': States.READY,
                },
                {
                    'trigger': 'change_state_recv_cmd_connect',
                    'source': States.READY,
                    'dest': States.CREATE_CONN_W_RELAY,
                },
                {
                    'trigger': 'change_state_connected_to_relay',
                    'source': States.CREATE_CONN_W_RELAY,
                    'dest': States.WAITING_TO_START,
                },
                {
                    'trigger': 'change_state_told_go',
                    'source': States.WAITING_TO_START,
                    'dest': States.MEASUREMENT,
                },
                {
                    'trigger': 'change_state_finished',
                    'source': States.MEASUREMENT,
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

    def _ensure_conn_w_tor(self):
        ''' Main function in the ENSURE_CONN_W_TOR state. Launch a tor client
        and connect to it. Save the Controller object. '''
        assert self.state == States.ENSURE_CONN_W_TOR
        # TODO: what happens if tor client disappears? Exception thrown? What??
        # And what should we do about it? Try to relaunch? Just die? Choose
        # **something**
        c = tor_client.launch(
            self.conf.getpath('tor', 'tor_bin'),
            self.conf.getpath('measurer', 'tor_datadir'),
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

    def _ensure_conn_w_coord(self, delay: float):
        ''' Main function in the ENSURE_CONN_W_COORD state. Repeatedly try
        connecting to the coordinator until we are successful or have a fatal
        error warranting completely giving up on life.

        This function uses asynchronous python: the connection is represented
        by a transport and protocol, and we try connecting asynchronously and
        use a callback to find out the result. That said, the work done here
        should probably be the only thing going on.
        '''
        assert self.state == States.ENSURE_CONN_W_COORD
        # TODO: what if connection goes away?
        # Get the (host, port) from "host:port"
        coord_addr_port = self.conf.getaddr('measurer', 'coord_addr')
        if coord_addr_port is None:
            log.error('Don\'t know where coord is')
            self.change_state_fatal_error()
            return

        # Callback to get the result of one connection attempt. If it didn't
        # work and it wasn't fatal, schedule calling this function again some
        # time in the future. If fatal, die. If successful, save the transport
        # and protocol and move on!
        def cb(fut):
            nonlocal delay
            # It's possible that the programmer didn't catch all exceptions.
            # If the result is an exception, this *should* bubble up to the
            # default exception handler, _exception_handler(...).
            success_code, stuff_or_error = fut.result()
            # Now check if we were successful, fatally unable to connect, or if
            # we should retry.
            if success_code == CoordConnRes.FATAL_ERROR:
                log.error(
                    'Fatal error connecting to coordinator: %s',
                    stuff_or_error)
                self.change_state_fatal_error()
                return
            elif success_code == CoordConnRes.RETRY_ERROR:
                delay = min(2 * delay, 60)
                log.warn(
                    'Unable to connect to coordinator: %s. Retrying in %.2fs.',
                    stuff_or_error, delay)
                loop.call_later(
                    delay, partial(self._ensure_conn_w_coord, delay))
                return
            assert success_code == CoordConnRes.SUCCESS
            assert not isinstance(stuff_or_error, str)
            self.coord_trans, self.coord_proto = stuff_or_error
            self.change_state_connected_to_coord()
        # Kick off the asyncronous attempt to connect and attach the above
        # callback so we can get the result.
        task = asyncio.Task(_try_connect_to_coord(
            coord_addr_port,
            self.conf.getpath('measurer', 'key'),
            self.conf.getpath('measurer', 'coord_cert'),
        ))
        task.add_done_callback(cb)
        # This is asynchronous python. We end immediately and the callback will
        # eventually be called with the connection results. Nothing left to do
        # for now.

    def _single_meas_cleanup(self):
        ''' Cleanup all state associated with a specific measurement. Do not
        disconnect from anybody. Use this when a measurement has ended
        successfully. '''
        self.launched_circs = set()
        for circ_id in self.built_circs:
            try:
                self.tor_client.close_circuit(circ_id)
            except InvalidArguments:
                # We'll get this if the circuit has already been closed. It's
                # fine. It could have been the tor client closing the circuit
                # itself.
                pass
            except Exception as e:
                log.warn(
                    'Exception trying to close circ %d: %s %s', circ_id,
                    type(e), e)
        self.built_circs = set()
        self.connect_msg = None

    def _complete_cleanup(self):
        ''' Cleanup all of our state while being very careful to not allow any
        exceptions to bubble up. Use this when in an error state and you want
        to cleanup before starting over or just dying. '''
        if hasattr(self, 'tor_client') and self.tor_client:
            log.info('cleanup: closing tor')
            try:
                self.tor_client.close()
            except Exception as e:
                log.error('Error closing tor: %s', e)
        if hasattr(self, 'coord_trans') and self.coord_trans:
            log.info('cleanup: closing coord transport')
            try:
                self.coord_trans.close()
            except Exception as e:
                log.error('Error closing transport with coord: %s', e)
        if hasattr(self, 'coord_proto') and self.coord_proto:
            # nothing to do
            pass

    def _die(self):
        ''' End execution of the program. '''
        loop.stop()

    def _create_conn_w_relay(self, message: msg.ConnectToRelay):
        ''' Main function for the CREATE_CONN_W_TOR state '''
        ret = tor_client.send_msg(
            self.tor_client,
            MeasrStartMeas(message.fp, message.n_circs, message.dur))
        # Make sure the circuit launches went well. Note they aren't built yet.
        # It's just that tor found nothing obviously wrong with trying to build
        # these circuits.
        if not ret.is_ok():
            self.change_state_nonfatal_error(
                'Failed to start %d circuits to %s: %s' %
                (message.n_circs, message.fp, ret))
            return
        # We expect to see "250 FF_MEAS 0 LAUNCHED CIRCS=1,2,3,4,5", where the
        # 0 is just a placeholder and the actual list of launched circuits is
        # CIRCS the comma-separated list
        code, _, content = ret.content()[0]
        parts = content.split()
        if code != '250':
            log.error('Got non-success code %s: %s', code, content)
            self.change_state_nonfatal_error(
                'Malformed response from tor: %s' % (ret,))
            return
        if len(parts) != 4 or \
                not content.startswith('FF_MEAS 0 LAUNCHED CIRCS='):
            log.error('Did not expect body of message to be: %s', content)
            self.change_state_nonfatal_error(
                'Malformed response from tor: %s' % (ret,))
            return
        for circ_id_str in parts[3].split('=')[1].split(','):
            self.launched_circs.add(int(circ_id_str))
        log.info(
            'Launched %d circuits with the relay: %s',
            len(self.launched_circs), self.launched_circs)
        self.connect_msg = message
        # That's all for now. We stay in this state until Tor tells us it has
        # finished building all circuits

    def _tell_coord_ready_to_start(self, success: bool):
        ''' Main function for the WAITING_TO_START state '''
        m = msg.ConnectedToRelay(success, self.connect_msg)
        self.coord_trans.write(m.serialize())

    def _start_measuring(self):
        ''' Main function for the MEASUREMENT state '''
        m = MeasrStartMeas(
            self.connect_msg.fp, len(self.built_circs), self.connect_msg.dur)
        ret = tor_client.send_msg(self.tor_client, m)
        if not ret.is_ok():
            self.change_state_nonfatal_error(
                'Unable to tell tor to start active measurement: %s', ret)
            return

    def _tell_all_failure(self, f: msg.Failure):
        # TODO: tell tor client too
        log.error(f.desc)
        try:
            self.coord_trans.write(f.serialize())
        except Exception as e:
            log.error('Tried sending Failure to coord, but: %s', e)
            pass
        return

    # ########################################################################
    # STATE CHANGE EVENTS. These are called when entering the specified state.
    # ########################################################################

    def on_enter_READY(self):
        loop.call_soon(self._single_meas_cleanup)

    def on_enter_ENSURE_CONN_W_TOR(self):
        loop.call_soon(self._ensure_conn_w_tor)

    def on_enter_ENSURE_CONN_W_COORD(self):
        loop.call_soon(partial(self._ensure_conn_w_coord, 0.5))

    def on_enter_NONFATAL_ERROR(self, err_msg: str):
        loop.call_soon(self._tell_all_failure, msg.Failure(err_msg))
        loop.call_soon(self._complete_cleanup)
        loop.call_soon(self.change_state_starting)

    def on_enter_FATAL_ERROR(self):
        # log.error('We encountered a fatal error :(')
        self._complete_cleanup()
        self._die()

    def on_enter_CREATE_CONN_W_RELAY(self, message: msg.ConnectToRelay):
        loop.call_soon(partial(self._create_conn_w_relay, message))

    def on_enter_WAITING_TO_START(self, success: bool):
        loop.call_soon(partial(self._tell_coord_ready_to_start, success))

    def on_enter_MEASUREMENT(self):
        loop.call_soon(self._start_measuring)

    # ########################################################################
    # MESSAGES FROM COORD. These are called when the coordinator tells us
    # something.
    # ########################################################################

    def recv_coord_msg(self, message: msg.FFMsg):
        msg_type = type(message)
        state = self.state
        # The asserts below are for shutting up mypy
        if msg_type == msg.ConnectToRelay and state == States.READY:
            assert isinstance(message, msg.ConnectToRelay)
            self._recv_msg_connect_to_relay(message)
        elif msg_type == msg.Failure:
            assert isinstance(message, msg.Failure)
            self._recv_msg_failure(message)
        elif msg_type == msg.Go:
            assert isinstance(message, msg.Go)
            self._recv_msg_go(message)
        else:
            self.change_state_nonfatal_error(
                'Unexpected %s message received in state %s' %
                (msg_type, state))

    def _recv_msg_connect_to_relay(self, message: msg.ConnectToRelay):
        assert self.state == States.READY
        self.change_state_recv_cmd_connect(message)

    def _recv_msg_failure(self, message: msg.Failure):
        self.change_state_nonfatal_error(
            'Coord-induced failure: ' + message.desc)
        self._die()

    def _recv_msg_go(self, message: msg.Go):
        self.change_state_told_go()

    # ########################################################################
    # MISC EVENTS. These are called from other parts of the measr code.
    # ########################################################################

    def notif_ffmeas_event(self, event: FFMeasEvent):
        ''' Called from stem to tell us about FF_MEAS events.

        These events come from a different thread. We tell the main thread's
        loop (in a threadsafe manner) to handle this event in the similarly
        named function with a leading underscore.
        '''
        loop.call_soon_threadsafe(partial(self._notif_ffmeas_event, event))

    def _notif_ffmeas_event(self, event: FFMeasEvent):
        ''' Actually handle the FF_MEAS event.

        We look for:
        - per-second BW_REPORTs of the amount of measurement traffic sent and
        received, and we will fowarded those on to the coordinator.
        - a END message at the end signally success.
        '''
        if event.ffmeas_type == 'BW_REPORT':
            log.debug(
                'Forwarding report of %d/%d sent/recv meas bytes',
                event.sent, event.recv)
            report = msg.BwReport(event.sent, event.recv)
            self.coord_trans.write(report.serialize())
            return
        elif event.ffmeas_type == 'END':
            if event.success:
                self.change_state_finished()
            else:
                self.change_state_nonfatal_error(
                    'Got non-successful END message from tor client')
            return
        self.change_state_nonfatal_error(
            'Unexpected FF_MEAS event type %s' % (event.ffmeas_type,))
        return

    def notif_circ_event(self, event: CircuitEvent):
        ''' Called from stem to tell us about circuit events.

        These events come from a different thread. We tell the main thread's
        loop (in a threadsafe manner) to handle this event in the similarly
        named function with a leading underscore.
        '''
        loop.call_soon_threadsafe(partial(self._notif_circ_event, event))

    def _notif_circ_event(self, event: CircuitEvent):
        ''' Actually handle the circuit event. We usually don't care, but
        sometimes we are waiting on circuits to be built with a relay.

        This runs in the main thread's loop unlike the similarly named function
        (without a leading underscore) that tells the loop to call us.
        '''
        circ_id = int(event.id)
        # We don't care about anything if we're just idle waiting
        if self.state == States.READY:
            return
        # Make sure it's a circuit we care about
        if circ_id not in self.launched_circs:
            # log.warn(
            #     'Ignoring CIRC event not for us. %d not in our list of ' +
            #     'launched circs', circ_id)
            return
        # It's for us, and the circuit has just been built. It we're in the
        # right state for this, continue on to the next state. Otherwise this
        # was unexpected and should error out.
        if event.status == CircStatus.BUILT:
            if self.state == States.CREATE_CONN_W_RELAY:
                assert circ_id in self.launched_circs
                self.built_circs.add(circ_id)
                log.debug(
                    'Added circ %d to set of built circs. Now have %d/%d',
                    circ_id, len(self.built_circs), len(self.launched_circs))
                if len(self.built_circs) == len(self.launched_circs):
                    log.info('Bult all circuits')
                    self.change_state_connected_to_relay(True)
            else:
                self.change_state_nonfatal_error(
                    'Found out circ %d is done building, but that '
                    'shouldn\'t happen in state %s' %
                    (circ_id, self.state))
            return
        # It's for us, and the circuit is still getting built. Don't care.
        # Ignore.
        elif event.status in [CircStatus.LAUNCHED, CircStatus.EXTENDED]:
            # ignore these
            return
        # It's for us, and the circuit has been closed. TODO this might be fine
        # in some case?
        elif event.status == CircStatus.CLOSED:
            log.warn('circ %d with relay closed', circ_id)
            self.change_state_nonfatal_error(
                'Circ %d closed unexpectedly' % (circ_id,))
            return
        elif event.status == CircStatus.FAILED:
            log.error('circ %d entered failed state: %s', circ_id, event)
            self.change_state_nonfatal_error(
                'Cird %d failed' % (circ_id,))
            return
        # It's for us, but don't know how to handle it yet
        log.warn('Not handling CIRC event for us: %s', event)


class CoordConnRes(enum.Enum):
    ''' Part of the return value of _try_connect_to_coord(...).

    SUCCESS: We successfully connected to the coord, shook our TLS hands, and
    all is well.

    RETRY_ERROR: We were not successful, but whatever happened may be temporary
    and it's logical to try connecting again in the future.

    FATAL_ERROR: We were not successful, and trying again in the future is
    extremely likely to not be successful. You should give up.
    '''
    SUCCESS = enum.auto()
    RETRY_ERROR = enum.auto()
    FATAL_ERROR = enum.auto()


async def _try_connect_to_coord(
    addr_port: Tuple[str, int],
    our_key: str,
    coord_cert: str,
) -> Tuple[
    CoordConnRes, Union[
        str, Tuple[asyncio.BaseTransport, asyncio.BaseProtocol]]]:
    ''' Try to connect to the coordinator at the given (host, port) tuple.
    Perform the TLS handshake using our client TLS key in the file `our_key`
    and only trusting the coord server cert in the file `coord_cert`.

    Returns a tuple in all cases. The first item indicates success with
    CoordConnRes. If it is an *_ERROR, then the second item is a string with
    more details. If it is SUCCESS, then the second item is the transport and
    protocol with the coordinator.

    This function is a coroutine and all exceptions **should** be handled
    within this function's body. If they aren't, that's a programming error.
    To handle the case of unhandled exceptions, wrap this function in a
    Task/Future, then catch and handle the generic Exception.

        def cb(fut):
            # handle the completion of the Task, whether successful or not
            pass
        task = asyncio.Task(_try_connect_to_coord(...))
        task.add_done_callback(cb)
        try:
            result = task.result()
        except Exception as e:
            log.error(
                'An unhandled exception occurred. Tell your programmer: %s', e)
            # Additional code to handle the error, as necessary
    '''
    if not os.path.isfile(our_key):
        return CoordConnRes.FATAL_ERROR, our_key + ' does not exist'
    if not os.path.isfile(coord_cert):
        return CoordConnRes.FATAL_ERROR, coord_cert + ' does not exist'
    ssl_context = ssl.SSLContext()
    # Load our TLS private key and certificate
    ssl_context.load_cert_chain(our_key)
    # Load the certificate of the coord
    ssl_context.load_verify_locations(coord_cert)
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    try:
        res = await loop.create_connection(
            CoordProtocol,
            addr_port[0],
            addr_port[1],
            ssl=ssl_context,
        )
    except OSError as e:
        return CoordConnRes.RETRY_ERROR, str(e)
    return CoordConnRes.SUCCESS, res


def _exception_handler(loop, context):
    log.error('%s', context['message'])
    if 'exception' in context:
        log.error(context['exception'])
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
        log.error('Traceback not available. Run with PYTHONASYNCIODEBUG=1')
    machine.change_state_fatal_error()


# # Not sure if this would actually work here. Maybe add to the logging config
# # file?
# # https://docs.python.org/3.6/library/asyncio-dev.html#logging
# logging.getLogger('asyncio').setLevel(logging.WARNING)
log = logging.getLogger(__name__)
loop = asyncio.get_event_loop()
machine: StateMachine


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow measurer.'
    p = sub.add_parser('measurer', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    global machine
    os.makedirs(conf.getpath('measurer', 'datadir'), mode=0o700, exist_ok=True)
    os.makedirs(conf.getpath('measurer', 'keydir'), mode=0o700, exist_ok=True)
    machine = StateMachine(conf)
    loop.set_exception_handler(_exception_handler)
    loop.call_soon(machine.change_state_starting)
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    return
