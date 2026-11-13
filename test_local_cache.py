"""Disposable local cache and Postgres integration; no live provider/effect calls."""
from contextlib import contextmanager, closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from local_cache import LocalCache, CacheError, configured, publish_candles, read_backtest_candles
from test_postgres_support import PostgresTestCase, TestStore


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = LocalCache(self.tmp.name, 'test')

    def test_response_expiry_future_clock_namespace_and_restart(self):
        self.cache.put('key', [1], 100)
        self.assertEqual(LocalCache(self.tmp.name, 'test').get('key', 399, 300), [1])
        self.assertIsNone(self.cache.get('key', 400, 300))
        self.assertIsNone(self.cache.get('key', 99, 300))
        self.assertIsNone(LocalCache(self.tmp.name, 'other').get('key', 101, 300))
        self.cache.put('new', [], 100000)
        self.assertIsNone(self.cache.get('key', 101, 300))
        self.assertEqual(self.cache.get('new', 100001, 300), [])

    def test_consistent_private_backup_with_wal_and_no_overwrite(self):
        self.cache.put('key', ['payload'], 100)
        backup = Path(self.tmp.name) / 'backup.sqlite3'
        self.cache.backup(backup)
        if os.name != 'nt':
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('SELECT payload FROM responses').fetchone()[0], '["payload"]')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        with self.assertRaises(FileExistsError):
            self.cache.backup(backup)
        health = self.cache.health()
        self.assertEqual(health['integrity'], 'ok')
        self.assertGreater(health['cache_bytes'], 0)

    def test_bad_permissions_corruption_and_relative_path_fail_closed(self):
        with self.assertRaises(CacheError):
            LocalCache('relative', 'test')
        if os.name != 'nt':
            os.chmod(self.tmp.name, 0o755)
            with self.assertRaisesRegex(CacheError, 'private'):
                self.cache.health()
            os.chmod(self.tmp.name, 0o700)
        self.cache.path.write_bytes(b'broken database')
        with self.assertRaisesRegex(CacheError, 'unavailable'):
            self.cache.health()

    def test_destination_isolation(self):
        with patch.dict(os.environ, TRADING_LOCAL_CACHE_DIR=self.tmp.name, TRADING_DATABASE_URL='destination-a'):
            configured().put('key', [1], 100)
            with patch.dict(os.environ, TRADING_DATABASE_URL='destination-b'):
                self.assertIsNone(configured().get('key', 101, 300))
            self.assertNotIn(b'destination-a', self.cache.path.read_bytes())

    def test_failed_and_ambiguous_publication_never_acknowledged(self):
        class FailingStore:
            @contextmanager
            def connect(self):
                class DB:
                    def executemany(self, query, rows):
                        pass
                yield DB()
                raise RuntimeError('COMMIT acknowledgement lost')
        with patch.dict(os.environ, TRADING_LOCAL_CACHE_DIR=self.tmp.name, TRADING_DATABASE_URL='test'):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'COMMIT'):
                    publish_candles(FailingStore(), 'candle_cache', [LIVE])
                self.assertEqual(configured().health()['candle_rows'], 0)


LIVE = ('provider:feed:ABC', 'ABC', 'daily', 1, 2, 10., 12., 9., 11.)


