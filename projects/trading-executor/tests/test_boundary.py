import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime_store import Store,assert_worker
from signal_client import Client
from migrate import import_snapshot,verify_snapshot,TABLES
from momentum_policy import outcome,prepare
from candles import Candle

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'execution.sqlite3');self.addCleanup(self.store.close)
    def test_import_preserves_broker_ids_and_is_idempotent_and_disabled(self):
        snapshot=dict(format=1,writers_stopped=True,tables={k:[] for k in TABLES})
        trade=dict(id='demo-original',order={'orderId':'broker-original','orderLinkId':'demo-original'},source_id='source-original')
        snapshot['tables']['demo_execution_trades']=[['demo-original','protected',trade,123]]
        digest=import_snapshot(self.store,snapshot)
        self.assertEqual(import_snapshot(self.store,snapshot),digest)
        self.assertEqual(verify_snapshot(self.store,snapshot)['demo_execution_trades'],1)
        with self.store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM demo_execution_trades').fetchone()[0]),trade)
        self.assertEqual(self.store.state()[:2],(0,0))
        snapshot['tables']['paper_trades']=[['x',{}]]
        with self.assertRaises(ValueError):import_snapshot(self.store,snapshot)
    def test_disabled_by_default_and_single_process_owner(self):
        with patch.dict(os.environ,EXECUTION_ENABLED='true'):
            with self.assertRaises(RuntimeError):assert_worker(self.store)
            self.store.acquire_worker()
            with self.assertRaises(RuntimeError):assert_worker(self.store)
            other=Store(self.store.path)
            try:
                with self.assertRaises(RuntimeError):other.acquire_worker()
            finally:other.close()
    def test_page_is_saved_before_failed_ack_and_retry_is_idempotent(self):
        client=Client('http://127.0.0.1:8090','private','project')
        event=dict(contract_version=1,event_id='e1',signal_id='s1',cursor=1,kind='detected',delivery_mode='replay')
        page=dict(contract_version=1,project='project',next_cursor=1,events=[event],has_more=False)
        def request(path,body=None):
            if path=='/v1/ack':
                with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM inbox').fetchone()[0],1)
                raise TimeoutError()
            return page
        with patch.object(client,'request',side_effect=request):
            with self.assertRaises(TimeoutError):client.poll(self.store)
        with patch.object(client,'request',side_effect=[page|dict(events=[]),{}]):client.poll(self.store)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM inbox').fetchone()[0],1)
    def test_replay_old_future_and_invalidated_events_cannot_enter(self):
        with self.store.connect() as db:
            db.execute('UPDATE control SET activated_at=100')
            for i,changes in enumerate([{},dict(delivery_mode='replay'),dict(origin='replay'),dict(occurred_at=99),dict(occurred_at=201),dict(kind='invalidated')],1):
                event=dict(signal_id=str(i),event_id=str(i),strategy='rsi-pb',kind='detected',origin='live',delivery_mode='live',published_at=150,occurred_at=150)|changes
                db.execute('INSERT INTO inbox VALUES(?,?,?,?)',(str(i),i,str(i),json.dumps(event)))
        self.assertEqual(len(self.store.signals('rsi-pb',200)),1)
    def test_no_imports_from_signal_service_internals(self):
        forbidden={'scanner','platform_app','postgres_store','chasing_runtime','rsi_pb','momentum','managed_assets','source_update','telegram_checklist'}
        for file in Path(__file__).resolve().parents[1].glob('*.py'):
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node,ast.ImportFrom):self.assertNotIn(node.module,forbidden,str(file))
                if isinstance(node,ast.Import):
                    for name in node.names:self.assertNotIn(name.name,forbidden,str(file))
    def test_momentum_stop_first_and_missing_session_not_substituted(self):
        signal=dict(market='crypto',direction='buy',confirmed_at=1,entry_open_at=10,entry_min=95,entry_max=105,stop=90,target=125,rules=dict(max_hold=2,cost_bps=10))
        both=Candle(10,20,100,126,89,110)
        self.assertEqual(outcome(signal,[both])['status'],'stop')
        self.assertEqual(outcome(signal,[Candle(20,30,100,110,95,105)])['status'],'unavailable')

    def test_token_for_different_project_is_rejected_before_persistence(self):
        client=Client('http://127.0.0.1:8090','token','expected')
        with patch.object(client,'request',return_value=dict(project='other')):
            with self.assertRaisesRegex(ValueError,'another project'):client.poll(self.store)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM inbox').fetchone()[0],0)
