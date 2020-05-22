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
    FOO = 876234
    BAR = 2876


class FFMsg:
    def serialize(self) -> bytes:
        assert None, 'Child class did not implement its own serialize()'

    @staticmethod
    def deserialize(b: bytes) -> 'FFMsg':
        j = json.loads(b.decode('utf-8'))
        msg_type = MsgType(j['msg_type'])
        if msg_type == MsgType.FOO:
            return Foo.from_dict(j)
        elif msg_type == MsgType.BAR:
            return Bar.from_dict(j)
        assert None, 'Unknown/unimplemented MsgType %d' % (j['msg_type'],)


class Foo(FFMsg):
    msg_type = MsgType.FOO

    def __init__(self, i: int):
        self.i = i

    def serialize(self) -> bytes:
        return json.dumps({
            'msg_type': self.msg_type.value,
            'i': self.i,
        }).encode('utf-8')

    @staticmethod
    def from_dict(d: dict) -> 'Foo':
        return Foo(d['i'])


class Bar(FFMsg):
    msg_type = MsgType.BAR

    def __init__(self, s: str):
        self.s = s

    def serialize(self) -> bytes:
        return json.dumps({
            'msg_type': self.msg_type.value,
            's': self.s,
        }).encode('utf-8')

    @staticmethod
    def from_dict(d: dict) -> 'Bar':
        return Bar(d['s'])
