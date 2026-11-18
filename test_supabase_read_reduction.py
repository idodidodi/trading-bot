"""Bandwidth regressions against disposable PostgreSQL; no production access."""
import copy
import json
from contextlib import contextmanager
from unittest.mock import patch

from test_postgres_support import PostgresTestCase, TestStore
import managed_assets
import momentum


class ReadReductionTests(PostgresTestCase):
    def setUp(self):
        self.store = TestStore()
        managed_assets.ensure(self.store, dict(assets=[], timeframes=['daily']))

    def test_unchanged_config_omits_payload_but_reads_current_revisions(self):
        queries, rows = [], []
        connect = self.store.connect

        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def fetchone(self):
                row = self.cursor.fetchone()
                rows.append(row)
                return row

        class Database:
            def __init__(self, db): self.db = db
            def execute(self, query, args=None):
                queries.append(query)
                return Cursor(self.db.execute(query, args))

        @contextmanager
        def observed_connect():
            with connect() as db:
                yield Database(db)

        with connect() as db:
            db.execute('UPDATE managed_config SET applied_revision=1 WHERE id=1')
        with patch.object(self.store, 'connect', observed_connect):
            result = managed_assets.get(self.store)
        self.assertEqual(result['applied_revision'], 1)
        self.assertEqual(len(queries), 3)
        self.assertIsNone(rows[-1][3])  # No configuration text crosses the connection.
        self.assertFalse(any('pg_advisory' in q for q in queries))

    def test_same_revision_external_edit_is_immediately_visible(self):
        before = managed_assets.get(self.store)
        config = copy.deepcopy(before['config'])
        config['test_marker'] = 'updated externally'
        with self.store.connect() as db:
            db.execute('UPDATE managed_config SET payload=%s WHERE id=1', (json.dumps(config),))
        after = managed_assets.get(self.store)
        self.assertEqual(after['revision'], before['revision'])
        self.assertEqual(after['config'], config)

    def test_caller_mutations_do_not_change_cached_config(self):
        before = managed_assets.get(self.store)
        altered = managed_assets.get(self.store)
        altered['config']['assets'].append(dict(id='in-memory-only'))
        self.assertEqual(managed_assets.get(self.store), before)

    def test_warm_cache_does_not_hide_database_failure_or_missing_row(self):
        with patch.object(self.store, 'connect', side_effect=ConnectionError('offline')):
            with self.assertRaises(ConnectionError): managed_assets.get(self.store)
        with self.store.connect() as db:
            db.execute('DELETE FROM managed_config')
        with self.assertRaisesRegex(ValueError, 'not been initialized'):
            managed_assets.get(self.store)

    def test_live_momentum_view_does_not_fetch_replay_history(self):
        # The live result must not parse unrelated replay data, however large.
        with self.store.connect() as db:
            db.execute('INSERT INTO momentum_history VALUES(%s,%s,%s)', ('stock', 'TEST', 'invalid replay JSON'))
        result = momentum.view(self.store)
        self.assertIsNone(result['latest'])
        self.assertEqual(result['signals'], [])
