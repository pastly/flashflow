''' Messages that FlashFlow coordinators and measurers can send to each other.

Messages serialize to JSON. Each contains a :class:`MsgType` integer, which is
**the** way the message type is determined.  Parties are trusted to not be
malicious, so relatively little is done to verify that messages are
well-formed.

Usage
=====

To **create** a new message, create it directly with its constructor. E.g.
:func:`ConnectToRelay`.

To **send** a message, call its :func:`FFMsg.serialize` method and write the
bytes you get out to the stream.

To **receive** a message, pass the :class:`bytes` to the static method
:func:`FFMsg.deserialize`

Example::

    # "Send" message to measurer
    m_send = ConnectToRelay('DEADBEEF', 80, 30)
    print(m_send.serialize())  # outputs JSON byte string
    # "Receive" message from coordinator
    b = b"{'msg_type': -289, 'sent': 16666, 'recv': 15555}"
    m_recv = FFMsg.deserialize(b)  # Returns BwReport object

Adding new messages
===================

1. Define its :class:`MsgType` with a random integer
2. Check for the new variant in :func:`FFMsg.deserialize`
3. Define the new class, ensuring you
    1. Set ``msg_type`` to the new :class:`MsgType` variant
    2. Define a ``_to_dict()`` method that takes ``self`` and returns a
       ``dict``
    3. Define a ``from_dict()`` method that takes a ``dict`` and returns a valid
       instance of the new message type
'''  # noqa: E501
import enum
import json
from typing import NoReturn, Optional


class MsgType(enum.Enum):
    ''' Message types used so that the parent :class:`FFMsg` class can tell
    which type of JSON it is looking at and pass deserialization work off to
    the appropriate subclass.

    I would normally use :py:func:`enum.auto` for these since I don't want to
    allow implicit assumptions about each variant's value and their relation to
    each other. However in the off chance a version ``X`` coordinator tries to
    talk to version ``Y`` measurer with different values for the variants,
    setting static and explicit values helps preserve their ability to
    communicate.
    '''
    CONNECT_TO_RELAY = 357
    CONNECTED_TO_RELAY = 78612
    FAILURE = 62424
    GO = 1089
    BW_REPORT = -289


def _assert_never(x: NoReturn) -> NoReturn:
    ''' Helper that always throws an assert.

    Used to help mypy ensure that all variants of an enum are covered.
    https://github.com/python/mypy/issues/6366#issuecomment-560369716
    '''
    assert False, "Unhandled type: {}".format(type(x).__name__)


class FailCode(enum.Enum):
    ''' :class:`Failure` codes.

    Those prefixed with ``M_`` can only originate
    at a measurer. Those prefixed with ``C_`` can only originate at a
    coordinator. All others can originate from anywhere. '''
    #: A Tor client was unable to launch the required circuit(s) with the relay
    LAUNCH_CIRCS = enum.auto()
    #: A Tor client sent its controller a response it couldn't understand
    MALFORMED_TOR_RESP = enum.auto()
    #: Measurer cannot start a new measurement with the given ID because it
    #: already has one with the same ID
    M_DUPE_MEAS_ID = enum.auto()
    #: Measurer given a command containing an unknown measurement ID
    M_UNKNOWN_MEAS_ID = enum.auto()
    #: Measurer's Tor client didn't accept command to start active measurement
    M_START_ACTIVE_MEAS = enum.auto()
    #: Coordinator's Tor client didn't accept command to start active
    #: measurement
    C_START_ACTIVE_MEAS = enum.auto()

    def __str__(self) -> str:
        if self is FailCode.LAUNCH_CIRCS:
            return 'Unable to launch circuit(s)'
        elif self is FailCode.MALFORMED_TOR_RESP:
            return 'Malformed response from Tor client'
        elif self is FailCode.M_DUPE_MEAS_ID:
            return 'Measurer already has measurement with given ID'
        elif self is FailCode.M_UNKNOWN_MEAS_ID:
            return 'Measurer does not have measurement with given ID'
        elif self is FailCode.M_START_ACTIVE_MEAS:
            return 'Measurer unable to tell Tor client to start active ' \
                'measurement'
        elif self is FailCode.C_START_ACTIVE_MEAS:
            return 'Coordinator unable to tell Tor client to start active ' \
                'measurement'
        else:
            _assert_never(self)


