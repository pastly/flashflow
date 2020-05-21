from configparser import ConfigParser, ExtendedInterpolation
from tempfile import NamedTemporaryFile
import os
import logging
import logging.config
from typing import Optional, Tuple

from . import PKG_DIR


log = logging.getLogger(__name__)

DEF_CONF_INI = os.path.join(PKG_DIR, 'config.default.ini')
DEF_CONF_LOG_INI = os.path.join(PKG_DIR, 'config.log.default.ini')


def get_config(user_conf_fname: Optional[str]):
    conf = _get_default_config()
    conf = _get_default_log_config(conf=conf)
    conf = _get_user_config(user_conf_fname, conf=conf)
    return conf


def config_logging(conf):
    with NamedTemporaryFile('w+t') as fd:
        conf.write(fd)
        fd.seek(0, 0)
        logging.config.fileConfig(fd.name)


def _get_default_config():
    conf = _empty_config()
    return _extend_config(conf, DEF_CONF_INI)


def _get_default_log_config(conf=None):
    conf = conf or _empty_config()
    return _extend_config(conf, DEF_CONF_LOG_INI)


def _get_user_config(fname: Optional[str], conf=None):
    conf = conf or _empty_config()
    if fname is None:
        return conf
    return _extend_config(conf, fname)


def _extend_config(conf, fname: str):
    # Logging here probably won't work. It probably hasn't been configured yet.
    # print(fname)
    with open(fname, 'rt') as fd:
        conf.read_file(fd, source=fname)
    return conf


def _expand_path(path: str) -> str:
    ''' Expand path string containing shell variables and '~' into their
    values. Environment variables MUST have their '$' escaped by another '$'.
    For example, '$$XDG_RUNTIME_DIR/foo.bar'. '''
    return os.path.expanduser(os.path.expandvars(path))


def _expand_addr(addr: str) -> Optional[Tuple[str, int]]:
    ''' Parse the given string into a (hostname, port) tuple. Not much effort
    is put into validation:
        - the port is checked to be a valid integer
        - if the host looks like an ipv6 address with brackets, they are
        removed
    and otherwise the values are left as-is.

    On success, returns (hostname, port) where port is an integer. On error,
    logs about the error and returns None. ConfigParser does **not** see this
    an error case worthy of special treatement, so you need to check if the
    returned value is None yourself.

        '127.0.0.1'        --> None (error: no port)
        ':1234'            --> None (error: no host)
        'example.com:asdf' --> None (error: invalid port)
        'localhost:1234'   --> ('localhost', 1234)
        ':1234'            --> ('', 1234)
        '127.0.0.1:1234'   --> ('127.0.0.1', 1234)
        '[::1]:0'          --> ('::1', 0)
        '::1:0'            --> ('::1', 0)

    It's not up to this function to decide how to specify "listen on all hosts"
    or "pick a port for me." These things should be documented and decided
    elsewhere.
    '''
    try:
        a, p = addr.rsplit(':', 1)
        if a.startswith('[') and a.endswith(']'):
            a = a[1:-1]
        return a, int(p)
    except ValueError as e:
        log.error('invalid host:port "%s" : %s', addr, e)
        return None


def _empty_config() -> ConfigParser:
    return ConfigParser(
        interpolation=ExtendedInterpolation(),
        converters={
            'path': _expand_path,
            'addr': _expand_addr,
        })
