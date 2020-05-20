from argparse import ArgumentParser
import logging
from . import __version__
from .config import get_config, config_logging
from .import tor_client


log = logging.getLogger(__name__)


def create_parser():
    p = ArgumentParser()
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('-c', '--config', help='Path to flashflow config file')
    # p.add_argument('--log-level',
    #                choices=['debug', 'info', 'warning', 'error', 'critical'],
    #                help='Override the configured flashflow log level')
    return p


def main_(args, conf):
    c = tor_client.connect(conf.getpath('coord', 'tor_location'))
    if not c:
        return
    print(c)


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
