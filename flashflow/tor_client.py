from stem.control import Controller
from stem.connection import IncorrectSocketType
from stem.process import launch_tor_with_config
from stem import SocketError, ProtocolError
import copy
import os
import logging
from typing import Optional, List, Union, Dict


log = logging.getLogger(__name__)


# A dictionary of torrc options we need  before launching tor and that do not
# depend on runtime configuration. Options only known at runtime (e.g. the
# DataDirectory) are added in launch(...)
TORRC_BASE: Dict[str, Union[str, List]] = {
    # SocksPort not needed
    'SocksPort': '0',
    # Easier than any other type of auth
    'CookieAuthentication': '1',
    # Unecessary, and avoids path bias warnings
    'UseEntryGuards': '0',
    # To make logs more useful
    'SafeLogging': '0',
    'LogTimeGranularity': '1',
}


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


def _connect(loc: str):
    ''' Connect to a Tor control port or control socket a the given location.
    If the given location looks like an integer, treat it as a port number and
    (assumes localhost). If it doesn't look like an integer, treat it as path
    to a socket. Returns None if unable to connect for any reason. Tor must not
    require password authentication (i.e. cookie authentication or no
    authentication). '''
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


def launch(tor_bin: str, tor_datadir: str) -> Optional[Controller]:
    ''' Launch and connect to Tor using the given tor binary (or path to tor
    binary) and using the given Tor DataDirectory. Returns an authenticated
    stem Controller object when successful. If any error occurs, this module
    logs about it and returns from here. '''
    opj = os.path.join
    os.makedirs(tor_datadir, mode=0o700, exist_ok=True)
    # Get a copy of the starting torrc without any dynamic options
    torrc = copy.deepcopy(TORRC_BASE)
    # Save a copy of this as it will be used a few times in this function
    sock_path = os.path.abspath(opj(tor_datadir, 'control'))
    # Update the torrc with everything that depends on runtime config
    torrc.update({
        'DataDirectory': tor_datadir,
        'PidFile': opj(tor_datadir, 'tor.pid'),
        'ControlSocket': sock_path,
        'Log': ['NOTICE file ' + opj(tor_datadir, 'notice.log')],
    })
    # Blocks while launching Tor
    launch_tor_with_config(
        torrc, tor_cmd=tor_bin, init_msg_handler=log.debug,
        take_ownership=True)
    c = _connect(sock_path)
    if c is None:
        log.error('Unable to connect to Tor')
        return None
    assert isinstance(c, Controller)
    log.info(
        'Started and connected to Tor %s via %s',
        c.get_version(),
        sock_path)
    return c
