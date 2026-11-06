from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import urllib.error
import urllib.request
from test_postgres_support import PostgresTestCase,TestStore
from signal_contract import signal_event
from signal_service import publish,register,consume,acknowledge,administer,handler,execution_notice


def evidence(at=2000000):
    return dict(symbol='NEARUSD',source='kraken:Kraken:NEAR/USD',market='crypto',
                timeframe='4h',direction='bullish',confirmed_at=at,pivot2=at-60000,version='test-v1')

class ContractTests(unittest.TestCase):
    def test_stable_identity_and_private_data_excluded(self):
        signal=evidence()|dict(account='secret',stop=1,credentials={'key':'private'},asset_config={'token':'private'})
        first=signal_event('rsi-pb',signal)
        second=signal_event('rsi-pb',signal|dict(price2=12),kind='updated')
        self.assertEqual(first['signal_id'],second['signal_id'])
        self.assertNotEqual(first['event_id'],second['event_id'])
        self.assertNotIn('secret',json.dumps(first));self.assertNotIn('stop',first['evidence'])
        self.assertNotEqual(first['signal_id'],signal_event('rsi-pb',signal|dict(version='v2'))['signal_id'])
    def test_service_cannot_import_execution(self):
        root=Path(__file__).parent
        for name in ('paper_trading.py','demo_execution.py','execution_gateway.py'):
            self.assertFalse((root/name).exists())
        self.assertNotIn('paper_worker',(root/'platform_app.py').read_text())

