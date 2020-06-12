import logging

log = logging.getLogger(__name__)


def write_begin(fp: str, ts: int):
    ''' Write a log line indicating the start of the given relay's measurement.

    - fp: the fingerprint of the relay
    - ts: the unix timestamp at which the measurement began
    '''
    return _write_beginend(fp, ts, True)


def write_end(fp: str, ts: int):
    ''' Write a log line indicating the end of the given relay's measurement.

    - fp: the fingerprint of the relay
    - ts: the unix timestamp at which the measurement ended
    '''
    return _write_beginend(fp, ts, False)


def write_meas(fp: str, ts: int, res: int):
    ''' Write a single per-second result from a measurer to our results.

    - fp: the fingerprint of the relay
    - ts: the unix timestamp at which the result came in
    - res: the number of measured bytes
    '''
    return _write_result(fp, ts, False, res, res)


def write_bg(fp: str, ts: int, given: int, trusted: int):
    ''' Write a single per-second report of bg traffic from the relay to our
    results.

    - fp: the fingerprint of the relay
    - ts: the unix timestamp at which the result came in
    - given: the number of reported bg bytes
    - trusted: the maximum given should be (from our perspective in this
    logging code, it's fine if given is bigger than trusted)
    '''
    return _write_result(fp, ts, True, given, trusted)


def _write_result(fp: str, ts: int, is_bg: bool, given: int, trusted: int):
    ''' Write a single per-second result from either a measurer or the relay
    (for bg traffic reports) to our log file.

    - fp: the fingerprint of the relay
    - ts: the unix timestamp at which the result came in
    - is_bg: True if this result is a bg report from the relay, else False for
    a result from a measurer
    - given: the number of measured bytes or reported bg bytes that second
    - trusted: the maximum given should be (from our perspective in this
    logging code, it's fine if given is bigger than trusted)
    '''
    label = 'BG' if is_bg else 'MEASR'
    log.info('%s %d %s GIVEN=%d TRUSTED=%d', fp, ts, label, given, trusted)


def _write_beginend(fp: str, ts: int, is_begin: bool):
    ''' Write a log line indicating the start or end of the given relay's
    measurement.

    - fp: the fingerprint of the relay
    - ts: the unix timestamp at which the measurement began/ended
    - is_begin: True if this is for the beginning, else False for ending
    '''
    word = 'BEGIN' if is_begin else 'END'
    log.info('%s %d %s', fp, ts, word)
