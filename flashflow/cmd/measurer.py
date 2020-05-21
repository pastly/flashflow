from argparse import ArgumentParser
import logging
from .. import tor_client
from .. import msg


log = logging.getLogger(__name__)


def gen_parser(sub) -> ArgumentParser:
    ''' Add the cmd line options for this FlashFlow command '''
    d = 'Run as a FlashFlow measurer.'
    p = sub.add_parser('measurer', description=d)
    return p


# This function needs **some sort** of type annotation so that mypy will check
# the things it does. Adding the return value (e.g. '-> None') is enough
def main(args, conf) -> None:
    c = tor_client.launch(
        conf.getpath('tor', 'tor_bin'),
        conf.getpath('measurer', 'tor_datadir'),
        conf.get('tor', 'torrc_extra_lines')
    )
    if not c:
        return
    m = msg.Foo(1, 'abc', [1, '4', ()], {'a': None, 'b': 'jeff', 'c': -1})
    log.debug('%s', m)
    s = m.serialize()
    log.debug('%s', s)
    m_ = msg.Foo.deserialize(s)
    log.debug('%s', m_)
