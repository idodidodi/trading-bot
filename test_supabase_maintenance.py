import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
spec = importlib.util.spec_from_file_location('supabase_maintenance', Path(__file__).parent / 'scripts/supabase-maintenance.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class GuardTests(unittest.TestCase):
    def test_protected_tables_and_clock(self):
        for table in ('signals', 'telegram_delivery', 'candle_cache', 'legacy_artifacts'):
            with self.assertRaises(ValueError):
                m.delete_sql(table, [{}], 1800000000)
        for now in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError):
                m.predicate('tiingo_requests', now)

    def test_failed_archive_never_deletes(self):
        with patch.object(m, 'TABLES', {'tiingo_requests': ('at', 86400)}), \
             patch.object(m, 'readonly', side_effect=[[{'n': 1}], [{'tid': '(0,1)', 'version': '1', 'row': {'at': 1}}]]), \
             patch.object(m, 'private_json', side_effect=OSError('disk full')), \
             patch.object(m, 'query') as mutation:
            with self.assertRaises(OSError):
                m.retain(True)
            mutation.assert_not_called()


@unittest.skipUnless(os.environ.get('SUPABASE_MAINTENANCE_TEST_DSN'), 'Explicit disposable DB required')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        dsn = os.environ['SUPABASE_MAINTENANCE_TEST_DSN']
        options = conninfo_to_dict(dsn)
        if options.get('dbname') != 'supabase_maintenance_test' or options.get('host') != '/tmp/supabase-maintenance':
            raise ValueError('Only the isolated maintenance test database is allowed')
        self.db = psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row)
        self.addCleanup(self.db.close)
        self.db.execute('DROP SCHEMA IF EXISTS bot_runtime CASCADE; CREATE SCHEMA bot_runtime')
        self.db.execute("CREATE TABLE bot_runtime.runtime_control(id integer,installation uuid,enabled bool)")
        self.db.execute('INSERT INTO bot_runtime.runtime_control VALUES(1,%s,true)', (m.INSTALLATION,))
        self.db.execute('CREATE TABLE bot_runtime.tiingo_requests(at double precision)')
        self.db.execute('CREATE TABLE bot_runtime.tiingo_responses(key text PRIMARY KEY,fetched double precision,payload text)')
        self.db.execute('CREATE TABLE bot_runtime.activity_log(id bigserial PRIMARY KEY,occurred double precision,event text,details text)')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.now = 1800000000
        self.patcher = patch.object(m, 'query', side_effect=self.query)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def query(self, sql):
        try:
            cur = self.db.execute(sql, prepare=False)
            rows = []
            while True:
                if cur.description:
                    rows = cur.fetchall()
                if not cur.nextset():
                    return rows
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def test_boundaries_batch_limit_and_verified_restore(self):
        self.db.execute('INSERT INTO bot_runtime.tiingo_requests SELECT 1 FROM generate_series(1,1005)')
        self.db.execute('INSERT INTO bot_runtime.tiingo_requests VALUES(0),(%s),(%s)', (self.now-86400, self.now+10))
        self.db.execute("INSERT INTO bot_runtime.activity_log(occurred,event,details) VALUES (1,'Scanner heartbeat','old'),(1,'Delivery failed','retain')")
        preview = m.retain(False, self.directory, self.now)
        self.assertEqual(preview['tiingo_requests']['eligible'], 1005)
        self.assertEqual(list(self.directory.iterdir()), [])
        report = m.retain(True, self.directory, self.now)
        self.assertEqual(report['tiingo_requests']['deleted'], 1000)
        self.assertEqual(report['activity_log']['deleted'], 1)
        self.assertEqual(self.db.execute('SELECT count(*) AS n FROM bot_runtime.tiingo_requests').fetchone()['n'], 8)
        self.assertEqual(self.db.execute('SELECT event FROM bot_runtime.activity_log').fetchone()['event'], 'Delivery failed')
        backup = json.loads(Path(report['tiingo_requests']['archive']).read_text())
        self.assertEqual(len(backup['rows']), 1000)
        # Archived rows restore exactly; never overwrite newer production rows.
        for row in backup['rows']:
            self.db.execute('INSERT INTO bot_runtime.tiingo_requests VALUES(%s)', (row['row']['at'],))
        self.assertEqual(self.db.execute('SELECT count(*) AS n FROM bot_runtime.tiingo_requests').fetchone()['n'], 1008)

    def test_refresh_between_archive_and_delete_is_preserved(self):
        self.db.execute("INSERT INTO bot_runtime.tiingo_responses VALUES('a',1,'old')")
        saved = self.query('SELECT ctid::text AS tid,xmin::text AS version,to_jsonb(t) AS row FROM bot_runtime.tiingo_responses t')
        self.db.execute("UPDATE bot_runtime.tiingo_responses SET fetched=%s,payload='refreshed'", (self.now,))
        self.assertEqual(self.query(m.delete_sql('tiingo_responses', saved, self.now))[0]['deleted'], 0)
        self.assertEqual(self.db.execute('SELECT payload FROM bot_runtime.tiingo_responses').fetchone()['payload'], 'refreshed')

    def test_wrong_installation_rolls_back(self):
        self.db.execute('INSERT INTO bot_runtime.tiingo_requests VALUES(1)')
        self.db.execute('UPDATE bot_runtime.runtime_control SET enabled=false')
        with self.assertRaises(Exception):
            m.retain(True, self.directory, self.now)
        self.assertEqual(self.db.execute('SELECT count(*) AS n FROM bot_runtime.tiingo_requests').fetchone()['n'], 1)
        self.assertEqual(len(list(self.directory.glob('retention-*.json'))), 1)

    def test_payload_quotes_are_data_and_replay_is_idempotent(self):
        self.db.execute('INSERT INTO bot_runtime.tiingo_responses VALUES(%s,1,%s)', ('x', "'; DELETE FROM bot_runtime.runtime_control; --"))
        rows = self.query('SELECT ctid::text AS tid,xmin::text AS version,to_jsonb(t) AS row FROM bot_runtime.tiingo_responses t')
        sql = m.delete_sql('tiingo_responses', rows, self.now)
        self.assertEqual(self.query(sql)[0]['deleted'], 1)
        self.assertEqual(self.query(sql)[0]['deleted'], 0)
        self.assertEqual(self.db.execute('SELECT count(*) AS n FROM bot_runtime.runtime_control').fetchone()['n'], 1)


if __name__ == '__main__':
    unittest.main()
