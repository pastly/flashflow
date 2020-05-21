from argparse import ArgumentParser
import selectors
import socket
import logging
from .. import tor_client
# from ..tor_ctrl_msg import CoordStartMeas
from typing import Optional, Tuple, List


log = logging.getLogger(__name__)


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow coordinator.'
    p = sub.add_parser('coord', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    addr_port = conf.getaddr('coord', 'listen_addr')
    if addr_port is None:
        log.error('Don\'t know what to listen on')
        return
    listen_socks = _get_listen_sockets(addr_port)
    if not listen_socks:
        log.error('Unable to bind/listen on %s', addr_port)
        return
    c = tor_client.launch(
        conf.getpath('tor', 'tor_bin'),
        conf.getpath('coord', 'tor_datadir'),
        conf.get('tor', 'torrc_extra_lines')
    )
    if not c:
        return
    sel = selectors.DefaultSelector()

    def _accept_cb(sock, mask) -> None:
        conn, addr = sock.accept()  # Should be ready
        log.info('Accepted connection from %s', addr)
        conn.setblocking(False)
        sel.register(conn, selectors.EVENT_READ, _read_cb)

    def _read_cb(conn, mask) -> None:
        data = conn.recv(1024)
        log.debug('Got %s', data)
        if not data:
            log.info(
                'Empty read. Closing connection with %s',
                conn.getpeername()[0:2])
            sel.unregister(conn)
            conn.close()

    for listen_sock in listen_socks:
        sel.register(listen_sock, selectors.EVENT_READ, _accept_cb)
    log.info('Waiting for events ...')
    while True:
        events = sel.select(timeout=10)
        if not len(events):
            log.info('No events before timeout. Breaking')
            break
        for key, mask in events:
            cb = key.data
            cb(key.fileobj, mask)
            # log.info('%s -> %s', key, mask)
    log.info('Going away now. Bye friend, I\'ll miss you :/')
    # ret = tor_client.send_msg(c, CoordStartMeas('relay1'))
    # log.info('%s', ret)


def _get_listen_sockets(
        addr_port: Tuple[str, int]) -> Optional[List[socket.socket]]:
    ''' Take the given (hostname, port) and opening listening socket(s).

    The hostname can be an ipv4 or ipv6 address or a hostname. If it's a
    hostname, we resolve it and listen on one ipv4 and one ipv6 address to
    which it resolves.

    Special hostname values:

        - '0' means all ipv4 addresses
        - '::' means all ipv6 addresses
        - 'localhost' could be both a local ipv4 and a local ipv6 address
        depending on your system

    The port ... well it must be a valid port value as an integer. The special
    value 0 means the OS will choose a port for us. This probably isn't very
    useful when you want the port to be known in advance and to not change next
    time you run the program.

    If we are unable to bind and listen on all sockets, then we close any
    existing sockets and return None. On success we return a list of sockets,
    even if that list only has one socket in it.
    '''
    out: List[socket.socket] = []
    # Look up all the IP addresses the given addr resolves it. It might be more
    # than one if it resolves to both an ipv4 and ipv6 address.
    addr_infos = socket.getaddrinfo(
        addr_port[0], addr_port[1],
        type=socket.SOCK_STREAM)
    # Create a socket for each address, then bind and listen on it
    for addr_info in addr_infos:
        family, type, _, _, sock_addr = addr_info
        log.debug(
            'Binding to %s (%s) port %d ...',
            addr_port[0], sock_addr[0], sock_addr[1])
        s = socket.socket(family, type)
        try:
            s.bind(sock_addr[0:2])
        except OSError as e:
            log.error('Could not bind to %s: %s', sock_addr[0:2], e)
            s.close()
            for s_ in out:
                s_.close()
            return None
        log.debug('Bound to %s:%d', *s.getsockname()[0:2])
        s.listen()
        log.info('Listening to %s:%d', *s.getsockname()[0:2])
        out.append(s)
    return out
