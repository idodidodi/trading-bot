from test_postgres_support import TestStore as Store, PostgresTestCase
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kraken_feed import fetch_kraken
from managed_assets import ensure, change, for_timeframe, validate_asset
from platform_app import load_rules
from scanner import prepare_store, scan_once
from test_scanner import fixture


class PacingTests(unittest.TestCase):
    def test_public_requests_share_one_second_spacing_and_cooldown_fails_without_request(self):
        import kraken_feed as feed
        clock=[10.];sleeps=[]
        def sleep(seconds):sleeps.append(seconds);clock[0]+=seconds
        with patch.object(feed,'_next_request_at',0.),patch.object(feed,'_cooldown_until',0.), \
             patch('kraken_feed.time.monotonic',side_effect=lambda:clock[0]),patch('kraken_feed.time.sleep',side_effect=sleep):
            feed.pace_public_request();feed.pace_public_request()
            self.assertEqual(sleeps,[1.])
            feed.cooldown()
            with self.assertRaisesRegex(ValueError,'cooldown'):feed.pace_public_request()
            self.assertEqual(sleeps,[1.])


class KrakenTests(PostgresTestCase):
    def setUp(self):
        pacing=patch('kraken_feed.pace_public_request');pacing.start();self.addCleanup(pacing.stop)
        cooling=patch('kraken_feed.cooldown');cooling.start();self.addCleanup(cooling.stop)
        self.asset = dict(id='BTCUSD', provider='kraken', symbol='BTC/USD', exchange='Kraken',
                          feed_confirmed=True, enabled=True, timeframes=['4h'])

    def response(self, pair='BTC/USD', errors=None):
        # Final OHLC entry is still uncommitted even if its timestamp looks old.
        return dict(error=errors or [], result={pair: [[1700000000, '2', '3', '1', '2', '2', '1', 1],
                                                      [1700014400, '2', '3', '1', '2', '2', '1', 1]], 'last': 1700014400})

    def test_exact_pair_intervals_and_uncommitted_bar(self):
        for frame, minutes in [('5m', 5), ('15m', 15), ('30m', 30), ('1h', 60), ('4h', 240), ('daily', 1440), ('weekly', 10080)]:
            with patch('kraken_feed.urllib.request.urlopen', return_value=io.StringIO(json.dumps(self.response()))) as request:
                bars = fetch_kraken(self.asset, frame)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0].end - bars[0].start, minutes * 60000)
            self.assertIn('pair=BTCUSD', request.call_args.args[0])
            self.assertIn('assetVersion=1', request.call_args.args[0])
            self.assertIn(f'interval={minutes}&', request.call_args.args[0])
            self.assertEqual(bars.next_close_at, 1700014400000 + minutes * 60000)
        with self.assertRaisesRegex(ValueError, 'calendar-month'):
            fetch_kraken(self.asset, 'monthly')

    def test_intraday_scans_keep_cache_and_deadlines_independent(self):
        from dataclasses import replace
        from scanner import ProviderCandles
        frames = {'5m': 5, '15m': 15, '30m': 30, 'daily': 1440}
        now = 1800000000000
        histories = {}
        for frame, minutes in frames.items():
            period = minutes * 60000
            bars = [replace(c, start=now-(260-i)*period, end=now-(259-i)*period)
                    for i, c in enumerate(fixture())]
            histories[frame] = ProviderCandles(bars, [])
            histories[frame].next_close_at = now + period
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            asset = self.asset | dict(timeframes=list(frames), scan_strategies=['rsi'])
            config = dict(assets=[asset], timeframes=list(frames))
            ensure(store, config)
            with patch('kraken_feed.fetch_kraken', side_effect=lambda a, f: histories[f]) as fetch, patch('scanner.time.sleep'):
                baseline = scan_once(store, config, load_rules(), now)
                repeated = scan_once(store, config, load_rules(), now+1000)
                self.assertEqual(fetch.call_count, 4)
                self.assertTrue(all(r['status']=='baseline set' for r in baseline), baseline)
                self.assertTrue(all(r['check_state']=='waiting for candle close' for r in repeated))
                # Only 5m is due at its next close; neither 15m nor 30m refetches.
                due = scan_once(store, config, load_rules(), now+300000)
                self.assertEqual(fetch.call_count, 5)
                self.assertEqual(fetch.call_args.args[1], '5m')
                self.assertEqual(due[0]['next_close_at'], now+360000)  # unchanged provider bar: retry in 1m
            with store.connect() as db:
                rows = db.execute('SELECT timeframe,COUNT(*),MIN("end"-start),MAX("end"-start) FROM candle_cache GROUP BY timeframe').fetchall()
            self.assertEqual({r[0]:tuple(r[1:]) for r in rows},
                             {f:(260, m*60000, m*60000) for f,m in frames.items()})
            self.assertEqual(store.rows(), [])

    def test_intraday_candle_view_preserves_kraken_chart_interval(self):
        from candle_views import detail
        from finding_feedback import finding_id
        from managed_assets import cache_candles
        from test_platform import candle_example
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            asset = self.asset | dict(timeframes=['5m','15m','30m'], tradingview_symbol='KRAKEN:BTCUSD')
            ensure(store, dict(assets=[asset], timeframes=asset['timeframes']))
            for frame, minutes in [('5m',5),('15m',15),('30m',30)]:
                signal = candle_example() | dict(symbol='kraken:Kraken:BTC/USD', timeframe=frame)
                store.insert(signal)
                cache_candles(store, asset, frame, fixture())
                view = detail(store, Path(folder), 'live', finding_id('live',signal))
                self.assertIn(f'interval={minutes}', view['tradingview_url'])
                self.assertIn('KRAKEN%3ABTCUSD', view['tradingview_url'])

    def test_hosted_intraday_validation_and_migration_rerun(self):
        import psycopg
        from psycopg.types.json import Jsonb
        from test_postgres_support import test_dsn
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3'); prepare_store(store)
            ensure(store, dict(assets=[self.asset], timeframes=['4h']))
            # Apply twice to the existing runtime copy, not just a fresh base API.
            with psycopg.connect(test_dsn(), autocommit=True) as db:
                migration = (Path(__file__).parent/'cloud'/'kraken-intraday.sql').read_text()
                db.execute(migration, prepare=False)
                db.execute(migration, prepare=False)
            def save(asset, revision=1, owner='00000000-0000-0000-0000-000000000001'):
                with psycopg.connect(test_dsn()) as db:
                    db.execute("SELECT set_config('test.auth_uid',%s,true)", (owner,))
                    db.execute('SET LOCAL ROLE authenticated')
                    return db.execute('SELECT public.dashboard_api(%s,%s,%s)',
                                      ('00000000-0000-0000-0000-000000000002','/api/assets',
                                       Jsonb(dict(action='save', revision=revision, asset=asset)))).fetchone()[0]
            asset = self.asset | dict(timeframes=['5m','15m','30m'], scan_strategies=['rsi'])
            result = save(asset)
            self.assertEqual(result['config']['assets'][0]['timeframes'], asset['timeframes'])
            with self.assertRaisesRegex(psycopg.Error,'require Kraken or CSV'):
                save(asset | dict(provider='twelvedata',exchange='Binance'), result['revision'])
            mixed = asset | dict(provider='twelvedata', exchange='Binance',
                                 timeframe_providers={f:'kraken' for f in asset['timeframes']})
            result = save(mixed, result['revision'])
            self.assertEqual(result['config']['assets'][0]['timeframe_providers'], mixed['timeframe_providers'])
            with self.assertRaises(psycopg.Error):
                save(asset, result['revision'], owner='00000000-0000-0000-0000-000000000003')

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
