from argparse import ArgumentParser
import logging
from .. import tor_client
from ..tor_ctrl_msg import CoordStartMeas


log = logging.getLogger(__name__)


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow coordinator.'
    p = sub.add_parser('coord', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    c = tor_client.launch(
        conf.getpath('coord', 'tor_bin'),
        conf.getpath('coord', 'tor_datadir'),
        conf.get('coord', 'torrc_extra_lines')
    )
    if not c:
        return
    ret = tor_client.send_msg(c, CoordStartMeas('relay1'))
    log.info('%s', ret)
