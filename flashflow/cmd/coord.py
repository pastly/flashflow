from argparse import ArgumentParser
import asyncio
import enum
import glob
import logging
import os
import selectors
import ssl
import socket
from tempfile import NamedTemporaryFile
from stem.control import Controller
from typing import Optional, Tuple, List, IO
from .. import tor_client
from ..tor_ctrl_msg import CoordStartMeas
from .. import msg
from transitions import Machine


class MeasrProtocol(asyncio.Protocol):
    transport = None

    def connection_made(self, transport):
        # TODO: log host:port of measurer
        log.debug('Connection from measurer')
        self.transport = transport
        self.transport.write(msg.ConnectToRelay('relay1').serialize())

    def connection_lost(self, exc):
        log.debug('Lost connection with measurer')
        # TODO: what do about this

    def data_received(self, data: bytes):
        log.info('Received %d bytes: %s', len(data), data)


class States(enum.Enum):
    ''' States that we, as a FlashFlow coordinator, can be in '''
    START = enum.auto()
    # First "real" state. Open all listening sockets
    ENSURE_LISTEN_SOCKS = enum.auto()
    # Next real state. Launch a tor client and connect to it
    ENSURE_CONN_W_TOR = enum.auto()
    # We're idle and ready to start doing stuff
    READY = enum.auto()
    # There was some sort of error that calls for cleaning everything up and
    # essentially relaunching, but we shouldn't outright die.
    NONFATAL_ERROR = enum.auto()
    # There is a serious error that isn't recoverable. Just cleanup and die.
    FATAL_ERROR = enum.auto()


class StateMachine(Machine):
    ''' State machine and main control flow hub for FlashFlow coordinator '''
    # conf  # This is set in __init__
    server: asyncio.base_events.Server
    tor_client: Controller

    def __init__(self, conf):
        self.conf = conf
        super().__init__(
            model=self,
            states=States,
            transitions=[
                {
                    'trigger': 'starting',
                    'source': [States.START, States.NONFATAL_ERROR],
                    'dest': States.ENSURE_LISTEN_SOCKS,
                },
                {
                    'trigger': 'listening',
                    'source': States.ENSURE_LISTEN_SOCKS,
                    'dest': States.ENSURE_CONN_W_TOR,
                },
                {
                    'trigger': 'connected_to_tor',
                    'source': States.ENSURE_CONN_W_TOR,
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

    def _ensure_listen_socks(self):
        ''' Main function in the ENSURE_LISTEN_SOCKS state. Open listening
        sockets '''
        # Get (host, port) from "host:port"
        addr_port = self.conf.getaddr('coord', 'listen_addr')
        if addr_port is None:
            log.error('Don\'t know what to listen on')
            self.fatal_error()
            return
        # Make sure TLS key material exists
        our_key = self.conf.getpath('coord', 'key')
        keydir = self.conf.getpath('coord', 'keydir')
        if not os.path.isfile(our_key):
            log.error('%s does not exist', our_key)
            self.fatal_error()
            return
        if not os.path.isdir(keydir):
            log.error('%s does not exist', keydir)
            self.fatal_error()
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
                self.fatal_error()
                return
            self.server = fut.result()
            for s in self.server.sockets:
                log.info('Listening on %s for measurers', s.getsockname())
            self.listening()
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
            self.fatal_error()
            return
        self.tor_client = c
        self.connected_to_tor()

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

    def on_enter_NONFATAL_ERROR(self):
        self._cleanup()
        loop.call_soon(self.starting)

    def on_enter_FATAL_ERROR(self):
        self._cleanup()
        self._die()

    # ########################################################################
    # MISC EVENTS. These are called from other parts of the measurer code.
    # ########################################################################

    def notif_sslerror(self, exc: ssl.SSLError, trans):
        log.debug(
            'Someone (%s) failed to TLS handshake with us: %s',
            trans.get_extra_info('peername'), exc)
        trans.close()


log = logging.getLogger(__name__)
loop = asyncio.get_event_loop()
machine: StateMachine


def _exception_handler(loop, context):
    ''' Last resort exception handler.

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
                    'SSLError caught without a transport too. Cannot pass '+
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
    machine.fatal_error()


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
    loop.call_soon(machine.starting)
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    return
