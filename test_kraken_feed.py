import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kraken_feed import fetch_kraken
from managed_assets import ensure, change, for_timeframe, validate_asset
from platform_app import Store, load_rules
from scanner import prepare_store, scan_once
from test_scanner import fixture


class KrakenTests(unittest.TestCase):
    def setUp(self):
        self.asset = dict(id='BTCUSD', provider='kraken', symbol='BTC/USD', exchange='Kraken',
                          feed_confirmed=True, enabled=True, timeframes=['4h'])

    def response(self, pair='BTC/USD', errors=None):
        # Final OHLC entry is still uncommitted even if its timestamp looks old.
        return dict(error=errors or [], result={pair: [[1700000000, '2', '3', '1', '2', '2', '1', 1],
                                                      [1700014400, '2', '3', '1', '2', '2', '1', 1]], 'last': 1700014400})

    def test_exact_pair_intervals_and_uncommitted_bar(self):
        for frame, minutes in [('1h', 60), ('4h', 240), ('daily', 1440), ('weekly', 10080)]:
            with patch('kraken_feed.urllib.request.urlopen', return_value=io.StringIO(json.dumps(self.response()))) as request:
                bars = fetch_kraken(self.asset, frame)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0].end - bars[0].start, minutes * 60000)
            self.assertIn('pair=BTCUSD', request.call_args.args[0])
            self.assertIn('assetVersion=1', request.call_args.args[0])
            self.assertEqual(bars.next_close_at, 1700014400000 + minutes * 60000)
        with self.assertRaisesRegex(ValueError, 'calendar-month'):
            fetch_kraken(self.asset, 'monthly')

    def test_wrong_quote_and_provider_errors_are_rejected(self):
        cases = [(self.response('BTC/USDT'), 'different spot pair'),
                 (self.response(errors=['EQuery:Unknown asset pair']), 'unavailable'),
                 (self.response(errors=['EAPI:Rate limit exceeded']), 'rate limit'),
                 (self.response(errors=['private unexpected response']), 'rejected')]
        for payload, expected in cases:
            with patch('kraken_feed.urllib.request.urlopen', return_value=io.StringIO(json.dumps(payload))):
                with self.assertRaisesRegex(ValueError, expected) as error:
                    fetch_kraken(self.asset, '4h')
                self.assertNotIn('private', str(error.exception))

    def test_managed_provider_routing_persists_and_keeps_caches_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            asset = self.asset | dict(provider='twelvedata', exchange='Binance', timeframes=['daily', '4h'])
            config = dict(assets=[asset], timeframes=['daily', '4h'])
            ensure(store, config)
            asset['timeframe_providers'] = {'4h': 'kraken'}
            change(store, dict(action='save', revision=1, asset=asset))
            with patch('scanner.fetch_twelve_data', return_value=fixture()) as twelve, patch('kraken_feed.fetch_kraken', return_value=fixture()) as kraken:
                coverage = scan_once(store, config, load_rules(), fixture()[-1].end)
            self.assertEqual([r['provider'] for r in coverage], ['twelvedata', 'kraken'])
            self.assertEqual(kraken.call_args.args[0]['exchange'], 'Kraken')
            self.assertEqual(twelve.call_args.args[0]['exchange'], 'Binance')
            self.assertTrue(all(r['status'] == 'baseline set' for r in coverage))
            self.assertEqual(store.rows(), [])
            with store.connect() as db:
                feeds = {r[0] for r in db.execute('select distinct feed from candle_cache')}
            self.assertEqual(feeds, {'kraken:Kraken:BTC/USD', 'twelvedata:Binance:BTC/USD'})
            self.assertEqual(for_timeframe(asset, 'monthly')['provider'], 'twelvedata')

    def test_invalid_overrides_and_unconfirmed_feeds(self):
        for overrides in ({'monthly': 'kraken'}, {'4h': 'unknown'}, {'yearly': 'kraken'}, []):
            with self.assertRaises(ValueError):
                validate_asset(self.asset | dict(provider='twelvedata', exchange='Binance',
                                                timeframes=['monthly', '4h'], timeframe_providers=overrides))
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            validate_asset(self.asset | dict(feed_confirmed=False))
        # A Kraken override cannot use the primary venue's chart mapping.
        feed = for_timeframe(self.asset | dict(provider='twelvedata', exchange='Binance',
                                               tradingview_symbol='BINANCE:BTCUSD',
                                               timeframe_providers={'4h': 'kraken'}), '4h')
        self.assertEqual(feed['tradingview_symbol'], '')

    def test_provider_pacing_is_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            assets = [self.asset | dict(timeframes=['daily', '4h']),
                      self.asset | dict(id='ETHUSD', provider='twelvedata', exchange='Binance', timeframes=['daily', '4h'])]
            with patch('scanner.fetch_twelve_data', return_value=fixture()), patch('kraken_feed.fetch_kraken', return_value=fixture()), patch('scanner.time.monotonic', return_value=10), patch('scanner.time.sleep') as sleep:
                scan_once(store, dict(assets=assets, timeframes=['4h']), load_rules(), fixture()[-1].end)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 8])

    def test_daily_crypto_uses_explicit_policy_without_changing_monthly(self):
        from daily_universe import asset
        rotating = asset('XRP/USD', 'crypto', ['monthly', 'weekly', 'daily', '4h'], 'kraken')
        self.assertEqual(for_timeframe(rotating, 'monthly')['exchange'], 'Binance')
        self.assertEqual(for_timeframe(rotating, '4h')['exchange'], 'Kraken')
        self.assertEqual(for_timeframe(rotating, 'weekly')['provider'], 'kraken')


if __name__ == '__main__':
    unittest.main()
