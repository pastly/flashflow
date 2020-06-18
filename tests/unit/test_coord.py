import unittest
from flashflow.cmd.coord import States


class MockMeasrProtocol:
    ''' Mock coord.MeasrProtocol '''
    pass


class Base(unittest.TestCase):
    ''' Abstract out the some state creation for tests in this file '''
    def setUp(self):
        from flashflow.cmd.coord import StateMachine
        from flashflow.config import get_config
        from tempfile import TemporaryDirectory
        self.datadir = TemporaryDirectory()
        self.conf = get_config(None)
        self.conf['coord']['datadir'] = self.datadir.name
        self.sm = StateMachine(self.conf)

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
