import unittest
from dataclasses import replace
from scanner import Candle
from near_rsi_atr import annotation, allows, VERSION


class ATRTests(unittest.TestCase):
    def bars(self):
        return [Candle(i*300000,(i+1)*300000,100,101,99,100) for i in range(25)]

    def evidence(self,bars,index=20):
        return annotation({'id':'NEARUSD'},bars,dict(timeframe='5m',pivot2=bars[index].start,confirmed_at=bars[index].end))['near_rsi_atr']

    def test_same_or_next_candle_only_and_exact_threshold(self):
        for offset,qualifies in ((0,True),(1,True),(2,False)):
            with self.subTest(offset=offset):
                bars=self.bars();i=20-offset;bars[i]=replace(bars[i],high=103,low=97)
                result=self.evidence(bars)
                self.assertEqual(result['qualifies'],qualifies)
                if qualifies:
                    self.assertEqual(result['atr_before'],2)
                    self.assertEqual(result['range_atr'],3)
                    self.assertEqual(result['candles_before'],offset)
        bars=self.bars();bars[20]=replace(bars[20],high=102.999,low=97)
        self.assertFalse(self.evidence(bars)['qualifies'])

    def test_wilder_smoothing_uses_true_range_not_just_range(self):
        bars=self.bars();bars[14]=replace(bars[14],open=103,high=104,low=102,close=103)
        bars[15]=replace(bars[15],high=106,low=98)
        self.assertAlmostEqual(self.evidence(bars,15)['atr_before'],(13*2+4)/14)

    def test_no_future_data_and_insufficient_or_zero_history(self):
        bars=self.bars();bars[21]=replace(bars[21],high=150,low=50)
        self.assertEqual(self.evidence(bars),self.evidence(bars[:21]))
        bars[20]=replace(bars[20],high=103,low=97)
        self.assertFalse(self.evidence(bars[10:],10)['qualifies'])
        flat=[replace(c,high=100,low=100) for c in bars];flat[20]=bars[20]
        self.assertFalse(self.evidence(flat)['qualifies'])

    def test_missing_interval_does_not_allow_previous_candle_exception(self):
        bars=self.bars();bars[19]=replace(bars[19],high=103,low=97)
        bars[20]=replace(bars[20],start=bars[20].start+300000,end=bars[20].end+300000)
        self.assertFalse(self.evidence(bars[:21])['qualifies'])

    def test_scope_and_missing_old_queued_metadata_fail_closed(self):
        for kind in ('regular','hidden'):
            for direction in ('bullish','bearish'):
                payload=dict(asset='NEARUSD',timeframe='5m',divergence_type=kind,direction=direction)
                self.assertFalse(allows(payload))
                self.assertTrue(allows(payload|{'near_rsi_atr':{'version':VERSION,'qualifies':True}}))
        self.assertTrue(allows({'asset':'NEARUSD','timeframe':'15m'}))
        self.assertTrue(allows({'asset':'SOLUSD','timeframe':'5m'}))
