from argparse import ArgumentParser
import logging
from typing import Dict, Any
import flashflow.cmd.coord
import flashflow.cmd.ctrl
import flashflow.cmd.measurer
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
    sub = p.add_subparsers(dest='cmd')
    flashflow.cmd.coord.gen_parser(sub)
    flashflow.cmd.ctrl.gen_parser(sub)
    flashflow.cmd.measurer.gen_parser(sub)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def call_real_main(args, conf) -> None:
    ''' Figure out what FlashFlow command the user gave and call into that
    command's main function where the real work begins to happen. The only
    logic here should be figuring out what command's main to call. '''
    # Most (actually, all as of right now) command's main functions take these
    # arguments
    def_args = [args, conf]
    def_kwargs: Dict[str, Any] = {}
    # How to call in to each command's main
    cmds = {
        'coord': {
            'f': flashflow.cmd.coord.main,
            'a': def_args, 'kw': def_kwargs,
        },
        'ctrl': {
            'f': flashflow.cmd.ctrl.main,
            'a': def_args, 'kw': def_kwargs,
        },
        'measurer': {
            'f': flashflow.cmd.measurer.main,
            'a': def_args, 'kw': def_kwargs,
        },
    }
    # The keys in the `cmds` dict must be the same as each command specified in
    # its gen_parser(...) function, thus it will be in `cmds`. args.cmd will
    # also be non-None because our caller must have checked that already.
    assert args.cmd in cmds
    # Here we go!
    cmd = cmds[args.cmd]
    return cmd['f'](*cmd['a'], *cmd['kw'])  # type: ignore


def main() -> None:
    ''' Entry point when called on the command line as `flashflow ...`.

    Do boring boilerplate stuff to get started initially. Parse the command
    line arguments and configuration file, then hand off control. This is where
    the bulk of the startup boring crap should happen.  '''
    p = create_parser()
    args = p.parse_args()
    if args.cmd is None:
        p.print_help()
        return
    try:
        conf = get_config(args.config)
    except FileNotFoundError as e:
        log.critical('Unable to open a config file: %s', e)
        return
    assert conf
    config_logging(conf)
    call_real_main(args, conf)
