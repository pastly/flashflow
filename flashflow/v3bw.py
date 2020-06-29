''' Functions to generate a v3bw file from the latest per-second measurement
results '''
# from glob import iglob


class Measurement:
    ''' Store all per-second results for a single measurement of a single relay

    :param relay_fp: The relay's fingerprint
    :param meas_id: The measurement ID
    '''
    def __init__(self, relay_fp: str, meas_id: int):
        self.relay_fp = relay_fp
        self.meas_id = meas_id


def _read(fname: str) -> None:
    ''' Read all per-second results from the given filename. The file must
    exist. '''
    pass


def gen(results_fname: str) -> None:
    ''' Generate a v3bw file based on the latest per-second measurement results
    we have on disk.

    :param results_fname: The path to the current results filename (e.g.
        ``data-coord/results/results.log``). It will be read for the latest
        results, and if needed, an ``*`` appended to the name to search for
        adjacent logrotated files for additional necessary data.

    :returns: ``None``
    '''
    return None
