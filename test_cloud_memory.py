import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from cloud_sync import backtest_reports, snapshot
from platform_app import Store
from unittest.mock import patch


class CloudMemoryTests(unittest.TestCase):
    def test_saved_reports_are_streamed_with_bounded_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'signals.sqlite3')
            with store.connect() as db:
                db.execute('CREATE TABLE backtest_runs(id INTEGER PRIMARY KEY,report TEXT)')
                raw=json.dumps({'results':[], 'padding':'x'*1_000_000})
                db.executemany('INSERT INTO backtest_runs(report) VALUES(?)',((raw,) for _ in range(16)))
                tracemalloc.start()
                try:
                    count=0
                    for report in backtest_reports(db):
                        self.assertEqual(len(report['padding']),1_000_000)
                        count+=1
                    _,peak=tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
            self.assertEqual(count,16)
            self.assertLess(peak,6_000_000)

    def test_snapshot_still_mirrors_each_run_and_latest_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'signals.sqlite3')
            with store.connect() as db:
                db.execute('CREATE TABLE backtest_runs(id INTEGER PRIMARY KEY,report TEXT)')
                for year in (2025,2026):
                    report=dict(year=year,results=[dict(asset=str(year),timeframe='daily',status='scanned',signals=[])])
                    db.execute('INSERT INTO backtest_runs(report) VALUES(?)',(json.dumps(report),))
            with patch('cloud_sync.ROOT',Path(folder)):
                snapshot(store)
            with store.connect() as db:
                runs=[json.loads(raw) for raw, in db.execute("SELECT payload FROM cloud_pending WHERE kind='backtest_run'")]
                latest=json.loads(db.execute("SELECT payload FROM cloud_pending WHERE kind='summary' AND key='backtest'").fetchone()[0])
            self.assertEqual({r['year'] for r in runs},{2025,2026})
            self.assertEqual(latest[0]['asset'],'2026')
