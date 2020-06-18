from argparse import ArgumentParser
import logging
import socket


log = logging.getLogger(__name__)


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Connect to a FlashFlow coordinator and tell it something'
    p = sub.add_parser('ctrl', description=d)
    p.add_argument('command', type=str, help='Command to send to coordinator')
    return p


def main(args, conf) -> None:
    s = socket.socket()
    addr_port = conf.getaddr('ctrl', 'coord_addr')
    s.connect((addr_port[0], addr_port[1]))
    s.sendall(args.command.encode('utf-8'))
    ret = s.recv(4096).decode('utf-8')
    print(ret)
    s.close()
    return
