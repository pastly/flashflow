''' Helper functions for writing per-second measurement results to a file that
might rotate, as well as classes for reading those results from files later.

**Note: The information here is only partially true until pastly/flashflow#4 is
implemented and this message is removed.**

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

    <meas_id> <time> BEGIN <fp>

Where:

- ``meas_id``: the measurement ID for this measurement
- ``time``: the integer unix timestamp at which active measurement began.
- ``fp``: the fingerprint of the relay this BEGIN message is for.

Example::

    58234 1591979504 BEGIN B0430D21D6609459D141078C0D7758B5CA753B6F

END line
--------

::

    <meas_id> <time> END

Where:

- ``meas_id``: the measurement ID for this measurement
- ``time``: the integer unix timestamp at which active measurement ended.

Example::

    58234 1591979534 END B0430D21D6609459D141078C0D7758B5CA753B6F


Results line
------------

::

    <meas_id> <time> <is_bg> GIVEN=<given> TRUSTED=<trusted>

Where:

- ``meas_id``: the measurement ID for this measurement
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
    58234 1591979083 BG GIVEN=744904 TRUSTED=1659029
    # bg report from relay, use TRUSTED b/c less than GIVEN
    58234 1591979042 BG GIVEN=671858 TRUSTED=50960
    # result from measurer, always trusted
    58234 1591979083 MEASR GIVEN=5059082 TRUSTED=5059082
'''
import logging
from statistics import median
from typing import Optional, List


log = logging.getLogger(__name__)


def _try_parse_int(s: str) -> Optional[int]:
    ''' Try to parse an integer from the given string. If impossible, return
    ``None``. '''
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _ensure_len(lst: List[int], min_len: int):
    ''' Ensure that the given list is at least ``min_len`` items long. If it
    isn't, append zeros to the right until it is. '''
    if len(lst) < min_len:
        lst += [0] * (min_len - len(lst))


class Meas:
    ''' Accumulate ``MeasLine*`` objects into a single measurement summary.

    The first measurement line you should see is a :class:`MeasLineBegin`;
    create a :class:`Meas` object with it. Then pass each :class:`MeasLineData`
    that you encounter to either :meth:`Meas.add_measr` or :meth:`Meas.add_bg`
    based on where it came from. Finally pass the :class:`MeasLineEnd` to tell
    the object it has all the data.

    Not much is done to ensure you're using this data storage class correctly.
    For example:

        - You can add more :class:`MeasLineData` after marking the end.
        - You can pass untrusted :class:`MeasLineData` from the relay to the
            :meth:`Meas.add_measr` function where they will be treated as
            trusted.
        - You can get the :meth:`Meas.result` before all data lines have been
            given.
        - You can provide data from different measurements for different
            relays.

    **You shouldn't do these things**, but you can. It's up to you to use your
    tools as perscribed.
    '''
    _begin: 'MeasLineBegin'
    _end: Optional['MeasLineEnd']
    _data: List[int]

    def __init__(self, begin: 'MeasLineBegin'):
        self._begin = begin
        self._end = None
        self._data = []

    @property
    def relay_fp(self) -> str:
        ''' The relay measured, as given in the initial :class:`MeasLineBegin`.
        '''
        return self._begin.relay_fp

    @property
    def meas_id(self) -> int:
        ''' The measurement ID, as given in the initial :class:`MeasLineBegin'.
        '''
        return self._begin.meas_id

    @property
    def start_ts(self) -> int:
        ''' The integer timestamp for when the measurement started, as given in
        the initial :class:`MeasLineBegin`. '''
        return self._begin.ts

    def _ensure_len(self, data_len: int):
        ''' Ensure we can store at least ``data_len`` items, expanding our data
        list to the right with zeros as necessary. '''
        if len(self._data) < data_len:
            self._data += [0] * (data_len - len(self._data))

    def add_measr(self, data: 'MeasLineData'):
        ''' Add a :class:`MeasLineData` to our results that came from a
        measurer.

        As it came from a measurer, we trust it entirely (and there's no
        ``trusted_bw`` member) and simply add it to the appropriate second.
        '''
        idx = data.ts - self.start_ts
        _ensure_len(self._data, idx + 1)
        self._data[idx] += data.given_bw

    def add_bg(self, data: 'MeasLineData'):
        ''' Add a :class:`MeasLineData` to our results that came from the relay
        and is regarding the amount of background traffic.

        As it came from the relay, we do not a ``given_bw > trusted_bw``. Thus
        we add the minimum of the two to the appropriate second.
        '''
        idx = data.ts - self.start_ts
        _ensure_len(self._data, idx + 1)
        assert data.trusted_bw is not None  # for mypy, bg will have this
        self._data[idx] += min(data.given_bw, data.trusted_bw)

    def set_end(self, end: 'MeasLineEnd'):
        ''' Indicate that there is no more data to be loaded into this
        :class:`Meas`. '''
        self._end = end

    def have_all_data(self) -> bool:
        ''' Check if we still expect to be given more data '''
        return self._end is not None

    def result(self) -> float:
        ''' Calculate and return the result of this measurement '''
        return median(self._data)


