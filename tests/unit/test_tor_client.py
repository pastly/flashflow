from flashflow import tor_client


class TestConnect:
    ''' Test Tor client stuff that doesn't require an actual working tor
    client/network. See also the integration tests.
    '''

    def test_nonexist(self):
        ''' Cannot connect to non-existent ControlSocket location '''
        ret = tor_client._connect('/this/is/a/path/that/does/not/exist')
        assert ret is None
