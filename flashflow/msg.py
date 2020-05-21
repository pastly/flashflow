'''
This is the collection of messages that FlashFlow entities can send to each
other.

All messages must be of the ABC FFMsg and provide its methods.
'''
from abc import ABC, abstractmethod
import json


class FFMsg(ABC):
    @abstractmethod
    def serialize(self) -> bytes: pass
    @staticmethod
    @abstractmethod
    def deserialize(bytes) -> 'FFMsg': pass


class Foo(FFMsg):
    def __init__(self, i: int, s: str, l: list, d: dict):  # noqa: #741
        self.i = i
        self.s = s
        self.l = l  # noqa: E741
        self.d = d

    def serialize(self) -> bytes:
        return json.dumps({
            'i': self.i,
            's': self.s,
            'l': self.l,
            'd': self.d,
        }).encode('utf-8')

    @staticmethod
    def deserialize(b: bytes) -> 'Foo':
        d = json.loads(b.decode('utf-8'))
        return Foo(d['i'], d['s'], d['l'], d['d'])
