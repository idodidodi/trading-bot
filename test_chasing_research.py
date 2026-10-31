from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from scanner import Candle
from chasing_research import read_series, outcome, detect_series
from platform_app import load_rules


class ResearchTests(unittest.TestCase):
    def test_csv_volume_preserved_open_excluded_and_duplicates_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bars.csv'
            p.write_text('time,open,high,low,close,Volume\n2026-10-01T00:00:00Z,100,101,99,100,123\n2026-10-01T00:01:00Z,100,101,99,100,456\n')
            from scanner import timestamp
            bars,volumes,audit=read_series(p,'1m',timestamp('2026-10-01T00:01:00Z'))
            self.assertEqual(len(bars),1);self.assertEqual(volumes[bars[0].start],123)
            self.assertEqual(audit['excluded_open_or_future'],1)
            with p.open('a') as f:f.write('2026-10-01T00:00:00Z,100,102,99,100,123\n')
            with self.assertRaises(ValueError):read_series(p,'1m',timestamp('2026-10-01T00:02:00Z'))

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

    def test_provisional_event_prefix_invariance(self):
        from test_scanner import fixture
        bars=fixture();volumes={b.start:100 for b in bars};rules=load_rules()
        whole=detect_series(bars,volumes,rules,'TEST','4h')
        prefix=detect_series(bars[:257],volumes,rules,'TEST','4h')
        self.assertEqual(prefix,[s for s in whole if s['confirmed_at']<=bars[256].end])
