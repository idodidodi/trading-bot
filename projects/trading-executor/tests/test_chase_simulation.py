import unittest
from dataclasses import replace
from candles import Candle
from chase_simulation import outcome

class SimulationTests(unittest.TestCase):
    def test_execution_never_uses_preentry_wick_and_gap_never_becomes_success(self):
        bars=[Candle(60000*i,60000*(i+1),100,101,99,100) for i in range(1,7)]
        bars[0]=replace(bars[0],high=500,low=1)
        starts=[b.start for b in bars]
        result=outcome(bars,starts,60000,360000,'bullish',10)
        self.assertEqual(result['entry_at'],120000)
        self.assertAlmostEqual(result['mae_pct'],1)
        self.assertAlmostEqual(result['net_return_pct'],-.1)
        broken=[b for b in bars if b.start!=180000]
        self.assertEqual(outcome(broken,[b.start for b in broken],60000,360000,'bullish',10)['status'],'incomplete_price_path')

    def test_empty_observation_is_not_an_executable_entry(self):
        bars=[Candle(i*60000,(i+1)*60000,100,100,100,100) for i in range(1,9)]
        starts=[b.start for b in bars]
        result=outcome(bars,starts,60000,360000,'bullish',0,empty=[120000,180000,360000,420000])
        self.assertEqual(result['entry_at'],240000)
        self.assertEqual(result['end_at'],480000)
        self.assertEqual(result['exit_delay_ms'],120000)
