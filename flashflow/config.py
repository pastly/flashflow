from configparser import ConfigParser
from tempfile import NamedTemporaryFile
import os
import logging
import logging.config
from . import PKG_DIR


log = logging.getLogger(__name__)

DEF_CONF_INI = os.path.join(PKG_DIR, 'config.default.ini')
DEF_CONF_LOG_INI = os.path.join(PKG_DIR, 'config.log.default.ini')

def get_config(args):
    conf = _get_default_config()
    conf = _get_default_log_config(conf=conf)
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


def _extend_config(conf, fname):
    # print(fname)
    with open(fname, 'rt') as fd:
        conf.read_file(fd, source=fname)
    return conf
