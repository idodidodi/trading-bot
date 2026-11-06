import json
from pathlib import Path
import tempfile
import unittest
from compare_chase_variants import compare


class ComparisonTests(unittest.TestCase):
    def setup_reports(self,folder):
        paths=[Path(folder)/'baseline',Path(folder)/'candidate']
        config=dict.fromkeys(('symbol','cutoff','stop_pct','roundtrip_cost_bps','start_at','end_at','horizon_4h_bars','rules','entry','exit'),None)
        for p in paths:
            p.mkdir()
            (p/'replay.json').write_text(json.dumps(dict(config=config,audit={'1m':{'sha256':'original'}})))
            (p/'cases.json').write_text('[]')
        return paths

    def test_different_cost_assumptions_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=self.setup_reports(folder)
            report=json.loads((b/'replay.json').read_text());report['config']['roundtrip_cost_bps']=20
            (b/'replay.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'roundtrip_cost_bps'):compare(a,b,Path(folder)/'out')

    def test_different_candle_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            a,b=self.setup_reports(folder)
            report=json.loads((b/'replay.json').read_text());report['audit']['1m']['sha256']='changed'
            (b/'replay.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'Datasets differ'):compare(a,b,Path(folder)/'out')
