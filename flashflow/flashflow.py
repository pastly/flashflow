from argparse import ArgumentParser
import logging
from . import __version__
from .config import get_config, config_logging


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
    log.critical('Hello!')


def main():
    p = create_parser()
    args = p.parse_args()
    conf = get_config(args)
    config_logging(conf)
    main_(args, conf)
