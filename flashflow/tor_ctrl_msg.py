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
    def __init__(self, nick_fp: str):
        self.nick_fp = nick_fp

    def __str__(self) -> str:
        return 'COORD_START_MEAS ' + self.nick_fp


class MeasrStartMeas(TorCtrlMsg):
    ''' Message sent from a FlashFlow measurer to its Tor client to instruct it
    to open circuits with the given relay as part of the pre-measurement
    process. '''
    def __init__(self, nick_fp: str, n_circs: int):
        self.nick_fp = nick_fp
        self.n_circs = n_circs

    def __str__(self) -> str:
        return 'MEASR_START_MEAS %s %d' % (self.nick_fp, self.n_circs)
