import os
import unittest
from tempfile import TemporaryDirectory
from flashflow import tor_client

NET_TAR = 'tests/integration/net.txz'


class Base(unittest.TestCase):
    ''' Abstract out the some state creation for tests in this file '''
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.setUp_network()
        # DataDirectory to use if test will be launching a Tor client
        self.datadir = TemporaryDirectory()

    def setUp_network(self):
        import subprocess
        import time
        # Extract network
        cmd = 'tar xf {fname} --directory={tmpdir}'.format(
            fname=NET_TAR,
            tmpdir=self.tempdir.name)
        assert subprocess.run(cmd.split()).returncode == 0
        # Start network
        net_dname = os.path.join(self.tempdir.name, 'net')
        cmd = './02-start-network.sh'
        ret = subprocess.run(
            cmd, cwd=net_dname,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert ret.returncode == 0
        # Check it has bootstrapped
        time.sleep(1)
        cmd = './03-network-in-ready-state.py -d auth* guard* middle* exit*'
        ret = subprocess.run(
            # need shell=True so the globbing in the command works
            cmd, cwd=net_dname, shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert ret.returncode == 0

    def tearDown(self):
        import subprocess
        import time
        net_dname = os.path.join(self.tempdir.name, 'net')
        subprocess.run(['./04-stop-network.sh'], cwd=net_dname)
        time.sleep(0.100)
        self.tempdir.cleanup()
        self.datadir.cleanup()


class TestLaunchTor(Base):
    ''' Can launch a tor client and successfully bootstrap '''
    TOR_BIN = 'tor'

    def test_basic_testnet_tor(self):
        ''' Can launch a tor client that connects to our local test network '''
        torrc_common = os.path.join(self.tempdir.name, 'net', 'torrc-common')
        torrc_extra = torrc_for_testnet_client(torrc_common)
        with TemporaryDirectory() as datadir:
            c = tor_client.launch(self.TOR_BIN, datadir, torrc_extra)
            assert c.is_authenticated()
            line = c.get_info('status/bootstrap-phase')
            state, _, progress, *_ = line.split()
            assert state == 'NOTICE'
            progress = int(progress.split('=')[1])
            assert progress == 100


def torrc_for_testnet_client(torrc_common: str):
    ''' Given the filename for the testnet's torrc-common file containing
    the ``DirAuthority`` lines, return the extra torrc lines necessary to
    connect to this testnet. I.e. those ``DirAuthority`` lines and
    ``TestingTorNetwork 1``.
    '''
    out = 'TestingTorNetwork 1\n'
    with open(torrc_common, 'rt') as fd:
        for line in fd:
            if line.startswith('DirAuthority'):
                out += line
    return out