class IntegrationTests(PostgresTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, TRADING_LOCAL_CACHE_DIR=self.tmp.name, TIINGO_API_KEY='not-a-real-token',
                              TIINGO_REQUESTS_PER_HOUR='1', TIINGO_REQUESTS_PER_DAY='1')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = TestStore()

    def test_cold_warm_revision_restart_and_reconciliation(self):
        with patch('local_cache.time.time', return_value=100):
            publish_candles(self.store, 'candle_cache', [LIVE])
            publish_candles(self.store, 'candle_cache', [LIVE])
        h = configured().health()
        self.assertEqual(h['counters']['candles_published'], 1)
        self.assertEqual(h['counters']['candle_writes_avoided'], 1)
        revised = LIVE[:-1] + (11.5,)
        publish_candles(self.store, 'candle_cache', [revised])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT close FROM candle_cache').fetchone()[0], 11.5)
            db.execute('DELETE FROM candle_cache')
        # After the daily reconciliation horizon a missing hosted row is restored.
        with patch('local_cache.time.time', return_value=10**11):
            publish_candles(self.store, 'candle_cache', [revised])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT close FROM candle_cache').fetchone()[0], 11.5)

    def test_concurrent_publishers_keep_hosted_and_local_payload_equal(self):
        from concurrent.futures import ThreadPoolExecutor
        def publish(i):
            publish_candles(self.store, 'candle_cache', [LIVE[:-1] + (float(i),)])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(publish, range(16)))
        with self.store.connect() as db:
            hosted = db.execute('SELECT * FROM candle_cache').fetchone()
        with configured().connect() as db:
            local = json.loads(db.execute('SELECT payload FROM candles').fetchone()[0])
        self.assertEqual(list(hosted), local)
        with patch.object(self.store, 'connect', side_effect=AssertionError('unchanged candle went online')):
            publish_candles(self.store, 'candle_cache', [hosted])

    def test_candle_revision_commits_before_local_ack_and_empty_batch(self):
        from managed_assets import cache_candles
        from scanner import Candle
        asset = dict(provider='provider', exchange='feed', symbol='ABC', id='ABC')
        cache_candles(self.store, asset, 'daily', [Candle(*LIVE[3:])])
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM candle_cache').fetchall()
        self.assertEqual(rows, [LIVE])
        cache_candles(self.store, asset, 'daily', [])
        self.assertEqual(configured().health()['candle_rows'], 1)

    def test_backtest_read_through_refresh_and_disabled_mode(self):
        row = (LIVE[0], LIVE[2], *LIVE[3:])
        publish_candles(self.store, 'backtest_candle_cache', [row])
        with patch('local_cache.time.time', return_value=100):
            self.assertEqual(list(read_backtest_candles(self.store, LIVE[0], 'daily')[0]), list(LIVE[3:]))
            with patch.object(self.store, 'connect', side_effect=AssertionError('unexpected hosted read')):
                self.assertEqual(read_backtest_candles(self.store, LIVE[0], 'daily')[0][-1], 11.)
        with self.store.connect() as db:
            db.execute('UPDATE backtest_candle_cache SET close=11.5')
        with patch('local_cache.time.time', return_value=400):
            self.assertEqual(read_backtest_candles(self.store, LIVE[0], 'daily')[0][-1], 11.5)
        with patch.dict(os.environ, TRADING_LOCAL_CACHE_DIR=''):
            self.assertIsNone(configured())
            publish_candles(self.store, 'backtest_candle_cache', [row])
            self.assertEqual(read_backtest_candles(self.store, LIVE[0], 'daily')[0][-1], 11.)

    def test_scanner_enabled_cache_preserves_baseline_signal_and_dedup(self):
        from dataclasses import replace
        from platform_app import load_rules
        from scanner import prepare_store, scan_once
        from test_scanner import fixture
        bars = fixture()
        start = bars[0].start
        bars = [replace(c, start=start+i*86400000, end=start+(i+1)*86400000) for i,c in enumerate(bars)]
        prepare_store(self.store)
        asset = dict(id='SPX', provider='fmp', exchange='INDEX', symbol='^GSPC', market='index', scan_strategies=['rsi'])
        config = dict(timeframes=['daily'], assets=[asset])
        with patch('fmp_feed.fetch_fmp', return_value=bars[:256]):
            baseline = scan_once(self.store, config, load_rules(), bars[255].end)
        self.assertEqual(baseline[0]['status'], 'baseline set')
        self.assertEqual(self.store.rows(), [])
        with patch('fmp_feed.fetch_fmp', return_value=bars[:257]):
            result = scan_once(self.store, config, load_rules(), bars[256].end)
            repeat = scan_once(self.store, config, load_rules(), bars[256].end)
        self.assertEqual(result[0]['new_signals'], 1)
        self.assertEqual(repeat[0]['new_signals'], 0)
        self.assertEqual(len(self.store.rows()), 1)
        h = configured().health()
        self.assertEqual(h['counters']['candles_published'], 257)
        self.assertGreaterEqual(h['counters']['candle_writes_avoided'], 256)

    def test_tiingo_payload_local_shared_budget_and_token_isolation(self):
        from tiingo_feed import request_json
        from postgres_store import connect
        args = ('EUR/USD', '1hour', '2026-10-01', '2026-10-02')
        data = [dict(ticker='eurusd', close=1.5)]
        with patch('tiingo_feed.urllib.request.urlopen', return_value=io.StringIO(json.dumps(data))) as provider:
            self.assertEqual(request_json(*args), data)
            self.assertEqual(request_json(*args), data)
            self.assertEqual(provider.call_count, 1)
        with connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tiingo_responses').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM tiingo_requests').fetchone()[0], 1)
        with patch('tiingo_feed.urllib.request.urlopen') as provider:
            with self.assertRaisesRegex(ValueError, 'budget'):
                request_json('GBP/USD', *args[1:])
            with patch.dict(os.environ, TIINGO_API_KEY='changed-token'):
                with self.assertRaisesRegex(ValueError, 'budget'):
                    request_json(*args)
            provider.assert_not_called()
        self.assertNotIn(b'not-a-real-token', configured().path.read_bytes())

    def test_tiingo_expired_local_payload_never_bypasses_budget(self):
        from tiingo_feed import request_json
        args = ('EUR/USD', '1hour', '2026-10-01', '2026-10-02')
        with patch('tiingo_feed.urllib.request.urlopen', return_value=io.StringIO('[]')):
            self.assertEqual(request_json(*args), [])
        with configured().connect() as db:
            db.execute('UPDATE responses SET fetched=0')
        with patch('tiingo_feed.urllib.request.urlopen') as provider:
            with self.assertRaisesRegex(ValueError, 'budget'):
                request_json(*args)
            provider.assert_not_called()


if __name__ == '__main__':
    unittest.main()
