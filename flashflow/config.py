from configparser import ConfigParser
from tempfile import NamedTemporaryFile
import os
import logging
import logging.config
from typing import Optional

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
    conf = ConfigParser()
    return _extend_config(conf, DEF_CONF_INI)


def _get_default_log_config(conf=None):
    conf = conf or ConfigParser()
    return _extend_config(conf, DEF_CONF_LOG_INI)


def _get_user_config(fname: Optional[str], conf=None):
    conf = conf or ConfigParser()
    if fname is None:
        return conf
    return _extend_config(conf, fname)


def _extend_config(conf, fname: str):
    # Logging here probably won't work. It probably hasn't been configured yet.
    # print(fname)
    with open(fname, 'rt') as fd:
        conf.read_file(fd, source=fname)
    return conf