class FFMsg:
    ''' Base class for all messages that FlashFlow coordinators and measurers
    can send to each other.

    See the module-level documentation for more information.
    '''
    def serialize(self) -> bytes:
        return json.dumps(self._to_dict()).encode('utf-8')

    def _to_dict(self):
        assert None, 'Child FFMsg type did not implement _to_dict()'

    @staticmethod
    def deserialize(b: bytes) -> 'FFMsg':
        j = json.loads(b.decode('utf-8'))
        msg_type = MsgType(j['msg_type'])
        if msg_type is MsgType.CONNECT_TO_RELAY:
            return ConnectToRelay.from_dict(j)
        elif msg_type is MsgType.CONNECTED_TO_RELAY:
            return ConnectedToRelay.from_dict(j)
        elif msg_type is MsgType.FAILURE:
            return Failure.from_dict(j)
        elif msg_type is MsgType.GO:
            return Go.from_dict(j)
        elif msg_type is MsgType.BW_REPORT:
            return BwReport.from_dict(j)
        else:
            _assert_never(msg_type)


class ConnectToRelay(FFMsg):
    ''' Coordinator to Measurer message instructing them to connect to the
    specified relay.

    :param meas_id: the ID to assign to this measurement
    :param fp: the fingerprint of the relay to which the measurer should
        connect
    :param n_circs: the number of circuits they should open with the relay
    :param dur: the duration of the active measurement phase, in seconds
    '''
    msg_type = MsgType.CONNECT_TO_RELAY

    def __init__(self, meas_id: int, fp: str, n_circs: int, dur: int):
        self.meas_id = meas_id
        self.fp = fp
        self.n_circs = n_circs
        self.dur = dur

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'meas_id': self.meas_id,
            'fp': self.fp,
            'n_circs': self.n_circs,
            'dur': self.dur
        }

    @staticmethod
    def from_dict(d: dict) -> 'ConnectToRelay':
        return ConnectToRelay(d['meas_id'], d['fp'], d['n_circs'], d['dur'])


class ConnectedToRelay(FFMsg):
    ''' Measurer to Coordinator message indicating the have
    successfully connected to the relay. Non-success is signed with a
    :class:`Failure` message

    :param orig: the original :class:`ConnectToRelay` message
    '''
    msg_type = MsgType.CONNECTED_TO_RELAY

    def __init__(self, orig: ConnectToRelay):
        self.orig = orig

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'orig': self.orig._to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> 'ConnectedToRelay':
        return ConnectedToRelay(ConnectToRelay.from_dict(d['orig']))


class Failure(FFMsg):
    ''' Bidirectional message indicating the sending party has experienced some
    sort of error and the measurement should be halted.

    :param meas_id: the ID of the measurement to which this applies, or
        ``None`` if the failure is not specific to a measurement
    :param code: the :class:`FailCode`
    :param info: optional, any arbitrary extra information already stringified
    '''
    msg_type = MsgType.FAILURE

    def __init__(
            self, code: FailCode, meas_id: Optional[int],
            extra_info: Optional[str] = None):
        self.code = code
        self.meas_id = meas_id
        self.extra_info = extra_info

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'code': self.code,
            'meas_id': self.meas_id,
            'extra_info': self.extra_info,
        }

    @staticmethod
    def from_dict(d: dict) -> 'Failure':
        return Failure(
            d['code'],
            d['meas_id'],
            extra_info=d['extra_info'],
        )

    def __str__(self) -> str:
        prefix = str(self.code)
        if self.meas_id is not None:
            prefix += ' (meas %d)' % (self.meas_id,)
        if self.extra_info is not None:
            prefix += ': %s' % (str(self.extra_info),)
        return prefix


class Go(FFMsg):
    ''' Coordinator to Measurer message indicating its time to start the
    measurement

    :param meas_id: the ID of the measurement to which this applies
    '''
    msg_type = MsgType.GO

    def __init__(self, meas_id: int):
        self.meas_id = meas_id

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'meas_id': self.meas_id
        }

    @staticmethod
    def from_dict(d: dict) -> 'Go':
        return Go(d['meas_id'])


class BwReport(FFMsg):
    ''' Measurer to Coordinator message containing the number of sent and
    received bytes with the target relay in the last second.

    :param meas_id: the ID of the measurement to which this applies
    :param ts: the seconds since the unix epoch for which this
        :class:`BwReport` applies
    :param sent: number of sent bytes in the last second
    :param recv: number of received bytes in the last second
    '''
    msg_type = MsgType.BW_REPORT

    def __init__(self, meas_id: int, ts: float, sent: int, recv: int):
        self.meas_id = meas_id
        self.ts = ts
        self.sent = sent
        self.recv = recv

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'meas_id': self.meas_id,
            'ts': self.ts,
            'sent': self.sent,
            'recv': self.recv,
        }

    @staticmethod
    def from_dict(d: dict) -> 'BwReport':
        return BwReport(d['meas_id'], d['ts'], d['sent'], d['recv'])
