import unittest
from unittest.mock import MagicMock
from flashflow.cmd.coord import States
from flashflow.msg import FFMsg


class MockMeasrProtocol:
    ''' Mock coord.MeasrProtocol '''
    pass


class MockTorController(MagicMock):
    pass


def rand_listen_addr():
    from random import randint
    # return '[::1]:' + str(randint(10000, 64000))
    return 'localhost:' + str(randint(10000, 64000))


# def loop():
#     import asyncio
#     return asyncio.get_event_loop()


class Base(unittest.TestCase):
    ''' Abstract out the some state creation for tests in this file '''
    def setUp(self):
        from flashflow.cmd.coord import StateMachine
        from flashflow.config import get_config
        from tempfile import TemporaryDirectory
        self.datadir = TemporaryDirectory()
        self.conf = get_config(None)
        self.conf['coord']['datadir'] = self.datadir.name
        self.conf['coord']['keydir'] = 'tests/data/coord/keys'
        self.conf['coord']['listen_addr'] = rand_listen_addr()
        self.sm = StateMachine(self.conf)
        self.sm.tor_client = MockTorController()

    def tearDown(self):
        self.datadir.cleanup()


class TestInitialState(Base):
    ''' Start out with properly initialized state '''
    def test(self):
        self.assertEqual(self.sm.state, States.START)
        self.assertFalse(self.sm.measurements)
        self.assertFalse(self.sm.measurers)


class TestMeasrConnect(Base):
    ''' What happens when a measurer connects

    We always accept new measurer connections. This probably isn't what we
    actually want to do.
    https://gitlab.torproject.org/pastly/flashflow/-/issues/13 '''
    def test_READY(self):
        ''' If we're in the READY state, we accept the new connection. '''
        self.sm.state = States.READY
        self.sm.notif_measurer_connected(MockMeasrProtocol())
        self.assertEqual(len(self.sm.measurers), 1)

    @unittest.skip('pastly/flashflow#13')
    def test_nonREADY(self):
        ''' When any state other than READY, should not accept measr conn '''
        # Just test the one state, START, for now
        assert self.sm.state == States.START
        self.sm.notif_measurer_connected(MockMeasrProtocol())
        self.assertFalse(self.sm.measurers)


class TestMeasrDisconnect(Base):
    ''' What happens when a measurer disconnects '''
    def test_empty(self):
        ''' While this should never happen, nothing bad happens if the measr
        that disconnects doesn't exist '''
        assert self.sm.state == States.START
        m = MockMeasrProtocol()
        self.sm.notif_measurer_disconnected(m)
        # empty
        self.assertFalse(self.sm.measurers)
        # still in init state
        self.assertEqual(self.sm.state, States.START)

    def test_exist(self):
        ''' Measr exists, and is removed '''
        m = MockMeasrProtocol()
        self.sm.measurers.append(m)
        assert len(self.sm.measurers) == 1
        self.sm.notif_measurer_disconnected(m)
        self.assertFalse(self.sm.measurers)

    def test_not_exist(self):
        ''' We have measurers, but this isn't one of them '''
        m_listed = MockMeasrProtocol()
        m_unlisted = MockMeasrProtocol()
        self.sm.measurers.append(m_listed)
        assert len(self.sm.measurers) == 1
        self.sm.notif_measurer_disconnected(m_unlisted)
        self.assertEqual(len(self.sm.measurers), 1)


class TestEnsureListenSocks(Base):
    ''' Transition to the state for opening listening sockets. '''
    @unittest.skip(
        'Can\'t figure out why contained cb() called twice, the 1st time '
        'with "address already in use". Actually ... it\'s probably because '
        'transitions calls _ensure_listen_socks itself and we are also '
        'calling it explicitly.')
    def test(self):
        assert self.sm.state == States.START
        self.sm.change_state_starting()
        # While working on this, I modified _ensure_listen_socks to return the
        # task.
        # task = self.sm._ensure_listen_socks()
        # loop().run_until_complete(task)
        # print(task)
        # print(self.sm.state)
        # assert False

    def test_bad_addr_port(self):
        ''' We're configured to use an invalid "hostname:port" string '''
        self.conf['coord']['listen_addr'] = 'example.com11111'
        assert self.sm.state == States.START
        self.sm.change_state_starting()
        with self.assertLogs('flashflow.cmd.coord', 'ERROR'):
            self.sm._ensure_listen_socks()
        self.assertEqual(self.sm.state, States.FATAL_ERROR)

    def test_no_keydir(self):
        ''' Our configured keydir doesn't exist, but must in order to load
        client TLS certs '''
        # This exists
        self.conf['coord']['key'] = 'tests/data/coord/keys/coord.pem'
        # This doesn't
        self.conf['coord']['keydir'] = '/tmp/directory/does/not/exist'
        assert self.sm.state == States.START
        self.sm.change_state_starting()
        with self.assertLogs('flashflow.cmd.coord', 'ERROR'):
            self.sm._ensure_listen_socks()
        self.assertEqual(self.sm.state, States.FATAL_ERROR)

    def test_no_key(self):
        ''' Our configured keydir doesn't exist, but must in order to load
        client TLS certs '''
        self.conf['coord']['key'] = '/tmp/coord/key/does/not/exist'
        assert self.sm.state == States.START
        self.sm.change_state_starting()
        with self.assertLogs('flashflow.cmd.coord', 'ERROR'):
            self.sm._ensure_listen_socks()
        self.assertEqual(self.sm.state, States.FATAL_ERROR)


class TestRecvMeasrMsg(Base):
    ''' What happens when we receive a FFMsg from a measurer in various
    situations.

    The only time we want to handle a FFMsg is when we are READY.
    '''
    def test_nonREADY(self):
        assert self.sm.state == States.START
        msg = FFMsg()
        measr = MockMeasrProtocol()
        for start_state in [
            States.START,
            States.ENSURE_LISTEN_SOCKS,
            States.ENSURE_CONN_W_TOR,
            # States.READY,  # testing all BUT this state
            States.NONFATAL_ERROR,
            States.FATAL_ERROR,
        ]:
            self.sm.state = start_state
            with self.assertLogs('flashflow.cmd.coord', 'ERROR'):
                self.sm.recv_measr_msg(measr, msg)
            self.assertEqual(self.sm.state, States.NONFATAL_ERROR)
