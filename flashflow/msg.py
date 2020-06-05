'''
This is the collection of messages that FlashFlow entities can send to each
other.
'''
import enum
import json


class MsgType(enum.Enum):
    ''' Message types. These are used so that the parent FFMsg class can tell
    which type of Msg subclass it is attempting to deserialize and pass off the
    data to it to finish deserialization.

    While I would normally use enum.auto() for these since I don't want to
    allow implicit assumptions about each variant's value and their relation to
    each other. However in the off chance a version X coordinator tries to talk
    to version Y measurer with different values for the variants, setting
    static and explicit values helps preserve their ability to communicate.

    To prevent devs from being tempted to think the variants' values imply some
    sort of relationship or order, set them to random ints.
    '''
    CONNECT_TO_RELAY = 357
    CONNECTED_TO_RELAY = 78612
    FAILURE = 62424
    GO = 1089
    BW_REPORT = -289


class FFMsg:
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
    ''' Coordinator --> Measurer message instructing them to connect to the
    specified relay. This message contains

    - the fingerprint of the relay the measurer should connect to
    - the number of circuits they should open with the relay
    - the duration of the active measurement phase, in seconds
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
    ''' Measurer --> Coordinator message indicating whether or not they
    successfully connected to the relay. This message contains

    - a bool, indicating success/failure
    - the original ConnectToRelay message
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
    ''' Coordinator <--> Measurer message indicating the sending party has
    experienced some sort of error and the measurement should be halted.

    - a str, containing a human-meaningful description of what happened
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
    ''' Coordinator --> Measurer message indicating its time to start the
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
    ''' Measurer --> Coordinator message containing the number of sent and
    received bytes with the target relay in the last second '''
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
