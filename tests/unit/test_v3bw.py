import unittest
from flashflow import v3bw
import os
import time
from typing import Tuple
from tempfile import TemporaryDirectory


def touch(fname: str, times: Tuple[float, float] = None):
    ''' Update ``fname``\'s last access and modified times to now. If it does
    not exist, create it first. If ``times`` are specified, use them instead of
    the current time.

    :param fname: Name of file to update access and modified time
    :param times: tuple with access time and modified time, respectively
    '''
    if not times:
        now = time.time()
        times = (now, now)
    with open(fname, 'a') as fd:
        os.utime(fd.fileno(), times=times)


class TestFindFiles(unittest.TestCase):
    def test_nonexist(self):
        ''' Result file not existing, nor anything to glob for, returns empty
        list '''
        path = '/path/that/does/not/exist'
        assert v3bw._find_files(path) == []

    def test_justbase(self):
        ''' Result file exists, but nothing to glob for, still get list
        containing result file '''
        with TemporaryDirectory() as tempdir:
            base = os.path.join(tempdir, 'results.log')
            touch(base)
            assert v3bw._find_files(base) == [base]

    def test_justbase_unrelated(self):
        ''' Result file exists, nothing to glob for, and unrelated file in same
        dir. Get just result file. '''
        with TemporaryDirectory() as tempdir:
            base = os.path.join(tempdir, 'results.log')
            touch(base)
            touch(os.path.join(tempdir, 'unrelated_file'))
            assert v3bw._find_files(base) == [base]

    def test_justbase_unrelated_dir(self):
        ''' Result file exists, nothing to glob for, and similarly-named
        directory.  Get just result file. '''
        with TemporaryDirectory() as tempdir:
            base = os.path.join(tempdir, 'results.log')
            touch(base)
            os.mkdir(os.path.join(tempdir, 'results.log.thisisadir'))
            assert v3bw._find_files(base) == [base]

    def test_multi(self):
        ''' Multiple files to match, and they are returned in the correct order
        '''
        with TemporaryDirectory() as tempdir:
            # f1 is older, so returned first
            base = os.path.join(tempdir, 'results.log')
            f1 = base + 'f1'
            f2 = base + 'f2'
            touch(f1)
            touch(f2)
            assert v3bw._find_files(base) == [f1, f2]
            # f2 is older, so returned first
            touch(f2)
            touch(f1)
            assert v3bw._find_files(base) == [f2, f1]

    def test_base_not_first(self):
        ''' The base file isn't treated special and returned first. It's sorted
        based on modtime '''
        with TemporaryDirectory() as tempdir:
            base = os.path.join(tempdir, 'results.log')
            other = base + '.foo'
            touch(base)
            touch(other)
            # base is older, so returned first
            assert v3bw._find_files(base) == [base, other]
            # other is older, so returned first
            touch(other)
            touch(base)
            assert v3bw._find_files(base) == [other, base]

    def test_too_old(self):
        ''' The only file that exists is too old to be considered '''
        with TemporaryDirectory() as tempdir:
            fname = os.path.join(tempdir, 'foo')
            now = 1000000
            touch(fname, times=(now, now))
            assert v3bw._find_files(fname, min_ts=now+1) == []

    def test_recent_enough(self):
        ''' The only file that exists is new enough to be considered '''
        with TemporaryDirectory() as tempdir:
            fname = os.path.join(tempdir, 'foo')
            now = 1000000
            touch(fname, times=(now, now))
            assert v3bw._find_files(fname, min_ts=now-1) == [fname]
