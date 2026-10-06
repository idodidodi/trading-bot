import csv
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error

from platform_app import Store, load_rules
from scanner import Candle, check_candles, detect, indicators, prepare_store, scan_once, fetch_twelve_data, scanner_worker, normalize_provider_candles


def fixture():
    closes = [100.] * 260
    closes[250:259] = [90, 92, 94, 96, 98, 100, 94, 96, 98]
    start = 1700000000000
    bars = [Candle(start + i * 14400000, start + (i + 1) * 14400000, p, p + 1, p - 1, p) for i, p in enumerate(closes)]
    bars[250] = replace(bars[250], low=80)
    bars[256] = replace(bars[256], low=79)
    return bars


class EngineTests(unittest.TestCase):
    def test_provider_revisions_and_short_native_interval_boundaries(self):
        bars = [Candle(1000, 5000, 10, 12, 9, 11), Candle(1000, 5000, 10, 12, 9, 12),
                Candle(4000, 8000, 12, 13, 11, 12)]
        result = normalize_provider_candles(bars)
        self.assertEqual(result, [replace(bars[1], end=4000), bars[2]])
        self.assertEqual(len(result.notes), 2)
        self.assertEqual(check_candles(result, 6000), result[:1])
        # Generic CSV validation still rejects duplicates and overlaps.
        with self.assertRaises(ValueError):
            check_candles(bars, 10000)

    def test_live_response_checks_feed_and_excludes_open_candles(self):
        import io
        import json
        asset = dict(id='NVDA', symbol='NVDA', exchange='NASDAQ', feed_confirmed=True)
        data = dict(meta=dict(symbol='NVDA', interval='4h', exchange='NASDAQ'), values=[
            dict(datetime='2026-10-06 00:00:00', open='10', high='12', low='9', close='11'),
            dict(datetime='2026-10-06 04:00:00', open='11', high='12', low='10', close='12')])
        with patch.dict('os.environ', {'TWELVE_DATA_API_KEY': 'secret'}), patch('scanner.urllib.request.urlopen', return_value=io.StringIO(json.dumps(data))):
            candles = fetch_twelve_data(asset, '4h')
        now = int(datetime(2026, 10, 6, 6, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(check_candles(candles, now), candles[:1])
        data['meta']['exchange'] = 'OTHER'
        with patch.dict('os.environ', {'TWELVE_DATA_API_KEY': 'secret'}), patch('scanner.urllib.request.urlopen', return_value=io.StringIO(json.dumps(data))):
            with self.assertRaisesRegex(ValueError, 'different exchange'):
                fetch_twelve_data(asset, '4h')

    def test_live_provider_errors_are_actionable_without_credentials(self):
        asset = dict(id='EURUSD', symbol='EUR/USD', feed_confirmed=True)
        for code, expected in [(403, 'plan'), (404, 'symbol'), (429, 'quota')]:
            error = urllib.error.HTTPError('https://example.test/?apikey=secret', code, 'secret', {}, None)
            with patch.dict('os.environ', {'TWELVE_DATA_API_KEY': 'secret'}), patch('scanner.urllib.request.urlopen', side_effect=error):
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    fetch_twelve_data(asset, '4h')
                self.assertNotIn('secret', str(caught.exception))

    def test_live_requests_are_paced_and_first_scan_does_not_alert(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            config = dict(timeframes=['4h'], request_spacing_seconds=8, assets=[
                dict(id=s, provider='twelvedata', symbol=s, feed_confirmed=True) for s in ['EUR/USD', 'BTC/USD']])
            with patch('scanner.fetch_twelve_data', return_value=fixture()), patch('scanner.time.monotonic', return_value=10), patch('scanner.time.sleep') as sleep:
                result = scan_once(store, config, load_rules(), fixture()[-1].end)
            sleep.assert_called_once_with(8)
            self.assertTrue(all(r['status'] == 'baseline set' for r in result))
            self.assertEqual(store.rows(), [])

    def test_worker_uses_managed_poll_interval(self):
        from managed_assets import ensure
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3')
            config = dict(assets=[], timeframes=['4h'], poll_seconds=3600)
            ensure(store, config)
            stop = Mock(); stop.is_set.side_effect = [False, False, True]
            with patch('scanner.scan_once', return_value=[]):
                scanner_worker(store, config | {'poll_seconds': 300}, load_rules(), stop)
            stop.wait.assert_called_once_with(3600)

    def test_worker_wakes_after_next_candle_close(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3')
            config = dict(assets=[], timeframes=['4h'], poll_seconds=14400)
            stop = Mock(); stop.is_set.side_effect = [False, False, True]
            with patch('scanner.scan_once', return_value=[dict(status='scanned', next_close_at=2000000)]), patch('scanner.time.time', return_value=1900):
                scanner_worker(store, config, load_rules(), stop)
            stop.wait.assert_called_once_with(115)
            import json
            with store.connect() as db:
                schedule = json.loads(db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()[0])
            self.assertEqual(schedule['next_scan_at'], 2015000)
            self.assertEqual(schedule['phase'], 'waiting')

    def test_provisional_at_pivot_close_without_future_candles(self):
        from platform_app import validate_signal, message
        rules = load_rules()
        for bearish in (False, True):
            bars = fixture()
            if bearish:
                bars = [replace(c, open=200-c.open, close=200-c.close, high=200-c.low, low=200-c.high) for c in bars]
            found = detect(bars[:257], rules, 'test:EURUSD', '4h', provisional=True)
            self.assertEqual(len(found), 1)
            signal = validate_signal(found[0], rules)
            self.assertEqual(signal['pivot1'], bars[250].start)
            self.assertEqual(signal['pivot2'], bars[256].start)
            self.assertEqual(signal['pivot_closed_at'], bars[256].end)
            self.assertEqual(signal['signal_status'], 'provisional')
            self.assertIn('Second pivot closed:', message(signal))
            self.assertNotIn('Confirmed:', message(signal))
            self.assertEqual(detect(check_candles(bars, bars[256].end-1), rules, 'test:EURUSD', '4h', provisional=True), [])
            # Later invalidation must not erase an alert already knowable at P2.
            bars[257] = replace(bars[257], **({'high': bars[256].high+1} if bearish else {'low': bars[256].low-1}))
            replay = detect(bars, rules, 'test:EURUSD', '4h', provisional=True)
            self.assertIn(signal, replay)
            self.assertFalse(any(s['pivot2'] == bars[256].start for s in detect(bars, rules, 'test:EURUSD', '4h')))

    def test_provisional_requires_strict_left_and_band_touch(self):
        bars = fixture(); rules = load_rules()
        self.assertEqual(detect(bars, rules | {'bb_multiplier': 20}, 'test:EURUSD', '4h', provisional=True), [])
        bars[255] = replace(bars[255], low=79)
        found = detect(bars, rules, 'test:EURUSD', '4h', provisional=True)
        self.assertFalse(any(s['pivot2'] == bars[256].start for s in found))

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
        self.assertEqual(detect(bars[:257], load_rules(), 'test:EURUSD', '4h'), [])
        found = detect(bars[:258], load_rules(), 'test:EURUSD', '4h')
        self.assertEqual(len(found), 1)
        signal = found[0]
        self.assertEqual(signal['direction'], 'bullish')
        self.assertEqual(signal['spacing'], 6)
        self.assertEqual(signal['confirmed_at'], bars[257].end)
        self.assertGreater(signal['rsi2'], signal['rsi1'])
        # Widen the bands without changing the underlying divergence: no touch remains.
        self.assertEqual(detect(bars, load_rules() | {'bb_multiplier': 20}, 'test:EURUSD', '4h'), [])

    def test_one_right_confirmation_at_11_israel_time(self):
        from platform_app import message
        from scanner import timestamp
        rules = load_rules()
        self.assertEqual(rules['pivot_right'], 1)
        bars = fixture()
        pivot_start = timestamp('2026-10-06T03:00:00+03:00')
        bars = [replace(c, start=pivot_start+(i-256)*14400000,
                        end=pivot_start+(i-255)*14400000,
                        open=200-c.open, close=200-c.close,
                        high=200-c.low, low=200-c.high) for i,c in enumerate(bars)]
        # No alert at the pivot close or while the following candle is open.
        for cutoff in ('2026-10-06T07:00:00+03:00', '2026-10-06T10:59:59+03:00'):
            self.assertEqual(detect(check_candles(bars,timestamp(cutoff)),rules,'test:NEAR/USD','4h'), [])
        found = detect(check_candles(bars,timestamp('2026-10-06T11:00:00+03:00')),rules,'test:NEAR/USD','4h')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['confirmed_at'], timestamp('2026-10-06T11:00:00+03:00'))
        self.assertIn('11:00:00+03:00', message(found[0]))
        self.assertNotIn('provisional', message(found[0]))
        # A higher high on the following candle rejects this pivot pair.
        bars[257] = replace(bars[257], high=bars[256].high+1)
        self.assertEqual(detect(bars[:258],rules,'test:NEAR/USD','4h'), [])

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
            write(bars[:257])
            self.assertEqual(scan_once(store, config, load_rules(), bars[-1].end)[0]['new_signals'], 0)
            write(bars[:258])
            result = scan_once(store, config, load_rules(), bars[-1].end)
            self.assertEqual(result[0]['new_signals'], 1)
            self.assertEqual(len(store.rows()), 1)
            write(bars)  # A second following candle must not send a duplicate alert.
            restored = Store(Path(folder) / 'db.sqlite3')
            self.assertEqual(scan_once(restored, config, load_rules(), bars[-1].end)[0]['new_signals'], 0)


if __name__ == '__main__':
    unittest.main()
