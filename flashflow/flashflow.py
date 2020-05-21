from argparse import ArgumentParser
import logging
from . import __version__
from .config import get_config, config_logging
from .tor_ctrl_msg import CoordStartMeas
from . import tor_client


log = logging.getLogger(__name__)


def create_parser():
    p = ArgumentParser()
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('-c', '--config', help='Path to flashflow config file')
    # p.add_argument('--log-level',
    #                choices=['debug', 'info', 'warning', 'error', 'critical'],
    #                help='Override the configured flashflow log level')
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main_(args, conf) -> None:
    c = tor_client.launch(
        conf.getpath('coord', 'tor_bin'),
        conf.getpath('coord', 'tor_datadir'),
        conf.get('coord', 'torrc_extra_lines')
    )
    if not c:
        return
    ret = tor_client.send_msg(c, CoordStartMeas('relay1'))
    log.info('%s', ret)


def main():
    p = create_parser()
    args = p.parse_args()
    try:
        conf = get_config(args.config)
    except FileNotFoundError as e:
        log.critical('Unable to open a config file: %s', e)
        return
    assert conf
    config_logging(conf)
    main_(args, conf)
