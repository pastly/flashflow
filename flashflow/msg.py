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
        if msg_type == MsgType.CONNECT_TO_RELAY:
            return ConnectToRelay.from_dict(j)
        elif msg_type == MsgType.CONNECTED_TO_RELAY:
            return ConnectedToRelay.from_dict(j)
        elif msg_type == MsgType.FAILURE:
            return Failure.from_dict(j)
        elif msg_type == MsgType.GO:
            return Go.from_dict(j)
        elif msg_type == MsgType.BW_REPORT:
            return BwReport.from_dict(j)
        assert None, 'Unknown/unimplemented MsgType %d' % (j['msg_type'],)


class ConnectToRelay(FFMsg):
    ''' Coordinator to Measurer message instructing them to connect to the
    specified relay.

    :param fp: the fingerprint of the relay to which the measurer should
        connect
    :param n_circs: the number of circuits they should open with the relay
    :param dur: the duration of the active measurement phase, in seconds
    '''
    msg_type = MsgType.CONNECT_TO_RELAY

    def __init__(self, fp: str, n_circs: int, dur: int):
        self.fp = fp
        self.n_circs = n_circs
        self.dur = dur

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'fp': self.fp,
            'n_circs': self.n_circs,
            'dur': self.dur
        }

    @staticmethod
    def from_dict(d: dict) -> 'ConnectToRelay':
        return ConnectToRelay(d['fp'], d['n_circs'], d['dur'])


class ConnectedToRelay(FFMsg):
    ''' Measurer to Coordinator message indicating whether or not they
    successfully connected to the relay.

    :param success: **True** if successful, else **False**
    :param orig: the original :class:`ConnectToRelay` message
    '''
    msg_type = MsgType.CONNECTED_TO_RELAY

    def __init__(self, success: bool, orig: ConnectToRelay):
        self.success = success
        self.orig = orig

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'success': self.success,
            'orig': self.orig._to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> 'ConnectedToRelay':
        return ConnectedToRelay(
            d['success'],
            ConnectToRelay.from_dict(d['orig']),
        )


class Failure(FFMsg):
    ''' Bidirectional message indicating the sending party has experienced some
    sort of error and the measurement should be halted.

    :param desc: human-meaningful description of what happened
    '''
    msg_type = MsgType.FAILURE

    def __init__(self, desc: str):
        self.desc = desc

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'desc': self.desc,
        }

    @staticmethod
    def from_dict(d: dict) -> 'Failure':
        return Failure(
            d['desc'],
        )


class Go(FFMsg):
    ''' Coordinator to Measurer message indicating its time to start the
    measurement '''
    msg_type = MsgType.GO

    def __init__(self):
        pass

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
        }

    @staticmethod
    def from_dict(d: dict) -> 'Go':
        return Go()


class BwReport(FFMsg):
    ''' Measurer to Coordinator message containing the number of sent and
    received bytes with the target relay in the last second.

    :param sent: number of sent bytes in the last second
    :param recv: number of received bytes in the last second
    '''
    msg_type = MsgType.BW_REPORT

    def __init__(self, sent: int, recv: int):
        self.sent = sent
        self.recv = recv

    def _to_dict(self) -> dict:
        return {
            'msg_type': self.msg_type.value,
            'sent': self.sent,
            'recv': self.recv,
        }

    @staticmethod
    def from_dict(d: dict) -> 'BwReport':
        return BwReport(d['sent'], d['recv'])