class MeasLine:
    ''' Parent class for other ``MeasLine*`` types. You should only ever need
    to interact with this class directly via its :meth:`MeasLine.parse` method.
    '''
    def __init__(self, meas_id: int, ts: int):
        self.meas_id = meas_id
        self.ts = ts

    def __str__(self):
        return '%d %d' % (
            self.meas_id,
            self.ts)

    @staticmethod
    def parse(s: str) -> Optional['MeasLine']:
        ''' Try to parse a MeasLine subclass from the given line ``s``. If
        impossible, return ``None``. '''
        s = s.strip()
        # ignore comment lines
        if s.startswith('#'):
            return None
        words = s.split()
        # minimum line length, in words, is 3: end lines have 3 words
        # maximum line length, in words, is 5: bg data lines have 5
        MIN_WORD_LEN = 3
        MAX_WORD_LEN = 5
        if len(words) < MIN_WORD_LEN or len(words) > MAX_WORD_LEN:
            return None
        # split off the prefix words (words common to all measurement data
        # lines).
        prefix, words = words[:2], words[2:]
        # try convert each one, bail if unable
        meas_id = _try_parse_int(prefix[0])
        ts = _try_parse_int(prefix[1])
        if meas_id is None or ts is None:
            return None
        # now act differently based on what type of line we seem to have
        if words[0] == 'BEGIN':
            # BEGIN <fp>
            if len(words) != 2:
                return None
            fp = words[1]
            return MeasLineBegin(fp, meas_id, ts)
        elif words[0] == 'END':
            # END
            return MeasLineEnd(meas_id, ts)
        elif words[0] == 'MEASR':
            # MEASR GIVEN=1234
            if len(words) != 2 or _try_parse_int(words[1]) is None:
                return None
            res = _try_parse_int(words[1])
            assert isinstance(res, int)  # for mypy
            return MeasLineData(res, None, meas_id, ts)
        elif words[0] == 'BG':
            # BG GIVEN=1234 TRUSTED=5678
            if len(words) != 3 or \
                    _try_parse_int(words[1]) is None or \
                    _try_parse_int(words[2]) is None:
                return None
            given = _try_parse_int(words[1])
            trusted = _try_parse_int(words[2])
            assert isinstance(given, int)  # for mypy
            assert isinstance(trusted, int)  # for mypy
            return MeasLineData(given, trusted, meas_id, ts)
        return None


class MeasLineBegin(MeasLine):
    def __init__(self, fp: str, *a, **kw):
        super().__init__(*a, **kw)
        self.relay_fp = fp

    def __str__(self):
        prefix = super().__str__()
        return prefix + ' BEGIN ' + self.relay_fp


class MeasLineEnd(MeasLine):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

    def __str__(self):
        prefix = super().__str__()
        return prefix + ' END'


class MeasLineData(MeasLine):
    def __init__(self, given_bw: int, trusted_bw: Optional[int], *a, **kw):
        super().__init__(*a, **kw)
        self.given_bw = given_bw
        self.trusted_bw = trusted_bw

    def is_bg(self) -> bool:
        return self.trusted_bw is not None

    def __str__(self):
        prefix = super().__str__()
        if self.trusted_bw is None:
            # result from a measurer
            return prefix + ' MEASR %d' % (self.given_bw,)
        # result from relay
        return prefix + ' BG %d %d' % (self.given_bw, self.trusted_bw)


def write_begin(fp: str, meas_id: int, ts: int):
    ''' Write a log line indicating the start of the given relay's measurement.

    :param fp: the fingerprint of the relay
    :param meas_id: the measurement ID
    :param ts: the unix timestamp at which the measurement began
    '''
    log.info(MeasLineBegin(fp, meas_id, ts))


def write_end(meas_id: int, ts: int):
    ''' Write a log line indicating the end of the given relay's measurement.

    :param meas_id: the measurement ID
    :param ts: the unix timestamp at which the measurement ended
    '''
    log.info(MeasLineEnd(meas_id, ts))


def write_meas(meas_id: int, ts: int, res: int):
    ''' Write a single per-second result from a measurer to our results.

    :param meas_id: the measurement ID
    :param ts: the unix timestamp at which the result came in
    :param res: the number of measured bytes
    '''
    log.info(MeasLineData(res, None, meas_id, ts))


def write_bg(meas_id: int, ts: int, given: int, trusted: int):
    ''' Write a single per-second report of bg traffic from the relay to our
    results.

    :param meas_id: the measurement ID
    :param ts: the unix timestamp at which the result came in
    :param given: the number of reported bg bytes
    :param trusted: the maximum given should be (from our perspective in this
        logging code, it's fine if given is bigger than trusted)
    '''
    log.info(MeasLineData(given, trusted, meas_id, ts))
