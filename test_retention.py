import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('retention', Path(__file__).parent/'scripts/retention.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
NOW = 1800000000


class LocalRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name, age=100):
        p = self.root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'preserve this evidence\n'*100)
        os.utime(p, (NOW-age*r.DAY, NOW-age*r.DAY))
        return p

    def test_only_allowlisted_old_data_selected(self):
        self.file('__pycache__/old.pyc')
        self.file('__pycache__/new.pyc', 1)
        self.file('__pycache__/boundary.pyc', 7)
        self.file('data/inactive.log')
        self.file('data/active.log')
        self.file('data/history.csv')
        rows = r.inventory(self.root, ['data/inactive.log'], NOW)
        self.assertEqual({x['path'] for x in rows if x['action']=='quarantine'},
                         {'__pycache__/old.pyc', 'data/inactive.log'})
        self.assertTrue((self.root/'__pycache__/old.pyc').exists())

    def test_archive_restore_and_repeat(self):
        p = self.file('__pycache__/old.pyc')
        original = p.read_bytes()
        result = r.quarantine(self.root, r.inventory(self.root, now=NOW), NOW)
        self.assertFalse(p.exists())
        manifest = next(Path(result['archive']).glob('manifest.json'))
        self.assertEqual(r.restore_local(self.root, manifest), 1)
        self.assertEqual(p.read_bytes(), original)
        with self.assertRaises(FileExistsError):
            r.restore_local(self.root, manifest)
        self.assertEqual(r.quarantine(self.root, r.inventory(self.root, now=NOW), NOW)['files'], 1)
        self.assertEqual(r.quarantine(self.root, r.inventory(self.root, now=NOW), NOW)['files'], 0)

    def test_changed_candidate_is_retained(self):
        p = self.file('__pycache__/old.pyc')
        rows = r.inventory(self.root, now=NOW)
        p.write_bytes(b'updated')
        with self.assertRaises(ValueError):
            r.quarantine(self.root, rows, NOW)
        self.assertEqual(p.read_bytes(), b'updated')

    def test_symlink_and_escape_rejected(self):
        p = self.file('data/protected.csv')
        (self.root/'__pycache__').symlink_to(self.root/'data', target_is_directory=True)
        self.assertFalse(any(x['action']=='quarantine' for x in r.inventory(self.root, now=NOW)))
        with self.assertRaises(ValueError):
            r.safe_file(self.root, self.root/'../outside')
        with self.assertRaises(ValueError):
            r.safe_file(self.root, self.root/'__pycache__/protected.csv')
        self.assertTrue(p.exists())

    def test_corrupt_backup_prevents_restore(self):
        self.file('__pycache__/old.pyc')
        result = r.quarantine(self.root, r.inventory(self.root, now=NOW), NOW)
        folder = Path(result['archive'])
        next(folder.glob('*.gz')).write_bytes(b'corrupt')
        with self.assertRaises(Exception):
            r.restore_local(self.root, next(folder.glob('manifest.json')))


from test_postgres_support import PostgresTestCase, test_dsn
import psycopg


class DatabaseRetentionTests(PostgresTestCase):
    def test_cutoffs_backup_and_protected_records(self):
        with psycopg.connect(test_dsn()) as db:
            for at in (NOW-r.DAY-1, NOW-r.DAY, NOW, 0, NOW*1000):
                db.execute('INSERT INTO bot_runtime.tiingo_requests VALUES(%s)', (at,))
            db.execute("INSERT INTO bot_runtime.tiingo_responses VALUES('old',%s,'{}')", (NOW-r.DAY-1,))
            for event in ('Scanner heartbeat', 'Delivery failed', 'Market check'):
                db.execute('INSERT INTO bot_runtime.activity_log(occurred,event,details) VALUES(%s,%s,%s)', (NOW-31*r.DAY,event,'evidence'))
            db.execute("INSERT INTO bot_runtime.activity_log(occurred,event) VALUES(NULL,'Scanner heartbeat')")
            db.execute("INSERT INTO bot_runtime.signals(id,payload,received,status) VALUES('protected','{}',1,'sent')")
        preview = r.database(test_dsn(), now=NOW)
        self.assertEqual(preview['tiingo_requests']['eligible'], 1)
        self.assertEqual(preview['activity_log']['eligible'], 1)
        with tempfile.TemporaryDirectory() as temp:
            result = r.database(test_dsn(), Path(temp), NOW)
            self.assertEqual(result['activity_log']['deleted'], 1)
            backup = json.loads(Path(result['activity_log']['backup']).read_text())
            with psycopg.connect(test_dsn()) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM bot_runtime.tiingo_requests').fetchone()[0], 4)
                self.assertEqual(db.execute('SELECT count(*) FROM bot_runtime.signals').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT count(*) FROM bot_runtime.activity_log').fetchone()[0], 3)
                # Demonstrate exact archive restoration against a disposable table.
                for row in backup['rows']:
                    db.execute('INSERT INTO bot_runtime.activity_log SELECT * FROM json_populate_record(NULL::bot_runtime.activity_log,%s::json)', (json.dumps(row),))
                self.assertEqual(db.execute('SELECT details FROM bot_runtime.activity_log WHERE event=%s AND occurred IS NOT NULL', ('Scanner heartbeat',)).fetchone()[0], 'evidence')
            self.assertEqual(r.database(test_dsn(), now=NOW)['tiingo_requests']['eligible'], 0)

    def test_batch_bound_and_backup_failure_rollback(self):
        with psycopg.connect(test_dsn()) as db:
            db.execute('INSERT INTO bot_runtime.tiingo_requests SELECT 1 FROM generate_series(1,1005)')
        with tempfile.TemporaryDirectory() as temp:
            result = r.database(test_dsn(), Path(temp), NOW)
            self.assertEqual(result['tiingo_requests']['deleted'], 1000)
        with self.assertRaises(FileNotFoundError):
            r.database(test_dsn(), Path('/missing-retention-archive'), NOW)
        self.assertEqual(r.database(test_dsn(), now=NOW)['tiingo_requests']['eligible'], 5)

    def test_locked_cache_refresh_skipped(self):
        with psycopg.connect(test_dsn()) as db:
            db.execute("INSERT INTO bot_runtime.tiingo_responses VALUES('busy',1,'{}')")
        with psycopg.connect(test_dsn()) as writer, tempfile.TemporaryDirectory() as temp:
            writer.execute("SELECT * FROM bot_runtime.tiingo_responses FOR UPDATE")
            result = r.database(test_dsn(), Path(temp), NOW)
            self.assertEqual(result['tiingo_responses']['deleted'], 0)
