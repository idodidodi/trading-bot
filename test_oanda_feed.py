from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error

from managed_assets import ensure, for_timeframe, get, validate_asset
from oanda_feed import available_forex, candle_end, fetch_oanda, request_json
from platform_app import Store, load_rules
from scanner import check_candles, interval_end, prepare_store, scan_once, scanner_worker, timestamp
from test_scanner import fixture


class OandaTests(unittest.TestCase):
    def setUp(self):
        self.asset = dict(id='EURUSD', symbol='EUR/USD', provider='oanda', exchange='OANDA',
                          enabled=True, feed_confirmed=True, timeframes=['1h'])

    def data(self, granularity='H1'):
        return dict(instrument='EUR_USD', granularity=granularity, candles=[
            dict(time='2026-10-06T10:00:00.000000000Z', complete=True,
                 mid=dict(o='1.1', h='1.2', l='1.0', c='1.15')),
            dict(time='2026-10-06T11:00:00.000000000Z', complete=False)])

    def test_read_only_request_uses_bearer_header_and_exact_metadata(self):
        with patch.dict('os.environ', dict(OANDA_API_TOKEN='private-token', OANDA_ENVIRONMENT='practice')):
            with patch('oanda_feed.urllib.request.urlopen', return_value=io.StringIO(json.dumps(self.data()))) as request:
                bars = fetch_oanda(self.asset, '1h')
        req = request.call_args.args[0]
        self.assertTrue(req.full_url.startswith('https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/candles?'))
        self.assertEqual(req.get_method(), 'GET')
        self.assertNotIn('private-token', req.full_url)
        self.assertEqual(req.get_header('Authorization'), 'Bearer private-token')
        self.assertIn('granularity=H1', req.full_url)
        self.assertIn('price=M', req.full_url)
        self.assertIn('smooth=false', req.full_url)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].end - bars[0].start, 3600000)
        self.assertEqual(bars.next_close_at, timestamp('2026-10-06T12:00:00Z'))
        with patch('oanda_feed.request_json', return_value=self.data('H4')):
            with self.assertRaisesRegex(ValueError, 'different instrument or candle granularity'):
                fetch_oanda(self.asset, '1h')

    def test_completion_flags_duplicates_bad_prices_and_forex_validation(self):
        data = self.data(); data['candles'][0]['complete'] = 'true'
        with patch('oanda_feed.request_json', return_value=data):
            with self.assertRaisesRegex(ValueError, 'completion flag'):
                fetch_oanda(self.asset, '1h')
        data = self.data(); data['candles'][1]['time'] = data['candles'][0]['time']
        with patch('oanda_feed.request_json', return_value=data):
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                fetch_oanda(self.asset, '1h')
        data = self.data(); data['candles'][0]['mid']['c'] = 'private payload'
        with patch('oanda_feed.request_json', return_value=data):
            with self.assertRaisesRegex(ValueError, 'malformed') as caught:
                fetch_oanda(self.asset, '1h')
            self.assertNotIn('private payload', str(caught.exception))
        with self.assertRaisesRegex(ValueError, 'exact BASE/QUOTE'):
            fetch_oanda(self.asset | dict(symbol='DAX'), 'daily')

    def test_new_york_daily_and_weekly_boundaries_respect_dst(self):
        start = datetime(2026, 3, 7, 22, tzinfo=timezone.utc)  # 17:00 New York before DST.
        end = candle_end(start, 'daily')
        self.assertEqual(end, datetime(2026, 3, 8, 21, tzinfo=timezone.utc))
        self.assertEqual((end - start).total_seconds(), 23 * 3600)
        weekly = datetime(2026, 3, 6, 22, tzinfo=timezone.utc)
        self.assertEqual(candle_end(weekly, 'weekly'), datetime(2026, 3, 13, 21, tzinfo=timezone.utc))
        month = datetime(2026, 2, 1, 22, tzinfo=timezone.utc)
        self.assertEqual(candle_end(month, 'monthly'), datetime(2026, 3, 1, 22, tzinfo=timezone.utc))

    def test_market_closure_does_not_stretch_hourly_candle(self):
        data = self.data(); data['candles'][1]['time'] = '2026-10-09T11:00:00Z'
        with patch('oanda_feed.request_json', return_value=data):
            bars = fetch_oanda(self.asset, '1h')
        self.assertEqual(bars[0].end - bars[0].start, 3600000)

    def test_errors_are_sanitized_and_hosts_are_fixed(self):
        for code, message in [(401, 'token'), (403, 'access'), (404, 'unavailable'), (429, 'rate limit')]:
            error = urllib.error.HTTPError('https://example.test/private-token', code, 'private-token', {}, None)
            with patch.dict('os.environ', dict(OANDA_API_TOKEN='private-token', OANDA_ENVIRONMENT='live')):
                with patch('oanda_feed.urllib.request.urlopen', side_effect=error):
                    with self.assertRaisesRegex(ValueError, message) as caught:
                        request_json('/v3/instruments/EUR_USD/candles')
                    self.assertNotIn('private-token', str(caught.exception))
        with patch.dict('os.environ', dict(OANDA_API_TOKEN='token', OANDA_ENVIRONMENT='https://untrusted.test')):
            with self.assertRaisesRegex(ValueError, 'practice or live'):
                request_json('/v3/accounts')

    def test_account_catalogue_keeps_exact_currency_pairs(self):
        data = dict(instruments=[dict(type='CURRENCY', name='EUR_USD'), dict(type='CURRENCY', name='USD_CNH'),
                                 dict(type='CFD', name='DE30_EUR')])
        with patch.dict('os.environ', {'OANDA_ACCOUNT_ID': '123-456'}), patch('oanda_feed.request_json', return_value=data):
            pairs = available_forex()
        self.assertEqual(pairs, {'EUR/USD', 'USD/CNH'})
        self.assertNotIn('USD/CNY', pairs)

    def test_hourly_routing_cache_baseline_and_next_close(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            raw = fixture()
            from dataclasses import replace
            bars = [replace(c, start=raw[0].start+i*3600000, end=raw[0].start+(i+1)*3600000) for i,c in enumerate(raw)]
            with patch.dict('os.environ', {'OANDA_ENVIRONMENT': 'practice'}), patch('oanda_feed.fetch_oanda', return_value=bars):
                result = scan_once(store, dict(assets=[self.asset], timeframes=['1h']), load_rules(), bars[-1].end)
            self.assertEqual(result[0]['status'], 'baseline set')
            from market_calendar import next_market_time
            self.assertEqual(result[0]['next_close_at'], next_market_time(self.asset,bars[-1].end + 3600000))
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT distinct feed FROM candle_cache').fetchone()[0], 'oanda:OANDA-practice:EUR/USD')
            self.assertEqual(store.rows(), [])
            with patch.dict('os.environ', {'OANDA_ENVIRONMENT': 'live'}):
                self.assertEqual(for_timeframe(self.asset, '1h')['exchange'], 'OANDA-live')
            validate_asset(self.asset)

    def test_hourly_worker_wakes_after_close_without_changing_default_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3')
            stop = Mock();stop.is_set.side_effect = [False, False, True]
            with patch('scanner.wait_for_command',return_value=None) as waiting, patch('scanner.scan_once', return_value=[dict(status='scanned', next_close_at=2000000)]), patch('scanner.time.time', return_value=1900):
                scanner_worker(store, dict(assets=[], timeframes=['1h'], poll_seconds=14400), load_rules(), stop)
            waiting.assert_called_once_with(store,stop,115)
        from managed_assets import DEFAULTS
        self.assertNotIn('1h', DEFAULTS)
        start = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
        self.assertEqual(interval_end(start, '1h'), datetime(2026, 10, 6, 11, tzinfo=timezone.utc))

    def test_activation_skips_unsupported_pair_and_preserves_hourly_opt_in(self):
        spec = importlib.util.spec_from_file_location('activate_oanda', Path(__file__).parent / 'scripts/activate-oanda.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3')
            assets = [self.asset | dict(provider='twelvedata', exchange='', timeframes=['daily']),
                      self.asset | dict(id='USDCNY', symbol='USD/CNY', provider='twelvedata', exchange='', timeframes=['daily'])]
            for a in assets:a['market'] = 'forex'
            ensure(store, dict(assets=assets, timeframes=['daily'], daily_universe=dict(enabled=True)))
            with patch.object(module, 'available_forex', return_value={'EUR/USD'}), patch.object(module, 'fetch_oanda', return_value=fixture()), patch.object(module.time, 'sleep'):
                result = module.activate(store)
            self.assertEqual(result['moved'], ['EURUSD'])
            self.assertIn('USD/CNY', result['skipped'])
            config = get(store)['config']
            self.assertEqual(config['assets'][0]['provider'], 'oanda')
            self.assertEqual(config['assets'][1]['provider'], 'twelvedata')
            self.assertNotIn('1h', config['assets'][0]['timeframes'])
            self.assertEqual(config['daily_universe']['forex_provider'], 'oanda')


if __name__ == '__main__':
    unittest.main()
