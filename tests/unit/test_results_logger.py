import unittest
from flashflow.results_logger import MeasLine, MeasLineBegin, MeasLineEnd,\
    MeasLineData

FP = 'relay1'
MEAS_ID = 43987234
TS = 298743256


class TestMeasLineBad(unittest.TestCase):
    ''' Test all the ways a measurement line can be malformed '''
    def test_empty(self):
        assert MeasLine.parse('') is None

    def test_empty_whitespace(self):
        assert MeasLine.parse('      ') is None

    def test_too_few_words(self):
        s = ' '.join(['a'] * 3)
        assert MeasLine.parse(s) is None

    def test_too_many_words(self):
        s = ' '.join(['a'] * 7)
        assert MeasLine.parse(s) is None

    def test_bad_meas_id(self):
        s = 'bad_meas_id 1 BEGIN %s' % (FP,)
        assert MeasLine.parse(s) is None

    def test_bad_ts(self):
        s = '%d bad_ts BEGIN %s' % (MEAS_ID, FP)
        assert MeasLine.parse(s) is None

    def test_bad_third_word(self):
        s = '%d %d NOT_A_REAL_WORD %s' % (MEAS_ID, TS, FP)
        assert MeasLine.parse(s) is None

    def test_too_few_bg(self):
        s = '%d %d BG %d' % (MEAS_ID, TS, 420)
        assert MeasLine.parse(s) is None

    def test_too_many_measr(self):
        s = '%d %d MEASR %d %d' % (MEAS_ID, TS, 420, 69)
        assert MeasLine.parse(s) is None


class TestMeasLineGood(unittest.TestCase):
    def test_begin(self):
        s = '%d %d BEGIN %s' % (MEAS_ID, TS, FP)
        out = MeasLine.parse(s)
        assert isinstance(out, MeasLineBegin)
        assert out.relay_fp == FP
        assert out.meas_id == MEAS_ID
        assert out.ts == TS

    def test_end(self):
        s = '%d %d END' % (MEAS_ID, TS)
        out = MeasLine.parse(s)
        assert isinstance(out, MeasLineEnd)
        assert out.meas_id == MEAS_ID
        assert out.ts == TS

    def test_data_measr(self):
        given = 42069
        s = '%d %d MEASR %d' % (MEAS_ID, TS, given)
        out = MeasLine.parse(s)
        assert isinstance(out, MeasLineData)
        assert out.meas_id == MEAS_ID
        assert out.ts == TS
        assert out.given_bw == given
        assert out.trusted_bw is None

    def test_data_bg(self):
        given = 420
        trusted = 69
        s = '%d %d BG %d %d' % (MEAS_ID, TS, given, trusted)
        out = MeasLine.parse(s)
        assert isinstance(out, MeasLineData)
        assert out.meas_id == MEAS_ID
        assert out.ts == TS
        assert out.given_bw == given
        assert out.trusted_bw == trusted

    def test_comment_nonsense(self):
        s = '      # foo    '
        assert MeasLine.parse(s) is None

    def test_comment_otherwise_valid(self):
        prefixes = ['#', '# ', ' #', ' # ']
        for prefix in prefixes:
            s = prefix + '%s %d %d BEGIN' % (FP, MEAS_ID, TS)
            assert MeasLine.parse(s) is None
