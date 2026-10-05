import csv
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from platform_app import Store, load_rules
from scanner import Candle, check_candles, detect, indicators, prepare_store, scan_once


def fixture():
    closes = [100.] * 260
    closes[250:259] = [90, 92, 94, 96, 98, 100, 94, 96, 98]
    start = 1700000000000
    bars = [Candle(start + i * 14400000, start + (i + 1) * 14400000, p, p + 1, p - 1, p) for i, p in enumerate(closes)]
    bars[250] = replace(bars[250], low=80)
    bars[256] = replace(bars[256], low=79)
    return bars


class EngineTests(unittest.TestCase):
    def test_wilder_seed_and_population_bands(self):
        bars = [Candle(i + 1, i + 2, p, p, p, p) for i, p in enumerate([10, 11, 10, 12])]
        rules = load_rules() | {'bb_period': 3}
        rsi, lower, upper = indicators(bars, rules)
        self.assertIsNone(rsi[2])
        self.assertAlmostEqual(rsi[3], 75)
        self.assertAlmostEqual(lower[2], 31 / 3 - 2 * (2 / 9) ** .5)
        self.assertAlmostEqual(upper[2], 31 / 3 + 2 * (2 / 9) ** .5)

    def test_real_candle_divergence_confirmation_and_touch(self):
        bars = fixture()
        self.assertEqual(detect(bars[:258], load_rules(), 'test:EURUSD', '4h'), [])
        found = detect(bars[:259], load_rules(), 'test:EURUSD', '4h')
        self.assertEqual(len(found), 1)
        signal = found[0]
        self.assertEqual(signal['direction'], 'bullish')
        self.assertEqual(signal['spacing'], 6)
        self.assertEqual(signal['confirmed_at'], bars[258].end)
        self.assertGreater(signal['rsi2'], signal['rsi1'])
        # Widen the bands without changing the underlying divergence: no touch remains.
        self.assertEqual(detect(bars, load_rules() | {'bb_multiplier': 20}, 'test:EURUSD', '4h'), [])

    def test_bearish_symmetry_and_equal_pivots(self):
        bars = fixture()
        mirrored = [replace(c, open=200-c.open, close=200-c.close, high=200-c.low, low=200-c.high) for c in bars]
        signals = detect(mirrored, load_rules(), 'test:EURUSD', '4h')
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]['direction'], 'bearish')
        bars[257] = replace(bars[257], low=79)
        self.assertEqual(detect(bars, load_rules(), 'test:EURUSD', '4h'), [])

    def test_data_quality_and_open_candles(self):
        bars = fixture()
        self.assertEqual(len(check_candles(bars, bars[-1].start)), len(bars)-1)
        for bad in [bars + [bars[0]], [replace(bars[0], low=200)], [replace(bars[0], close=float('nan'))]]:
            with self.assertRaises(ValueError):
                check_candles(bad, bars[-1].end)

    def test_csv_scan_baseline_new_signal_and_restart_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'candles.csv'
            def write(bars):
                with path.open('w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['timestamp', 'closed_at', 'open', 'high', 'low', 'close'])
                    for c in bars:
                        stamps = [datetime.fromtimestamp(v/1000, timezone.utc).isoformat() for v in (c.start, c.end)]
                        writer.writerow([*stamps, c.open, c.high, c.low, c.close])
            store = Store(Path(folder) / 'db.sqlite3')
            prepare_store(store)
            config = {'timeframes': ['4h'], 'assets': [{'id': 'EURUSD', 'provider': 'csv', 'path': str(path)}]}
            bars = fixture()
            write(bars[:256])
            self.assertEqual(scan_once(store, config, load_rules(), bars[-1].end)[0]['status'], 'baseline set')
            self.assertEqual(store.rows(), [])
            write(bars)
            result = scan_once(store, config, load_rules(), bars[-1].end)
            self.assertEqual(result[0]['new_signals'], 1)
            self.assertEqual(len(store.rows()), 1)
            restored = Store(Path(folder) / 'db.sqlite3')
            self.assertEqual(scan_once(restored, config, load_rules(), bars[-1].end)[0]['new_signals'], 0)


if __name__ == '__main__':
    unittest.main()
