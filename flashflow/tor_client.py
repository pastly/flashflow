from stem.control import Controller
from stem.connection import IncorrectSocketType
from stem import SocketError, ProtocolError
import logging


log = logging.getLogger(__name__)


def _connect_to_socket(loc: str):
    try:
        return Controller.from_socket_file(path=loc)
    except (IncorrectSocketType, SocketError) as e:
        log.error('Error connecting to Tor control socket %s: %s', loc, e)
        return None


def _connect_to_port(port: int):
    try:
        return Controller.from_port(port=port)
    except (IncorrectSocketType, SocketError) as e:
        log.error('Error connecting to Tor control port %d: %s', port, e)
        return None


def connect(loc: str):
    ''' Connect to a Tor control port or control socket a the given location.
    If the given location looks like an integer, treat it as a port number and
    (assumes localhost). If it doesn't look like an integer, treat it as path
    to a socket. Returns None if unable to connect for any reason. Tor must not
    require password authentication (i.e. cookie authentication either no
    authentication) '''
    try:
        port = int(loc)
    except ValueError:
        c = _connect_to_socket(loc)
    else:
        c = _connect_to_port(port)
    if c is None:
        return None
    try:
        c.authenticate()
    except (IncorrectSocketType, ProtocolError) as e:
        log.error('Error authenticating to Tor on %s: %s', loc, e)
        return None
    log.info('Connected to Tor at %s', loc)
    return c
