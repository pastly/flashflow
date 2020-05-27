from argparse import ArgumentParser
from functools import partial
from traceback import StackSummary
import asyncio
import enum
import logging
import ssl
import os
from stem.control import Controller  # type: ignore
from transitions import Machine  # type: ignore
from .. import tor_client
from .. import msg
from typing import Tuple, Union


class CoordProtocol(asyncio.Protocol):
    transport = None

    def connection_made(self, transport):
        log.debug('Connected to coord')
        self.transport = transport

    def connection_lost(self, exc):
        log.debug('Lost connection with coord')
        machine.nonfatal_error()
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
    # There was some sort of error that calls for cleaning everything up and
    # essentially relaunching, but we shouldn't outright die.
    NONFATAL_ERROR = enum.auto()
    # There is a serious error that isn't recoverable. Just cleanup and die.
    FATAL_ERROR = enum.auto()


class StateMachine(Machine):
    ''' State machine and main control flow hub for FlashFlow measurer '''
    # conf  # This is set in __init__
    tor_client: Controller
    coord_trans: asyncio.BaseTransport
    coord_proto: CoordProtocol

    def __init__(self, conf):
        self.conf = conf
        super().__init__(
            model=self,
            states=States,
            transitions=[
                {
                    'trigger': 'starting',
                    'source': [States.START, States.NONFATAL_ERROR],
                    'dest': States.ENSURE_CONN_W_TOR,
                },
                {
                    'trigger': 'connected_to_tor',
                    'source': States.ENSURE_CONN_W_TOR,
                    'dest': States.ENSURE_CONN_W_COORD,
                },
                {
                    'trigger': 'connected_to_coord',
                    'source': States.ENSURE_CONN_W_COORD,
                    'dest': States.READY,
                },
                {
                    'trigger': 'nonfatal_error',
                    'source': '*',
                    'dest': States.NONFATAL_ERROR,
                },
                {
                    'trigger': 'fatal_error',
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
            self.fatal_error()
            return
        self.tor_client = c
        self.connected_to_tor()

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
            self.fatal_error()
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
                self.fatal_error()
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
            self.connected_to_coord()
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

    def _cleanup(self):
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

    # ########################################################################
    # STATE CHANGE EVENTS. These are called when entering the specified state.
    # ########################################################################

    def on_enter_ENSURE_CONN_W_TOR(self):
        loop.call_soon(self._ensure_conn_w_tor)

    def on_enter_ENSURE_CONN_W_COORD(self):
        loop.call_soon(partial(self._ensure_conn_w_coord, 0.5))

    def on_enter_NONFATAL_ERROR(self):
        self._cleanup()
        loop.call_soon(self.starting)

    def on_enter_FATAL_ERROR(self):
        # log.error('We encountered a fatal error :(')
        self._cleanup()
        self._die()

    # ########################################################################
    # MESSAGES FROM COORD. These are called when the coordinator tells us
    # something.
    # ########################################################################

    def recv_coord_msg(self, message: msg.FFMsg):
        msg_type = type(message)
        state = self.state
        if msg_type == msg.ConnectToRelay and state == States.READY:
            assert isinstance(message, msg.ConnectToRelay)
            self._recv_msg_connect_to_relay(message)
        else:
            log.warn(
                'Unexpected %s message received in state %s',
                msg_type, state)
            self.nonfatal_error()

    def _recv_msg_connect_to_relay(self, message: msg.ConnectToRelay):
        assert self.state == States.READY
        log.info('Got msg to connect to relay %s', message.fp)
        self.coord_trans.write(msg.ConnectedToRelay(True, message).serialize())


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
    machine.fatal_error()


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
    loop.call_soon(machine.starting)
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    return
