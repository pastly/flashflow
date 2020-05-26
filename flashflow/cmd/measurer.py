from argparse import ArgumentParser
import enum
import logging
import socket
import ssl
import os
from .. import tor_client
from .. import msg
from typing import Tuple, Union


log = logging.getLogger(__name__)


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow measurer.'
    p = sub.add_parser('measurer', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    os.makedirs(conf.getpath('measurer', 'datadir'), mode=0o700, exist_ok=True)
    os.makedirs(conf.getpath('measurer', 'keydir'), mode=0o700, exist_ok=True)
    coord_addr_port = conf.getaddr('measurer', 'coord_addr')
    if coord_addr_port is None:
        log.error('Don\'t know where coord is')
        return
    c = tor_client.launch(
        conf.getpath('tor', 'tor_bin'),
        conf.getpath('measurer', 'tor_datadir'),
        conf.get('tor', 'torrc_extra_lines')
    )
    if not c:
        return
    # Open connection with coord
    ret_code, sock_or_err = _get_socket_with_coord(
        coord_addr_port,
        conf.getpath('measurer', 'key'),
        conf.getpath('measurer', 'coord_cert'),
    )
    if ret_code != CoordConnRes.SUCCESS:
        log.warn('Unable to connect to coord: %s', sock_or_err)
        return
    assert not isinstance(sock_or_err, str)
    sock = sock_or_err
    # m = msg.Bar('asdf')
    # s = m.serialize()
    # sock.write(s)
    # m = msg.Foo('1')
    # s = m.serialize()
    # sock.write(s)


class CoordConnRes(enum.Enum):
    ''' Part of the return value of _get_socket_with_coord(...).

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


def _get_socket_with_coord(
        addr_port: Tuple[str, int],
        our_key: str,
        coord_cert: str,
        ) -> Tuple[CoordConnRes, Union[str, ssl.SSLSocket]]:
    ''' Try to connect to the coordinator at the given (host, port) tuple.
    Perform the TLS handshake using our client TLS key in the file `our_key`
    and only trusting the coord server cert in the file `coord_cert`.

    We return a tuple. The first item indicates whether or not we were
    successful. If we were successful, then the second item is the TLS-wrapped
    socket. Otherwise we were not successful. The first item further indicates
    whether we should try gain, and the second is a string with more
    information about what went wrong. '''
    if not os.path.isfile(our_key):
        return CoordConnRes.FATAL_ERROR, our_key + ' does not exist'
    if not os.path.isfile(coord_cert):
        return CoordConnRes.FATAL_ERROR, coord_cert + ' does not exist'
    # Look up the IP(s) of the given address. It might be more than one.
    addr_infos = socket.getaddrinfo(
        addr_port[0], addr_port[1],
        type=socket.SOCK_STREAM)
    if not addr_infos:
        return CoordConnRes.RETRY_ERROR,\
            'Could not getaddrinfo() with %s' % (addr_port,)
    # Arbitrarily choose the first info returned. We *could*, for example,
    # favor ipv6, but nah let's put that off for now.
    assert len(addr_infos) > 0
    family, type, _, _, sock_addr = addr_infos[0]
    sock = ssl.wrap_socket(
        socket.socket(family, type),
        server_side=False,
        cert_reqs=ssl.CERT_REQUIRED,
        certfile=our_key,
        ca_certs=coord_cert,
    )
    # Try connecting now.
    try:
        sock.connect((sock_addr[0], sock_addr[1]))
    except ConnectionRefusedError:
        return CoordConnRes.RETRY_ERROR,\
            'Connection refused. Is the coordinator running at %s:%d?' %\
            (sock_addr[0], sock_addr[1])
    except ssl.SSLError as e:
        return CoordConnRes.RETRY_ERROR,\
            'Does the coord trust our cert, and do we have the right cert ' +\
            'for the coord? ' + str(e)
    log.info(
        'Connected to coordinator at %s:%d', sock_addr[0], sock_addr[1])
    return CoordConnRes.SUCCESS, sock
