import os
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager
import psycopg
from working_postgres import WorkingStore
from local_cache import publish_candles, read_backtest_candles

DSN='host=127.0.0.1 port=55439 dbname=trading_working_test user=postgres'

class WorkingTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('WORKING_PG_TEST')!='1': self.skipTest('Explicit disposable local test database required')
        self.env=patch.dict(os.environ,{'TRADING_WORKING_DATABASE_URL':DSN,'TRADING_DATABASE_URL':'test-shared-destination'})
        self.env.start();self.addCleanup(self.env.stop)
        self.cache=WorkingStore(DSN)
        with self.cache.connect() as db:
            db.execute('TRUNCATE working.candles,working.responses,working.publication,working.counters')
        self.rows=[('feed','1h',1,2,3.,4.,2.,3.)]
        self.calls=[]
        outer=self
        class Hosted:
            @contextmanager
            def connect(self):
                class Cursor:
                    def __enter__(self): return self
                    def __exit__(self,*args): pass
                    def executemany(self,q,rows): outer.calls.extend(rows)
                class DB:
                    def cursor(self): return Cursor()
                yield DB()
        self.host=Hosted()

    def test_publish_deduplicate_correct_and_local_replay(self):
        publish_candles(self.host,'backtest_candle_cache',self.rows)
        publish_candles(self.host,'backtest_candle_cache',self.rows)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(read_backtest_candles(None,'feed','1h'),[tuple(self.rows[0][-6:])])
        corrected=[(*self.rows[0][:-1],3.5)]
        publish_candles(self.host,'backtest_candle_cache',corrected)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.cache.read_candles('backtest_candle_cache','feed','1h')[0][-1],3.5)

    def test_ambiguous_host_commit_never_acknowledged(self):
        @contextmanager
        def lost():
            with self.host.connect() as db: yield db
            raise RuntimeError('lost commit response')
        class Broken:
            connect=staticmethod(lost)
        with self.assertRaises(RuntimeError): publish_candles(Broken(),'backtest_candle_cache',self.rows)
        with self.cache.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM publication').fetchone()[0],0)
        publish_candles(self.host,'backtest_candle_cache',self.rows)
        self.assertEqual(len(self.calls),2)

    def test_concurrent_publishers_keep_ack_and_history_equal(self):
        from concurrent.futures import ThreadPoolExecutor
        def send(value):
            publish_candles(self.host,'backtest_candle_cache',[(*self.rows[0][:-1],value)])
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(send,[3.25,3.75]))
        with self.cache.connect() as db:
            payload=db.execute('SELECT payload FROM publication').fetchone()[0]
        self.assertEqual(payload,list(self.calls[-1]))
        self.assertEqual(self.cache.read_candles('backtest_candle_cache','feed','1h')[0][-1],payload[-1])

    def test_response_ttl_and_namespaces(self):
        self.cache.put('x',[1],100)
        self.assertEqual(self.cache.get('x',101,300),[1])
        self.assertIsNone(self.cache.get('x',400,300))
        self.assertIsNone(self.cache.get('x',99,300))
        with patch.dict(os.environ,{'TRADING_DATABASE_URL':'other'}):
            self.assertIsNone(WorkingStore(DSN).get('x',101,300))

    def test_offline_store_does_not_fall_back_to_hosted(self):
        with patch.dict(os.environ,{'TRADING_WORKING_DATABASE_URL':DSN.replace('55439','55438')}):
            with self.assertRaises(psycopg.OperationalError):read_backtest_candles(self.host,'feed','1h')
        self.assertEqual(self.calls,[])

class DestinationTests(unittest.TestCase):
    def test_remote_and_indirect_destinations_rejected(self):
        for dsn in ['host=example.com dbname=x','host=localhost hostaddr=8.8.8.8 dbname=x','service=production','dbname=x']:
            with self.assertRaises(ValueError):WorkingStore(dsn)

from test_local_cache import IntegrationTests as SQLiteIntegration
from test_postgres_support import PostgresTestCase

class ScannerWorkingIntegration(PostgresTestCase):
    def setUp(self):
        if os.environ.get('WORKING_PG_TEST')!='1':self.skipTest('Explicit working fixture required')
        SQLiteIntegration.setUp(self)
        env=patch.dict(os.environ,{'TRADING_WORKING_DATABASE_URL':DSN})
        env.start();self.addCleanup(env.stop)
        with WorkingStore(DSN).connect() as db:
            db.execute('TRUNCATE candles,responses,publication,counters')

    test_scanner_signal_baseline_and_dedup=SQLiteIntegration.test_scanner_enabled_cache_preserves_baseline_signal_and_dedup
    test_provider_budget=SQLiteIntegration.test_tiingo_expired_local_payload_never_bypasses_budget

class SeedIntegration(PostgresTestCase):
    def test_seed_readonly_and_reject_overwrite(self):
        if os.environ.get('WORKING_PG_TEST')!='1':self.skipTest('Explicit working fixture required')
        from test_postgres_support import test_dsn
        import subprocess,sys
        with patch.dict(os.environ,{'TRADING_WORKING_DATABASE_URL':DSN}):
            local=WorkingStore(DSN)
            with local.connect() as db:db.execute('TRUNCATE candles,responses,publication,counters')
            with psycopg.connect(test_dsn()) as db:
                db.execute("INSERT INTO bot_runtime.backtest_candle_cache VALUES('seed','1h',1,2,3,4,2,3)")
            result=subprocess.run([sys.executable,'scripts/seed-working-postgres.py','--writers-stopped'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(local.read_candles('backtest_candle_cache','seed','1h'),[(1,2,3,4,2,3)])
            again=subprocess.run([sys.executable,'scripts/seed-working-postgres.py','--writers-stopped'],capture_output=True,text=True)
            self.assertNotEqual(again.returncode,0)
            with psycopg.connect(test_dsn()) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM bot_runtime.backtest_candle_cache').fetchone()[0],1)
