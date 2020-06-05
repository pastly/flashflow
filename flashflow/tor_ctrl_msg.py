'''
This is the collection of Tor Control Port commands that now exist in Tor
thanks to Flashflow but for which Stem does not yet implement a better
interface.

All messages must be of the ABC TorCtrlMsg and provide its methods. Most
notably this means the __str__ method, which is used to turn the message into a
string for sending to the ControlPort.

New messages are free to have a more complex construction process than simply
passing in all necessary information at __init__ time. But at the end of the
day they need to implement all TorCtrlMsg's methods and have a __str__ that
turns them into a one-line string for sending to Tor.
'''
from abc import ABC, abstractmethod


class TorCtrlMsg(ABC):
    @abstractmethod
    def __str__(self) -> str: pass


class CoordStartMeas(TorCtrlMsg):
    ''' Message sent from a FlashFlow coordinator to its Tor client to instruct
    it to start the measurement process with the given relay, identified by its
    nickname or fingerprint (you should always use fingerprint in practice).
    '''
    def __init__(self, nick_fp: str, dur: int):
        self.nick_fp = nick_fp
        self.dur = dur

    def __str__(self) -> str:
        return 'COORD_START_MEAS %s %d' % (self.nick_fp, self.dur)


class MeasrStartMeas(TorCtrlMsg):
    ''' Dual-purpose message sent from a FlashFlow measurer to its Tor client.

    First it's used to tell it to open circuits with the given relay as part of
    the pre-measurement process.

    Later, when everything is setup and ready to go, it is used to tell the tor
    client to actually start sending measurement traffic with the relay.  '''
    def __init__(self, nick_fp: str, n_circs: int, dur: int):
        self.nick_fp = nick_fp
        self.n_circs = n_circs
        self.dur = dur

    def __str__(self) -> str:
        return 'MEASR_START_MEAS %s %d %d' %\
            (self.nick_fp, self.n_circs, self.dur)