class StreamTests(PostgresTestCase):
    def setUp(self):self.store=TestStore()
    def emit(self,value=None,**kwargs):
        with self.store.connect() as db:publish(db,'rsi-pb',value or evidence(),**kwargs)
    def project(self,**kwargs):
        with patch('signal_service.time.time',return_value=1000):return register(self.store,dict(project='trading-executor',**kwargs))
    def test_duplicate_and_rollback_never_publish_twice(self):
        self.emit();self.emit()
        with self.assertRaises(RuntimeError):
            with self.store.connect() as db:
                publish(db,'rsi-pb',evidence(3000000));raise RuntimeError()
        p=self.project(mode='replay');page=consume(self.store,p['token'])
        self.assertEqual(len(page['events']),1);self.assertEqual(page['next_cursor'],1)
        self.assertEqual(page['events'][0]['delivery_mode'],'replay')
    def test_live_registration_skips_history_and_catchup_is_not_tradeable(self):
        self.emit();p=self.project();self.emit(evidence(3000000));self.emit(evidence(4000000),origin='replay')
        page=consume(self.store,p['token']);self.assertEqual(len(page['events']),2)
        self.assertEqual([e['delivery_mode'] for e in page['events']],['live','replay'])
        with self.assertRaises(ValueError):consume(self.store,p['token'],after=0)
    def test_filtered_empty_pages_advance_and_ack_survives_retry(self):
        p=self.project(filters={'symbol':['OTHER']});self.emit()
        page=consume(self.store,p['token']);self.assertEqual(page['events'],[]);self.assertEqual(page['next_cursor'],1)
        self.assertEqual(consume(self.store,p['token']),page)
        acknowledge(self.store,p['token'],1);self.assertEqual(consume(self.store,p['token'])['next_cursor'],1)
        with self.assertRaises(ValueError):acknowledge(self.store,p['token'],2)
        with self.assertRaises(ValueError):consume(self.store,p['token'],after=2)
    def test_auth_revocation_rotation_and_immutable_filters(self):
        p=self.project()
        with self.assertRaises(PermissionError):consume(self.store,'wrong')
        with self.assertRaises(ValueError):self.project(filters={'symbol':['OTHER']})
        new=administer(self.store,p['project'],'rotate')
        with self.assertRaises(PermissionError):consume(self.store,p['token'])
        consume(self.store,new['token']);administer(self.store,p['project'],'revoke')
        with self.assertRaises(PermissionError):consume(self.store,new['token'])
    def test_updates_and_invalidations_are_separate_events(self):
        p=self.project();self.emit();self.emit(evidence()|dict(price2=11),kind='updated');self.emit(kind='invalidated')
        events=consume(self.store,p['token'])['events']
        self.assertEqual([e['kind'] for e in events],['detected','updated','invalidated'])
        self.assertEqual(len({e['signal_id'] for e in events}),1)
    def test_commit_order_does_not_skip_late_transactions(self):
        p=self.project();started=threading.Event();done=threading.Event()
        with self.store.connect() as db:
            publish(db,'rsi-pb',evidence())
            def second():
                started.set();self.emit(evidence(3000000));done.set()
            thread=threading.Thread(target=second);thread.start();started.wait(2)
            self.assertFalse(done.wait(.1))
        thread.join(5);self.assertTrue(done.is_set())
        self.assertEqual([e['cursor'] for e in consume(self.store,p['token'])['events']],[1,2])
    def test_notice_retains_telegram_route_and_is_deduplicated(self):
        p=self.project(execution_notices=True)
        user='00000000-0000-0000-0000-000000000001';inst='00000000-0000-0000-0000-000000000002'
        with self.store.connect() as db:
            db.execute('INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES(%s,%s,72,%s)',(inst,user,json.dumps({'live_positions':'-10002'})))
            db.execute('INSERT INTO public.chasing_preferences(installation,user_id,live_positions) VALUES(%s,%s,true)',(inst,user))
        body=dict(id='demo-test:exit',kind='demo_exit',at=3000000,root=evidence(),signal=evidence(3000000)|dict(symbol='kraken:Kraken:NEAR/USD',direction='bearish'),symbol='NEARUSDT')
        execution_notice(self.store,p['token'],body);execution_notice(self.store,p['token'],body)
        with self.store.connect() as db:
            rows=db.execute('SELECT event FROM chasing_outbox').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][0]['destination'],'live_positions')
        self.assertEqual(rows[0][0]['kind'],'demo_exit')
        from chasing_runtime import deliver_pending
        person=dict(user_id=user,telegram_user_id=72,live_positions=True,routes={'live_positions':'-10002'})
        with patch('chasing_runtime.people',return_value=[person]),patch('telegram_checklist.api',return_value=dict(chat=dict(id=-10002),message_id=99)) as api:
            deliver_pending(self.store,'fake')
        message=next(c.args[2] for c in api.call_args_list if c.args[1]=='sendMessage')
        self.assertIn('reduce-only close queued',message['text'])
        self.assertNotIn("I've exited",str(message['reply_markup']))
        with self.assertRaises(ValueError):execution_notice(self.store,p['token'],body|dict(at=4000000))
    def test_independent_runner_consumes_over_http_without_broker_credentials(self):
        p=self.project();self.emit();self.emit(evidence(3000000),origin='replay')
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store,'a'*32))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                target=Path(directory)/'execution.sqlite3'
                env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT')}
                env.update(SIGNAL_API_URL=f'http://127.0.0.1:{server.server_port}',SIGNAL_PROJECT_TOKEN=p['token'],SIGNAL_PROJECT=p['project'])
                runner=Path(__file__).parent/'projects/trading-executor'
                import sys,sqlite3
                for _ in range(2):
                    result=subprocess.run([sys.executable,str(runner/'runner.py'),'--database',str(target),'--once'],cwd=directory,env=env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                with closing(sqlite3.connect(target)) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM inbox').fetchone()[0],2)
                    self.assertEqual(db.execute('SELECT enabled FROM control').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT count(*) FROM demo_execution_trades').fetchone()[0],0)
                req=urllib.request.Request(env['SIGNAL_API_URL']+'/v1/events')
                with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
                self.assertEqual(caught.exception.code,401)
        finally:server.shutdown();server.server_close();thread.join()

class LifecycleTests(PostgresTestCase):
    def test_pivot_invalidation_is_not_a_second_telegram_alert(self):
        from scanner import Candle
        from signal_service import reconcile_provisional
        store=TestStore();value=evidence()|dict(symbol='kraken:Kraken:NEAR/USD',signal_status='provisional',price2=10)
        with store.connect() as db:
            publish(db,'rsi',value)
            bars=[Candle(value['pivot2'],value['confirmed_at'],10,11,9,10),Candle(value['confirmed_at'],value['confirmed_at']+60000,10,11,8,9)]
            reconcile_provisional(db,value['source'],'4h',bars,dict(skill_hash='test-v1',pivot_right=1))
            reconcile_provisional(db,value['source'],'4h',bars,dict(skill_hash='test-v1',pivot_right=1))
            events=[r[0] for r in db.execute('SELECT payload FROM signal_events ORDER BY cursor')]
            self.assertEqual([e['kind'] for e in events],['detected','invalidated'])
            self.assertEqual(events[0]['signal_id'],events[1]['signal_id'])
            self.assertEqual(db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],0)

class CutoverTests(PostgresTestCase):
    def test_archive_is_private_and_preserves_orders(self):
        import psycopg
        from test_postgres_support import test_dsn
        store=TestStore()
        with store.connect() as db:db.execute('INSERT INTO paper_trades VALUES(%s,%s)',('original',json.dumps(dict(order={'id':'broker-id'}))))
        script=(Path(__file__).parent/'cloud/execution-cutover.sql').read_text().replace('BEGIN;','').replace('COMMIT;','')
        with psycopg.connect(test_dsn()) as db:
            with db.transaction(force_rollback=True):
                db.execute(script,prepare=False)
                self.assertEqual(db.execute("SELECT payload::jsonb->'order'->>'id' FROM execution_legacy.paper_trades").fetchone()[0],'broker-id')
                self.assertIsNone(db.execute("SELECT to_regclass('bot_runtime.paper_trades')").fetchone()[0])
                self.assertFalse(db.execute("SELECT has_schema_privilege('trading_worker','execution_legacy','USAGE')").fetchone()[0])
