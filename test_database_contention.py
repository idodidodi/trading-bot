from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import cloud_sync
from platform_app import Store
import paper_trading


class DatabaseContentionTests(unittest.TestCase):
    def test_linux_verbose_activity_is_both_printed_and_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'signals.sqlite3')
            with patch.dict('os.environ',{'TRADING_BOT_ENV':'prod','TRADING_BOT_LOG_FILE':str(Path(folder)/'runtime.log'),'TRADING_BOT_VERBOSE_LOGS':'true'}), \
                 patch('builtins.print') as console:
                store.log('Scan cycle completed','one market checked')
                self.assertIn('Scan cycle completed',console.call_args.args[0])
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT event FROM activity_log').fetchone()[0],'Scan cycle completed')

    def test_snapshot_releases_writer_between_candles_and_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'signals.sqlite3'
            store=Store(path)
            with store.connect() as db:
                db.executemany('INSERT INTO candle_cache VALUES(?,?,?,?,?,?,?,?,?)',
                    [('feed','TEST','daily',i,i+1,1,1,1,1) for i in range(201)])
                db.execute('CREATE TABLE backtest_runs(id INTEGER PRIMARY KEY,report TEXT)')
                db.execute('INSERT INTO backtest_runs(report) VALUES(?)',
                    (json.dumps(dict(test_report=True,results=[])),))
            canonical=cloud_sync.canonical
            competing_writes=[]

            def serialize(payload):
                if isinstance(payload,dict) and (payload.get('test_report') or payload.get('source')=='live' and payload.get('start')==150):
                    # A real independent writer while sync is serializing a
                    # report or iterating a still-open candle SELECT cursor.
                    with closing(sqlite3.connect(path,timeout=.05)) as concurrent:
                        with concurrent:
                            concurrent.execute("INSERT OR REPLACE INTO cloud_meta VALUES('scanner-test','alive')")
                    competing_writes.append(True)
                return canonical(payload)

            with patch('cloud_sync.ROOT',Path(folder)), patch('cloud_sync.canonical',side_effect=serialize):
                cloud_sync.snapshot(store)
            self.assertEqual(len(competing_writes),2)
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM cloud_pending WHERE kind='candle'").fetchone()[0],201)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM cloud_pending WHERE kind='backtest_run'").fetchone()[0],1)

    def test_logging_a_database_lock_uses_stderr_without_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'signals.sqlite3'
            store=Store(path)

            @contextmanager
            def fast_connect():
                db=sqlite3.connect(path,timeout=.01)
                try:
                    with db:
                        yield db
                finally:
                    db.close()

            with closing(sqlite3.connect(path)) as holder:
                holder.execute('BEGIN IMMEDIATE')
                with patch.object(store,'connect',fast_connect), \
                     patch.dict('os.environ',{'TRADING_BOT_ENV':'prod'}), patch('sys.stderr') as output:
                    store.log('Paper trading check failed','OperationalError')
                self.assertIn('activity database busy',''.join(call.args[0] for call in output.write.call_args_list))

    def test_disabled_paper_worker_does_no_broker_work(self):
        stop=Mock()
        stop.is_set.side_effect=[False,True]
        with patch.dict('os.environ',{'PAPER_TRADING_ENABLED':'false'}), patch('paper_trading.check_and_log') as check:
            paper_trading.worker(None,stop)
        check.assert_not_called()

    def test_paper_worker_retries_database_errors_from_recovery_logging(self):
        stop=Mock()
        stop.is_set.side_effect=[False,False,True]
        with patch.dict('os.environ',{'PAPER_TRADING_ENABLED':'true','TRADING_BOT_ENV':'prod'}), \
             patch('paper_trading.check_and_log',side_effect=[sqlite3.OperationalError('database is locked'),None]) as check, \
             patch('sys.stderr'):
            paper_trading.worker(None,stop)
        self.assertEqual(check.call_count,2)
        self.assertEqual(stop.wait.call_count,2)

    def test_cloud_worker_survives_failure_logging_lock(self):
        stop=Mock()
        stop.is_set.side_effect=[False,False,True]
        store=Mock()
        store.connect.side_effect=sqlite3.OperationalError('database is locked')
        with patch('runtime_env.cloud_sync_enabled',return_value=True), \
             patch.dict('os.environ',{'CLOUD_SYNC_URL':'https://example.test','CLOUD_SYNC_TOKEN':'test'}), \
             patch('cloud_sync.snapshot',side_effect=sqlite3.OperationalError('database is locked')) as snapshot, \
             patch('cloud_sync.time.monotonic',return_value=100), patch('sys.stderr'):
            cloud_sync.worker(store,stop)
        self.assertEqual(snapshot.call_count,2)
        self.assertEqual(stop.wait.call_count,2)
