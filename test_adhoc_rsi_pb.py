"""Offline checks for the on-demand screen; no database or provider calls."""
import contextlib
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rsi_pb
from test_rsi_pb import bars

spec = importlib.util.spec_from_file_location('adhoc_rsi_pb', Path(__file__).parent/'scripts/scan-rsi-pb-sp500.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class AdHocScreenTests(unittest.TestCase):
    def test_signal_evidence_recency_and_breach(self):
        candles=bars()
        candles[-1]=replace(candles[-1],low=candles[256].low-1)
        row, signals = scan.evaluate('TEST','4h',candles,rsi_pb.load_rules(),10,'sip')
        self.assertEqual(row['status'], 'qualified')
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]['direction'], 'bullish')
        self.assertEqual(signals[0]['pinbar_direction'], 'bearish')
        self.assertEqual(signals[0]['age_closed_candles'], 2)
        self.assertTrue(signals[0]['structural_breach'])
        row, signals = scan.evaluate('TEST','4h',bars(),rsi_pb.load_rules(),1,'sip')
        self.assertEqual(row['status'], 'no qualifying signal')
        self.assertFalse(signals)

    def test_insufficient_history_is_not_no_signal(self):
        row, signals = scan.evaluate('TEST','4h',bars()[:20],rsi_pb.load_rules(),10,'sip')
        self.assertEqual(row['status'], 'insufficient history')
        self.assertFalse(signals)

    def test_outputs_and_resume_reuse_original_snapshot_without_fetching(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); universe=root/'universe.json'; universe.write_text('["TEST"]')
            out=root/'report'
            args=['--timeframes','4h','--universe-file',str(universe),'--output',str(out)]
            with patch.dict(os.environ, ALPACA_API_KEY='offline-test', ALPACA_SECRET_KEY='offline-test'), contextlib.redirect_stdout(io.StringIO()):
                with patch.object(scan,'fetch_batch',return_value={'TEST':bars()}) as fetch:
                    self.assertEqual(scan.main(args),0)
                    fetch.assert_called_once()
                manifest=(out/'manifest.json').read_text()
                original=(out/'signals.json').read_text()
                with patch.object(scan,'fetch_batch',side_effect=AssertionError('Resume must use saved candles')):
                    self.assertEqual(scan.main(['--resume',str(out)]),0)
            self.assertEqual((out/'manifest.json').read_text(),manifest)
            self.assertEqual((out/'signals.json').read_text(),original)
            self.assertEqual(json.loads((out/'summary.json').read_text())['qualified_stocks'],1)
            self.assertEqual((out/'qualified-stocks.txt').read_text(),'TEST\n')
            self.assertTrue((out/'coverage.csv').exists())
            self.assertTrue((out/'report.md').exists())

    def test_provider_failure_is_explicit_and_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); universe=root/'universe.json'; universe.write_text('["TEST"]'); out=root/'report'
            with patch.dict(os.environ, ALPACA_API_KEY='offline-test', ALPACA_SECRET_KEY='offline-test'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with patch.object(scan,'fetch_batch',side_effect=ValueError('Provider unavailable')):
                    self.assertEqual(scan.main(['--timeframes','4h','--universe-file',str(universe),'--output',str(out)]),2)
                self.assertEqual(json.loads((out/'coverage.json').read_text())[0]['status'],'unavailable')
                with patch.object(scan,'fetch_batch',return_value={'TEST':bars()}):
                    self.assertEqual(scan.main(['--resume',str(out)]),0)
            self.assertEqual(json.loads((out/'coverage.json').read_text())[0]['status'],'qualified')

    def test_interruption_saves_manifest_and_unscanned_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); universe=root/'universe.json'; universe.write_text('["TEST"]'); out=root/'report'
            with patch.dict(os.environ, ALPACA_API_KEY='offline-test', ALPACA_SECRET_KEY='offline-test'), patch.object(scan,'fetch_batch',side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(scan.main(['--timeframes','4h','--universe-file',str(universe),'--output',str(out)]),130)
            self.assertEqual(json.loads((out/'coverage.json').read_text())[0]['status'],'not scanned')
            self.assertTrue((out/'manifest.json').exists())


if __name__=='__main__':
    unittest.main()
