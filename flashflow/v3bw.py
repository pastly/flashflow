''' Functions to generate a v3bw file from the latest per-second measurement
results '''
from flashflow.results_logger import MeasLine, MeasLineBegin, MeasLineData,\
    MeasLineEnd, Meas
import datetime
import gzip
import logging
import os
import time
from glob import iglob
from shutil import rmtree
from typing import List, Dict
from . import __version__ as FF_VERSION


log = logging.getLogger(__name__)
V3BW_FORMAT_VERSION = "1.5.0"


def _read(fname: str) -> List[MeasLine]:
    ''' Read all per-second results from the given filename.

    :param fname: the filename to read
    :returns: List of :class:`MeasLine`, sorted by timestamp

    The file must exist. The file is partially assumed to be well-formed: it
    might have invalid lines or comments, which will be skipped over just fine,
    but this function does not actually care that BEGIN lines come before data
    lines, which themselves come before END lines. :func:`sorted` is stable, so
    good order in == good order out.

    If the file name ends in ``.gz``, it is assumed to be gzip compressed.
    '''
    out = []
    if fname.endswith('.gz'):
        fd = gzip.open(fname, 'rt')
    else:
        fd = open(fname, 'rt')
    for line in fd:
        meas_line = MeasLine.parse(line)
        if not meas_line:
            continue
        out.append(meas_line)
    fd.close()
    return sorted(out, key=lambda item: item.ts)


def _find_files(base: str, min_ts: float = 0) -> List[str]:
    ''' Find all result files related to the given ``base`` filename.

    Sort them by modification time such that the oldest files are first, and
    return them all as a list. The ``base`` file *should* be last, given proper
    function of the rest of FlashFlow, but do not assume it will be.

    :param base: The path to the current results filename (e.g.
        `data-coord/results/results.log``). Assuming it exists, it will appear
        in the returned list of files. A ``*`` will be appended, and any files
        found when shell globbing with that will also be returned.
    :param min_ts: Only consider files with mtime equal or greater than this.
        Defaults to zero, meaning consider all files found.
    '''
    # List of (mod_time, file_name) tuples
    cache = []
    for fname in iglob(base + '*'):
        # Check if a file. It could be a directory.
        if not os.path.isfile(fname):
            continue
        # Check if new enough
        stat = os.stat(fname)
        if stat.st_mtime < min_ts:
            continue
        cache.append((stat.st_mtime, fname))
    # Since modification time is first, sorting the list of tuples will sort by
    # mod_time s.t. oldest is first.
    cache.sort()
    return [i[1] for i in cache]


def _write_results(
        symlink_fname: str, results: Dict[str, Meas], ts: float) -> str:
    ''' Write a new v3bw file and return the path to the new file.

    The output format follows the "Bandwidth File Format".
    https://gitweb.torproject.org/torspec.git/tree/bandwidth-file-spec.txt
    If FlashFlow claims to output version X, but it is found to violate the
    spec, revisions to FlashFlow should err on the side of simplicity. That may
    mean supporting version X-1 instead. The spec for what should be a simple
    document format is 1000 lines too long.

    :param symlink_fname: Path prefix for the file to create. Will end up being
        a symlink, and the actual file will be at this path plus a date suffix.
    :param results: The measurements
    :param ts: The current timestamp
    :returns: The path to the file we created
    '''
    # Prepare the header
    out = f'''{int(ts)}
version={V3BW_FORMAT_VERSION}
software=flashflow
software_version={FF_VERSION}
=====
'''
    # Add each relay's line
    for relay_fp, meas in results.items():
        bw = max(1, int(meas.result()/1000))
        out += f'node_id=${relay_fp} bw={bw} '\
            f'measured_at={int(meas.start_ts)}\n'
    actual_fname = symlink_fname + '.' + datetime.datetime\
        .utcfromtimestamp(ts).isoformat(timespec='seconds')
    with open(actual_fname, 'wt') as fd:
        fd.write(out)
    # According to a comment in sbws code, renaming a file is atomic. Thus make
    # the symlink at a temporary location, then rename it to the real desired
    # location.
    # https://github.com/torproject/sbws/blob/15fbb2bd3e290f30b18536d0822c703dbca65c4c/sbws/lib/v3bwfile.py#L1477
    symlink_tmp_fname = symlink_fname + '.tmp'
    # First ensure the temporary file doesn't exist. Perhaps from a previous
    # run that crashed at a very exact wrong time.
    try:
        os.unlink(symlink_tmp_fname)
    except IsADirectoryError:
        rmtree(symlink_tmp_fname)
    except FileNotFoundError:
        # This is actually the normal case. This is fine.
        pass
    os.symlink(os.path.basename(actual_fname), symlink_tmp_fname)
    os.rename(symlink_tmp_fname, symlink_fname)
    return actual_fname


def gen(v3bw_fname: str, results_fname: str, max_results_age: float) -> str:
    ''' Generate a v3bw file based on the latest per-second measurement results
    we have on disk.

    :param v3bw_fname: The path to the v3bw file to create
    :param results_fname: The path to the current results filename (e.g.
        ``data-coord/results/results.log``). It will be read for the latest
        results, and if needed, an ``*`` appended to the name to search for
        adjacent logrotated files for additional necessary data.
    :param max_results_age: The maximum number of seconds in the past a
        measurement can have occurred and we'll still include it in the v3bw
        file.

    :returns: Path to the v3bw file created. This will be the ``v3bw_fname``
        argument plus a suffix.
    '''
    log.info('Beginning v3bw file generation')
    now = time.time()
    ignored_lines = {
        'age': 0,
        'type': 0,
        'unknown_meas': 0,
    }
    used_lines = 0
    measurements: Dict[str, Meas] = {}
    fnames = _find_files(results_fname, min_ts=now-max_results_age)
    for fname in fnames:
        for line in _read(fname):
            if line.ts < now - max_results_age:
                ignored_lines['age'] += 1
            if isinstance(line, MeasLineBegin):
                if line.relay_fp in measurements:
                    log.info(
                        'Found new measurement for %s, forgetting old one',
                        line.relay_fp)
                used_lines += 1
                measurements[line.relay_fp] = Meas(line)
            elif isinstance(line, MeasLineEnd):
                if line.relay_fp not in measurements:
                    log.warn(
                        'Found END line for %s but no known meas. Dropping.',
                        line.relay_fp)
                    ignored_lines['unknown_meas'] += 1
                    continue
                used_lines += 1
                measurements[line.relay_fp].set_end(line)
            elif isinstance(line, MeasLineData):
                if line.is_bg():
                    measurements[line.relay_fp].add_bg(line)
                else:
                    measurements[line.relay_fp].add_measr(line)
                used_lines += 1
            else:
                log.warn(
                    'Unhandled MeasLine type %s', type(line))
                ignored_lines['type'] += 1
    log.info(
        'Finished v3bw file generation. Used %d lines from %d files to '
        'produce results for %d relays. Ignored %d lines due to age, '
        '%d lines due to unknown type, %d lines due to unknown meas.',
        used_lines, len(fnames), len(measurements),
        ignored_lines['age'], ignored_lines['type'],
        ignored_lines['unknown_meas'])
    return _write_results(v3bw_fname, measurements, now)
