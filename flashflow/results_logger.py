''' Helper functions for writing per-second measurement results to a file that
might rotate.

Results are "logged" via :mod:`logging` at level ``INFO``. It is important that
the user does not edit the way these messages are logged.
If the user would like to rotate the output file, e.g. with `logrotate
<https://linux.die.net/man/8/logrotate>`_, they can do that because by default
(*and this should not be changed lightly*) these "log messages" get "logged"
via a :class:`logging.handlers.WatchedFileHandler`, which handles this
situation gracefully.

Usage
=====

Call :meth:`write_begin` once at the beginning of the active measurement phase.
As measurement results come in every second from measurers, call
:meth:`write_meas` for each. Likewise for per-second background traffic reports
and :meth:`write_bg`.  As soon as active measurement is over, call
:meth:`write_end`.

Output Format
=============

Output is line based. Multiple measurements can take place simultaneously, in
which case per-second results from measurements of different relays can be
interleaved.

A **BEGIN** line signals the start of data for the measurement of a relay. An
**END** line signals the end. Between these lines there are zero or more result
lines for the measurement of this relay, each with a per-second result from
either a measurer measuring that relay or that relay itself reporting the
amount of background traffic it saw that second.

BEGIN Line
----------

::

    <fp> <time> BEGIN

Where:

- ``fp``: the fingerprint of the relay this BEGIN message is for.
- ``time``: the integer unix timestamp at which active measurement began.

Example::

    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979504 BEGIN

END line
--------

::

    <fp> <time> END

Where:

- ``fp``: the fingerprint of the relay this END message is for.
- ``time``: the integer unix timestamp at which active measurement ended.

Example::

    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979534 END


Results line
------------

::

    <fp> <time> <is_bg> GIVEN=<given> TRUSTED=<trusted>

Where:

- ``fp``: the fingerprint of the relay.
- ``time``: the integer unix timestamp at which this result was received.
- ``is_bg``: 'BG' if this result is a report from the relay on the number of
  background bytes it saw in the last second, or 'MEASR' if this is a result
  from a measurer
- ``given``: the number of bytes reported
- ``trusted``: if a bg report from the relay, the maximum `given` is trusted to
  be; or if a measurer result, then the same as `given`.

Both ``given`` and ``trusted`` are in bytes. Yes, for measurer lines it is
redundant to specify both.

Background traffic reports from the relay include the raw actual reported value
in ``given``; if the relay is malicious and claims 8 TiB of background traffic
in the last second, you will see that here. ``trusted`` is the **max** that
``given`` can be. When reading results from this file, use ``min(given,
trusted)`` as the trusted number of background bytes this second.

Example::

    # bg report from relay, use GIVEN b/c less than TRUSTED
    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979083 BG GIVEN=744904 TRUSTED=1659029
    # bg report from relay, use TRUSTED b/c less than GIVEN
    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979042 BG GIVEN=671858 TRUSTED=50960
    # result from measurer, always trusted
    B0430D21D6609459D141078C0D7758B5CA753B6F 1591979083 MEASR GIVEN=5059082 TRUSTED=5059082
'''
import logging

log = logging.getLogger(__name__)


def write_begin(fp: str, ts: int):
    ''' Write a log line indicating the start of the given relay's measurement.

    :param fp: the fingerprint of the relay
    :param ts: the unix timestamp at which the measurement began
    '''
    return _write_beginend(fp, ts, True)


def write_end(fp: str, ts: int):
    ''' Write a log line indicating the end of the given relay's measurement.

    :param fp: the fingerprint of the relay
    :param ts: the unix timestamp at which the measurement ended
    '''
    return _write_beginend(fp, ts, False)


def write_meas(fp: str, ts: int, res: int):
    ''' Write a single per-second result from a measurer to our results.

    :param fp: the fingerprint of the relay
    :param ts: the unix timestamp at which the result came in
    :param res: the number of measured bytes
    '''
    return _write_result(fp, ts, False, res, res)


def write_bg(fp: str, ts: int, given: int, trusted: int):
    ''' Write a single per-second report of bg traffic from the relay to our
    results.

    :param fp: the fingerprint of the relay
    :param ts: the unix timestamp at which the result came in
    :param given: the number of reported bg bytes
    :param trusted: the maximum given should be (from our perspective in this
        logging code, it's fine if given is bigger than trusted)
    '''
    return _write_result(fp, ts, True, given, trusted)


def _write_result(fp: str, ts: int, is_bg: bool, given: int, trusted: int):
    ''' Write a single per-second result from either a measurer or the relay
    (for bg traffic reports) to our log file.

    :param fp: the fingerprint of the relay
    :param ts: the unix timestamp at which the result came in
    :param is_bg: True if this result is a bg report from the relay, else False
        for a result from a measurer
    :param given: the number of measured bytes or reported bg bytes that second
    :param trusted: the maximum given should be (from our perspective in this
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
