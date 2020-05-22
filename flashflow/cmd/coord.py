from argparse import ArgumentParser
import glob
import os
import selectors
import ssl
import socket
from tempfile import NamedTemporaryFile
import logging
from .. import tor_client
# from ..tor_ctrl_msg import CoordStartMeas
from typing import Optional, Tuple, List, IO


log = logging.getLogger(__name__)


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow coordinator.'
    p = sub.add_parser('coord', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    os.makedirs(conf.getpath('coord', 'datadir'), mode=0o700, exist_ok=True)
    os.makedirs(conf.getpath('coord', 'keydir'), mode=0o700, exist_ok=True)
    # Open listen sockets
    addr_port = conf.getaddr('coord', 'listen_addr')
    if addr_port is None:
        log.error('Don\'t know what to listen on')
        return
    listen_socks = _get_listen_sockets(addr_port)
    if not listen_socks:
        log.error('Unable to bind/listen on %s', addr_port)
        return
    # Read measurer certs
    _, measr_cert_fname = _gen_concated_measr_cert_file(
        conf.getpath('coord', 'keydir'), conf.getpath('coord', 'key'))
    # Wrap listen sockets in TLS
    listen_socks = [
        ssl.wrap_socket(
            s, server_side=True,
            # Require clients to connect with client certs
            cert_reqs=ssl.CERT_REQUIRED,
            # Path to our cert (file containing both our private key and
            # certificate, in that order)
            certfile=conf.getpath('coord', 'key'),
            # Path to file containing all the client certs we allow to connect
            # to us. Note how these are treated as "CA certs." It's intended
            # that the "CA" certs listed here will be used themselves as client
            # certs, and this is the simpliest way I found to make the server
            # side trust them. However, I think it is possible that the listed
            # client certs can issue certificates that we never intended to
            # trust, and as long as the client provides a chain back up to one
            # of these "CA" certs, then we will trust it. I consider this okay:
            # you as a coordinator trust your measurers to behave correctly,
            # measure accurately, etc. If you can't trust them to protect their
            # TLS identity or to not do dangerous things with it, then maybe
            # they shouldn't be a measurer.
            ca_certs=measr_cert_fname,
        )
        for s in listen_socks]
    # Launch Tor
    c = tor_client.launch(
        conf.getpath('tor', 'tor_bin'),
        conf.getpath('coord', 'tor_datadir'),
        conf.get('tor', 'torrc_extra_lines')
    )
    if not c:
        return

    # Object allowing us to poll on sockets until they're ready
    sel = selectors.DefaultSelector()

    # Called when we get a new connection. I believe the TLS handshake is
    # performed in this function when we call accept(). Regardless, upon the
    # call to accept, if the client peer does not have a trusted cert, it
    # throws an SSLError. If the call succeeds, then the TLS handshake is
    # finished and trusted.
    def _accept_cb(listen_sock: ssl.SSLSocket, mask: int) -> None:
        try:
            # The listening socket gives us a new ssl.SSLSocket as `conn` for
            # communicating on this connection.
            conn, addr = listen_sock.accept()
        except ssl.SSLError as e:
            # Can't figure out how to log the IP:port of the remote entity
            # here: addr doesn't exist since sock.accept() failed, and
            # sock.getpeername() throws an OSError about the transport endpoint
            # not being connected.
            # log.info('%s', dir(sock))
            log.warn('Disallowing connection from someone: %s', e)
            return
        log.info('Accepted connection from %s', addr)
        # log.info('Peer cert: %s', conn.getpeercert())
        # Not sure if we actually need/want to set non-blocking here. That's
        # just what examples did.
        conn.setblocking(False)
        sel.register(conn, selectors.EVENT_READ, _read_cb)

    # Called when data available to read on a connection (after TLS)
    def _read_cb(conn: ssl.SSLSocket, mask: int) -> None:
        try:
            data = conn.recv(1024)
        except ConnectionResetError as e:
            # TODO with whom? Log who they were. conn.getpeername() won't work
            # as they're gone now.
            log.warn('Could not finish reading: %s', e)
            sel.unregister(conn)
            conn.close()
            return
        log.debug('Got %s', data)
        if not data:
            # TODO with whom? Log who they were. conn.getpeername() won't work
            # as they're gone now.
            log.info(
                'Empty read. Closing connection')
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
