import io
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg import sql
from test_postgres_support import reset,test_dsn
from platform_app import Store,load_rules
from postgres_store import close_pool,WorkerGuard,WorkerOwned
from test_platform import candle_example
from test_scanner import fixture

INST='00000000-0000-0000-0000-000000000002'
OWNER='00000000-0000-0000-0000-000000000001'
VIEWER='00000000-0000-0000-0000-000000000003'

@unittest.skipUnless(os.environ.get('TRADING_TEST_DATABASE_URL'),'requires disposable Postgres')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        reset();self.store=Store()
        with psycopg.connect(test_dsn()) as db:
            db.execute('DELETE FROM public.dashboard_members WHERE installation=%s',(INST,))
            db.execute('INSERT INTO auth.users VALUES(%s),(%s) ON CONFLICT DO NOTHING',(OWNER,VIEWER))
            db.execute('INSERT INTO public.dashboard_installations VALUES(%s,%s) ON CONFLICT DO NOTHING',(INST,OWNER))
            db.execute('INSERT INTO bot_runtime.runtime_control(id,installation,enabled,effects_enabled) VALUES(1,%s,true,true)',(INST,))
    def tearDown(self):
        if self.store.guard:self.store.guard.close()
        close_pool()
    def api(self,path,body=None,user=OWNER):
        from psycopg.types.json import Jsonb
        with psycopg.connect(test_dsn()) as db:
            db.execute('SELECT set_config(\'test.auth_uid\',%s,true)',(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_api(%s,%s,%s)',(INST,path,Jsonb(body or {}))).fetchone()[0]
    def test_missing_url_and_tls_fail_closed(self):
        from postgres_store import settings
        with patch.dict(os.environ,{'TRADING_DATABASE_URL':''}):
            with self.assertRaises(ValueError):settings()
        with patch.dict(os.environ,{'TRADING_DATABASE_URL':'host=remote dbname=x','TRADING_DB_TEST':'0'}):
            with self.assertRaises(ValueError):settings()
    def test_shared_runtime_no_sqlite_or_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            one=Store(Path(folder)/'never.sqlite3');two=Store(Path(folder)/'never-either.sqlite3')
            self.assertTrue(one.insert(candle_example()))
            self.assertFalse(two.insert(candle_example()))
            self.assertEqual(len(two.rows()),1);self.assertEqual(list(Path(folder).iterdir()),[])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='bot_runtime' AND table_name LIKE 'cloud_%'").fetchone()[0],0)
        self.assertNotIn('sync_thread',Path('platform_app.py').read_text())
    def test_postgres_claims_are_atomic_and_held_work_stays_held(self):
        from scanner_control import request,claim
        key=request(self.store)['id']
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:claim(self.store),range(6)))
        self.assertEqual(results.count(key),1)
        other=request(self.store,'00000000-0000-0000-0000-000000000009')['id']
        with self.store.connect() as db:db.execute("INSERT INTO migration_holds VALUES('scanner_commands',%s,'review')",(other,))
        self.assertIsNone(claim(self.store))
    def test_owner_guard_rejects_duplicate_and_revokes_on_loss(self):
        first=WorkerGuard()
        try:
            with self.assertRaises(WorkerOwned):WorkerGuard()
            first.db.close()
            first.check()  # Same durable generation can reconnect and resume.
            first.db.close()
            replacement=WorkerGuard()
            try:
                with self.assertRaises(WorkerOwned):first.check()
            finally:replacement.close()
            with self.assertRaises(WorkerOwned):first.check()
        finally:first.close()
    def test_transaction_rollback_and_reconnect(self):
        with self.assertRaises(RuntimeError):
            with self.store.connect() as db:
                db.execute("INSERT INTO notification_queue(id,text) VALUES('x','text')")
                raise RuntimeError('abort')
        with self.store.connect() as db:self.assertIsNone(db.execute("SELECT 1 FROM notification_queue WHERE id='x'").fetchone())
        close_pool()
        self.assertTrue(self.store.insert(candle_example()))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM signals').fetchone()[0],1)
    def test_feedback_shared_permissions_and_revision(self):
        from finding_feedback import finding_id,save
        signal=candle_example();self.store.insert(signal);key=finding_id('live',signal)
        read=self.api('/api/findings?source=live')['findings'][0]
        self.assertEqual(read['id'],key)
        self.api('/api/feedback',dict(source='live',finding_id=key,revision=0,rating=5,comment='hosted'))
        with self.assertRaises(LookupError):save(self.store,Path('.'),dict(source='live',finding_id=key,revision=0,rating=4,comment='stale'))
        save(self.store,Path('.'),dict(source='live',finding_id=key,revision=1,rating=4,comment='local'))
        self.assertEqual(self.api('/api/findings?source=live')['findings'][0]['feedback']['comment'],'local')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM feedback_history').fetchone()[0],2)
        with self.assertRaises(psycopg.Error):self.api('/api/findings',user=VIEWER)
        with psycopg.connect(test_dsn()) as db:db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer') ON CONFLICT DO NOTHING",(INST,VIEWER))
        self.assertEqual(len(self.api('/api/findings',user=VIEWER)['findings']),1)
        with self.assertRaises(psycopg.Error):self.api('/api/feedback',dict(source='live',finding_id=key,revision=2,rating=1,comment='forbidden'),VIEWER)
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE authenticated')
            with self.assertRaises(psycopg.Error):db.execute('SELECT * FROM bot_runtime.signals')
    def test_state_and_config_shared_with_hosted(self):
        from managed_assets import ensure,get,change
        from finding_feedback import finding_id
        cfg=dict(timeframes=['4h'],assets=[dict(id='TEST',provider='csv',enabled=False,path='test.csv',timeframes=['4h'])])
        ensure(self.store,cfg)
        self.assertEqual(self.api('/api/assets')['revision'],1)
        self.api('/api/assets',dict(action='bulk',revision=1,tickers='NEW'))
        self.assertEqual(get(self.store)['revision'],2)
        with self.assertRaises(LookupError):change(self.store,dict(action='bulk',revision=1,tickers='STALE'))
        signal=candle_example();self.store.insert(signal)
        self.api('/api/finding-state',dict(source='live',finding_id=finding_id('live',signal),revision=0,action='delete'))
        self.assertEqual(self.api('/api/findings')['findings'],[])

    def test_rsi_pb_hosted_pagination_and_owner_edits_cancel_pending_setups(self):
        from managed_assets import ensure,get
        cfg=dict(timeframes=['4h'],assets=[dict(id='TEST',provider='csv',enabled=False,path='test.csv',timeframes=['4h'])])
        ensure(self.store,cfg)
        with self.store.connect() as db:
            db.executemany('INSERT INTO rsi_pb_signals VALUES(%s,%s,%s)',[(str(i),i,json.dumps({'strategy':'rsi-pb','confirmed_at':i})) for i in range(73)])
            db.execute("INSERT INTO rsi_pb_state VALUES('TEST@csv@v1','4h',123)")
            db.execute("INSERT INTO rsi_pb_pending VALUES('waiting','TEST@csv@v1','4h','{}')")
        self.assertEqual(len(self.api('/api/rsi-pb?page=0')['signals']),50)
        second=self.api('/api/rsi-pb?page=1')
        self.assertEqual(len(second['signals']),23)
        self.assertFalse(second['has_more'])
        import rsi_pb
        self.assertEqual(second['rules'],rsi_pb.load_rules())
        asset=get(self.store)['config']['assets'][0]|dict(scan_strategies=['rsi'])
        self.api('/api/assets',dict(action='save',revision=1,asset=asset))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rsi_pb_pending').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM rsi_pb_state').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM rsi_pb_signals').fetchone()[0],73)

    def test_import_preserves_rsi_pb_baselines_pending_setups_and_history(self):
        from migration.legacy import prepare,apply
        with self.store.connect() as db:db.execute('UPDATE runtime_control SET enabled=false')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'old.db'
            with closing(sqlite3.connect(source)) as old,old:
                old.executescript('CREATE TABLE rsi_pb_state(feed TEXT,timeframe TEXT,last_end INTEGER,PRIMARY KEY(feed,timeframe)); CREATE TABLE rsi_pb_pending(id TEXT PRIMARY KEY,feed TEXT,timeframe TEXT,payload TEXT); CREATE TABLE rsi_pb_signals(id TEXT PRIMARY KEY,confirmed_at INTEGER,payload TEXT);')
                old.execute("INSERT INTO rsi_pb_state VALUES('saved-feed','4h',123)")
                old.execute("INSERT INTO rsi_pb_pending VALUES('waiting','saved-feed','4h','{\"version\":\"saved-v1\"}')")
                old.execute("INSERT INTO rsi_pb_signals VALUES('original-id',100,'{\"confirmed_at\":100}')")
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                plan=prepare(db,source,INST)
                self.assertEqual(apply(db,plan,INST)['mismatches'],[])
                self.assertEqual(db.execute('SELECT last_end FROM rsi_pb_state').fetchone()[0],123)
                self.assertEqual(db.execute('SELECT id,payload FROM rsi_pb_pending').fetchone(),('waiting','{"version":"saved-v1"}'))
                self.assertEqual(db.execute('SELECT id FROM rsi_pb_signals').fetchone()[0],'original-id')
                self.assertEqual(db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],0)
    def test_all_strategies_scan_and_history_is_not_capped(self):
        from scanner import scan_once
        from pin_bars import scan as pin_scan,load_rules as pin_rules
        from bb_pin_bars import scan as bb_scan,load_rules as bb_rules
        bars=fixture();cfg=dict(timeframes=['4h'],assets=[dict(id='EURUSD',provider='twelvedata',symbol='EUR/USD',enabled=True,timeframes=['4h'],scan_strategies=['rsi','pin-bars','bb-pin-bars'])])
        with patch('scanner.fetch_twelve_data',return_value=bars[:250]):scan_once(self.store,cfg,load_rules(),bars[249].end)
        with patch('scanner.fetch_twelve_data',return_value=bars):coverage=scan_once(self.store,cfg,load_rules(),bars[-1].end)
        self.assertEqual(coverage[0]['status'],'scanned')
        with self.store.connect() as db:
            for table in ('pin_bar_state','bb_pin_bar_state','scan_state','candle_cache'):
                self.assertGreater(db.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0],0)
            db.executemany('INSERT INTO pin_bar_signals VALUES(%s,%s,%s)',((str(i),i,json.dumps(dict(symbol='T',confirmed_at=i))) for i in range(650)))
        self.assertEqual(len(self.api('/api/pin-bars?page=12')['signals']),50)
    def test_tiingo_budget_survives_network_error_and_restart(self):
        from tiingo_feed import request_json
        with patch.dict(os.environ,TIINGO_API_KEY='test-token',TIINGO_REQUESTS_PER_HOUR='1'),patch('urllib.request.urlopen',side_effect=OSError('offline')) as send:
            with self.assertRaises(ValueError):request_json('EUR/USD','1hour','a','b')
            close_pool()
            with self.assertRaisesRegex(ValueError,'budget'):request_json('EUR/USD','1hour','a','b')
            self.assertEqual(send.call_count,1)
    def test_telegram_commit_and_retry_suppression_without_real_send(self):
        from telegram_delivery import deliver
        self.store.acquire_worker();send=Mock(return_value=123)
        self.assertEqual(deliver(self.store,'123:test','1','hello','event',send),'sent')
        self.assertEqual(deliver(self.store,'123:test','1','hello','event',send),'suppressed')
        self.assertEqual(send.call_count,1)
    def test_paper_ambiguous_submission_reconciles_without_second_post(self):
        from test_paper_trading import FakeBroker,signal,NOW
        import paper_trading
        broker=FakeBroker();broker.fail_after_post=True
        with patch.dict(os.environ,TRADING_BOT_ENV='prod',PAPER_TRADING_ENABLED='true'),patch('momentum.view',return_value=dict(signals=[signal()])):
            paper_trading.tick(self.store,broker,NOW)
            paper_trading.tick(self.store,broker,NOW+1000)
        self.assertEqual(broker.post_count,1)
    def test_schema_worker_has_no_ddl_or_activation_right(self):
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE trading_worker')
            db.execute('SELECT * FROM bot_runtime.signals')
            with self.assertRaises(psycopg.Error):db.execute('UPDATE bot_runtime.runtime_control SET enabled=false')
    def test_runtime_float_output_preserves_full_precision(self):
        value=1.9973369999999998
        with self.store.connect() as db:
            self.assertEqual(db.execute('SHOW extra_float_digits').fetchone(),('3',))
            self.assertEqual(db.execute('SELECT %s::float8',(value,)).fetchone(),(value,))
    def test_snapshot_import_rerun_preserves_ids_and_holds_delivery(self):
        from migration.legacy import snapshot,prepare,apply,verify
        with self.store.connect() as db:db.execute('UPDATE runtime_control SET enabled=false')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'legacy.db';snap=Path(folder)/'consistent.db'
            with closing(sqlite3.connect(source)) as old,old:
                old.execute('CREATE TABLE signals(id TEXT PRIMARY KEY,payload TEXT NOT NULL,received REAL NOT NULL,status TEXT,attempts INTEGER,next_attempt REAL,error TEXT)')
                old.execute('INSERT INTO signals VALUES(?,?,?,?,?,?,?)',('stable-id',json.dumps(candle_example()),10.123456789012345,'pending',2,15,None))
                old.execute('CREATE TABLE pin_bar_signals(id TEXT PRIMARY KEY,confirmed_at INTEGER,payload TEXT)')
                old.executemany('INSERT INTO pin_bar_signals VALUES(?,?,?)',((str(i),i,json.dumps({'id':i})) for i in range(750)))
                old.execute('CREATE TABLE alert_reviews(slot TEXT PRIMARY KEY,completed REAL)')
                old.execute('INSERT INTO alert_reviews VALUES(?,?)',('legacy-review',123.5))
                old.execute('CREATE TABLE cloud_seen(id TEXT PRIMARY KEY,payload TEXT)')
                old.executemany('INSERT INTO cloud_seen VALUES(?,?)',
                    ((str(i),json.dumps({'note':'preserved é','value':i})) for i in range(1501)))
            snapshot(source,snap)
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                db.execute('SET LOCAL extra_float_digits=0')
                plan=prepare(db,snap,INST);self.assertEqual(plan['conflicts'],[])
                self.assertEqual(apply(db,plan,INST)['mismatches'],[])
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                plan=prepare(db,snap,INST);self.assertEqual(apply(db,plan,INST)['mismatches'],[])
                self.assertEqual(db.execute("SELECT attempts FROM signals WHERE id='stable-id'").fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM pin_bar_signals').fetchone()[0],750)
                self.assertEqual(db.execute("SELECT count(*) FROM migration_holds WHERE queue='signals'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT local_value FROM migration_audit WHERE table_name='alert_reviews'").fetchone()[0], [{'slot':'legacy-review','completed':123.5}])
                manifest=db.execute("SELECT local_value FROM migration_audit WHERE table_name='cloud_seen' AND decision='archive-chunked'").fetchone()[0]
                self.assertEqual((manifest['rows'],manifest['chunks']),(1501,4))
                chunks=db.execute("SELECT local_value FROM migration_audit WHERE table_name='cloud_seen' AND decision='archive-chunk' ORDER BY row_key").fetchall()
                restored=[row for chunk, in chunks for row in chunk]
                self.assertEqual(restored,[dict(id=str(i),payload=json.dumps({'note':'preserved é','value':i})) for i in range(1501)])
                db.execute("UPDATE migration_audit SET local_value='[]'::jsonb WHERE table_name='cloud_seen' AND row_key='legacy-archive:chunk:00000001'")
                with self.assertRaisesRegex(ValueError,'Archive chunk content mismatch'):verify(db,plan)
                db.rollback()

    def test_conflicting_csv_files_keep_separate_decisions_and_audit_rows(self):
        from migration.legacy import prepare,apply
        with self.store.connect() as db:db.execute('UPDATE runtime_control SET enabled=false')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'old.db'
            with closing(sqlite3.connect(source)) as old,old:
                old.execute('CREATE TABLE backtest_candle_cache(feed TEXT,timeframe TEXT,start INTEGER,end INTEGER,open REAL,high REAL,low REAL,close REAL)')
                old.execute("INSERT INTO backtest_candle_cache VALUES('BTCUSD','weekly',1388361600000,1388966400000,737,980,737,904)")
            directories=[Path(folder)/name for name in ('full','partial')]
            for directory,opening,low in zip(directories,(742,737),(730,737)):
                directory.mkdir()
                (directory/'BTCUSD-weekly.csv').write_text('timestamp,closed_at,open,high,low,close\n2013-12-30T00:00:00+00:00,2014-01-06T00:00:00+00:00,'+str(opening)+',980,'+str(low)+',904\n')
            prefix='backtest_candle_cache:["BTCUSD","weekly",1388361600000]:csv:'
            choices={prefix+'full/BTCUSD-weekly.csv':'local',prefix+'partial/BTCUSD-weekly.csv':'hosted'}
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                plan=prepare(db,source,INST,resolutions=choices,history_dirs=directories)
                self.assertEqual(plan['conflicts'],[])
                self.assertEqual(apply(db,plan,INST)['mismatches'],[])
                self.assertEqual(db.execute('SELECT open,low FROM backtest_candle_cache').fetchone(),(742,730))
                self.assertEqual(db.execute("SELECT row_key FROM migration_audit WHERE table_name='backtest_candle_cache' ORDER BY row_key").fetchall(),[(k.split(':',1)[1],) for k in sorted(choices)])

    def test_hosted_replay_completes_without_linux_worker(self):
        from psycopg.types.json import Jsonb
        from finding_feedback import finding_id,save
        signal=candle_example()
        with self.store.connect() as db:
            db.execute('INSERT INTO backtest_candle_cache VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',('TEST','4h',1,2,100,101,99,100))
        job=self.api('/api/backtests',dict(assets=['TEST'],timeframes=['4h'],strategy='warmup'))
        self.assertEqual(job['executor'],'edge')
        signal=signal|dict(symbol='TEST',timeframe='4h')
        supplied_id='f'*64
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE service_role')
            history=db.execute("SELECT public.dashboard_replay_history(%s,'candle','TEST','4h',0)",(INST,)).fetchone()[0]
            self.assertEqual(history[0]['payload']['start'],1)
            result=db.execute('SELECT public.dashboard_finish_backtest(%s,%s,%s,%s)',(INST,job['id'],Jsonb([dict(asset='TEST',timeframe='4h',status='replayed',signals=1)]),Jsonb([dict(id=supplied_id,signal=signal,followups={})]))).fetchone()[0]
            self.assertTrue(result['completed'])
        # No guard, worker thread or network was started; both APIs see the result.
        self.assertIsNone(self.store.guard)
        shown=self.api('/api/findings?source=backtest')['findings'][0]
        self.assertEqual(shown['id'],supplied_id)
        save(self.store,Path('.'),dict(source='backtest',finding_id=supplied_id,revision=0,rating=4,comment='same record'))
        self.assertEqual(self.api('/api/findings?source=backtest')['findings'][0]['feedback']['rating'],4)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM backtest_jobs WHERE id=%s',(job['id'],)).fetchone()[0],'completed')

    def test_momentum_sql_matches_python_outcomes(self):
        from momentum import outcome
        from scanner import Candle
        from psycopg.types.json import Jsonb
        from dataclasses import asdict
        signal=dict(market='crypto',direction='buy',confirmed_at=1,entry_min=95,entry_max=105,stop=90,target=125,rules=dict(max_hold=2,cost_bps=10))
        for bars in ([],[Candle(10,20,100,126,89,110)],[Candle(10,20,100,110,95,105),Candle(20,30,105,110,96,106)]):
            expected=outcome(signal,bars)
            with self.store.connect() as db:
                actual=db.execute('SELECT bot_runtime.momentum_outcome(%s,%s)',(Jsonb(signal),Jsonb([asdict(b) for b in bars]))).fetchone()[0]
            self.assertEqual(actual.keys(),expected.keys())
            for key in actual:
                if isinstance(actual[key],float):self.assertAlmostEqual(actual[key],expected[key])
                else:self.assertEqual(actual[key],expected[key])

    def test_hosted_followup_csv_and_legacy_fences(self):
        from finding_feedback import finding_id
        from psycopg.types.json import Jsonb
        signal=candle_example();self.store.insert(signal)
        result=self.api('/api/followup?source=live&finding='+finding_id('live',signal))
        self.assertEqual(result['followup']['status'],'unavailable')
        report=dict(id='b'*64,strategy='pin-bars',signals=[],generated_at=1,filename='x.csv',symbol='TEST',timeframe='4h',start_at=1,end_at=2,signal_count=0)
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE service_role')
            db.execute('SELECT public.dashboard_save_csv_backtest(%s,%s)',(INST,Jsonb(report)))
        from csv_backtests import view
        self.assertEqual(view(self.store)['report']['id'],report['id'])
        self.assertEqual(self.api('/api/csv-backtests')['report']['id'],report['id'])
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE service_role')
            with self.assertRaises(psycopg.Error):db.execute('SELECT public.dashboard_ingest(%s,%s,0)',(INST,Jsonb([])))

    def test_import_conflict_is_explicit_and_both_values_are_audited(self):
        from migration.legacy import snapshot,prepare,apply
        from psycopg.types.json import Jsonb
        import copy
        local=dict(assets=[dict(id='LOCAL')]);hosted=dict(assets=[dict(id='HOSTED')])
        with self.store.connect() as db:db.execute('UPDATE runtime_control SET enabled=false')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'old.db';snap=Path(folder)/'snapshot.db'
            with closing(sqlite3.connect(source)) as old,old:
                old.execute('CREATE TABLE managed_config(id INTEGER PRIMARY KEY,revision INTEGER,applied_revision INTEGER,payload TEXT)')
                old.execute('INSERT INTO managed_config VALUES(1,1,1,?)',(json.dumps(local),))
                old.execute('CREATE TABLE finding_followup(finding_id TEXT PRIMARY KEY,results TEXT,calculated_at REAL)')
                old.execute('INSERT INTO finding_followup VALUES(?,?,?)',('reviewed-finding',json.dumps({'status':'local'}),1))
            snapshot(source,snap)
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                db.execute("INSERT INTO public.dashboard_records(installation,kind,key,payload) VALUES(%s,'config','current',%s) ON CONFLICT(installation,kind,key) DO UPDATE SET payload=excluded.payload",(INST,Jsonb(dict(revision=2,applied_revision=1,config=hosted))))
                db.execute("INSERT INTO public.dashboard_followups(installation,finding_id,results) VALUES(%s,'reviewed-finding',%s)",(INST,Jsonb({'status':'hosted'})))
                plan=prepare(db,snap,INST)
                self.assertEqual(plan['conflicts'][0]['key'],'managed_config:1')
                self.assertIn('finding_followup:reviewed-finding',[c['key'] for c in plan['conflicts']])
                plan=prepare(db,snap,INST,{'managed_config:1':'hosted','finding_followup:reviewed-finding':'hosted'})
                self.assertEqual(plan['conflicts'],[])
                self.assertEqual(apply(db,plan,INST)['mismatches'],[])
                self.assertEqual(json.loads(db.execute('SELECT payload FROM managed_config').fetchone()[0]),hosted)
                left,right=db.execute("SELECT local_value,hosted_value FROM migration_audit WHERE table_name='managed_config'").fetchone()
                self.assertEqual(json.loads(left['payload']),local)
                self.assertEqual(json.loads(right['payload']),hosted)
                self.assertEqual(json.loads(db.execute("SELECT results FROM finding_followup WHERE finding_id='reviewed-finding'").fetchone()[0]),{'status':'hosted'})
                self.assertEqual(db.execute("SELECT payload->'results' FROM legacy_artifacts WHERE kind='hosted_table:dashboard_followups'").fetchone()[0],{'status':'hosted'})
            # Retain no public fixture edits for the next test.
            with psycopg.connect(test_dsn()) as db:
                db.execute('DELETE FROM public.dashboard_records WHERE installation=%s',(INST,))
                db.execute('DELETE FROM public.dashboard_followups WHERE installation=%s',(INST,))

    def test_import_mixed_feedback_ids_preserves_existing_and_allocates_after_max(self):
        from migration.legacy import prepare,apply
        from psycopg.types.json import Jsonb
        evidence=json.dumps({'asset':'TEST'})
        with self.store.connect() as db:db.execute('UPDATE runtime_control SET enabled=false')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'old.db'
            with closing(sqlite3.connect(source)) as old,old:
                old.execute('CREATE TABLE feedback_history(sequence INTEGER PRIMARY KEY,finding_id TEXT,source TEXT,evidence TEXT,rating INTEGER,comment TEXT,revision INTEGER,updated_at REAL)')
                old.executemany('INSERT INTO feedback_history VALUES(?,?,?,?,?,?,?,?)',[(1,'unchanged','live',evidence,3,'unchanged',1,1),(100,'existing','live',evidence,3,'old',1,1)])
            try:
                with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                    for key in ('existing','hosted-only'):
                        payload=dict(finding_id=key,source='live',evidence=json.loads(evidence),rating=4,comment='hosted',revision=1,updated_at=2)
                        db.execute("INSERT INTO public.dashboard_records(installation,kind,key,payload) VALUES(%s,'feedback_history',%s,%s)",(INST,key+':1',Jsonb(payload)))
                    plan=prepare(db,source,INST,{'feedback_history:existing:1':'hosted'})
                    self.assertEqual(plan['conflicts'],[])
                    # Exercise generated IDs even when their group arrives first.
                    plan['tables']['feedback_history'].sort(key=lambda r:'sequence' in r)
                    self.assertEqual(apply(db,plan,INST)['mismatches'],[])
                    self.assertEqual(db.execute('SELECT finding_id,sequence FROM feedback_history ORDER BY sequence').fetchall(),[('unchanged',1),('existing',100),('hosted-only',101)])
                    self.assertEqual(apply(db,plan,INST)['mismatches'],[])
            finally:
                with psycopg.connect(test_dsn()) as db:db.execute("DELETE FROM public.dashboard_records WHERE installation=%s AND kind='feedback_history' AND key IN ('existing:1','hosted-only:1')",(INST,))

    def test_recovery_exports_new_files_without_touching_fallback(self):
        from migration.recovery import export
        from migration.legacy import snapshot
        from contextlib import closing
        self.store.insert(candle_example())
        with self.store.connect() as db:
            db.execute('UPDATE runtime_control SET enabled=false')
            db.execute("INSERT INTO migration_holds VALUES('signals',(SELECT id FROM signals LIMIT 1),'review')")
        with tempfile.TemporaryDirectory() as folder:
            baseline=Path(folder)/'baseline.db'
            with closing(sqlite3.connect(baseline)) as old,old:
                old.execute('CREATE TABLE signals(id TEXT PRIMARY KEY,payload TEXT,received REAL,status TEXT,attempts INTEGER,next_attempt REAL,error TEXT)')
            original=baseline.read_bytes();target=Path(folder)/'recovery'
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:export(db,baseline,target,INST)
            self.assertEqual(original,baseline.read_bytes())
            with closing(sqlite3.connect(target/'signals.sqlite3')) as recovered:
                self.assertEqual(recovered.execute('SELECT status FROM signals').fetchone()[0],'cancelled')
                self.assertEqual(recovered.execute('PRAGMA integrity_check').fetchone(),('ok',))
            self.assertTrue((target/'manifest.json').is_file())

    def test_static_dashboard_has_public_config_and_no_local_api(self):
        from dashboard_server import handler
        from http.server import ThreadingHTTPServer
        import urllib.request,urllib.error
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(Path('.')))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            with urllib.request.urlopen(base+'/web/config.js') as response:
                config=response.read().decode()
            self.assertIn('publishableKey',config)
            self.assertNotIn('TRADING_DATABASE_URL',config)
            for path in ('/web/style.css','/web/chart.js','/web/vendor/lightweight-charts.js'):
                with urllib.request.urlopen(base+path) as response:self.assertEqual(response.status,200)
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/findings')
        finally:server.shutdown();server.server_close();thread.join()

    def test_multiple_tiingo_snapshots_conservatively_preserve_all_reservations(self):
        from migration.legacy import snapshot,prepare,apply
        with self.store.connect() as db:db.execute('UPDATE runtime_control SET enabled=false')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'empty.db'
            with closing(sqlite3.connect(source)) as old,old:old.execute('CREATE TABLE signals(id TEXT PRIMARY KEY,payload TEXT,received REAL,status TEXT,attempts INTEGER,next_attempt REAL,error TEXT)')
            snapshots=[]
            for n in (1,2):
                cache=Path(folder)/f'cache{n}.db';target=Path(folder)/f'cache{n}.snapshot'
                with closing(sqlite3.connect(cache)) as old,old:
                    old.execute('CREATE TABLE requests(at REAL NOT NULL)');old.execute('INSERT INTO requests VALUES(?)',(100+n,))
                    old.execute('CREATE TABLE responses(key TEXT PRIMARY KEY,fetched REAL,payload TEXT)')
                snapshot(cache,target);snapshots.append(target)
            snap=Path(folder)/'signals.snapshot';snapshot(source,snap)
            with psycopg.connect(test_dsn(),options='-c search_path=bot_runtime,pg_catalog') as db:
                plan=prepare(db,snap,INST,tiingo=snapshots)
                self.assertEqual(apply(db,plan,INST)['mismatches'],[])
                self.assertEqual(db.execute('SELECT count(*) FROM tiingo_requests').fetchone()[0],2)

    def test_preflight_failure_never_prints_secrets(self):
        import importlib.util
        import contextlib
        spec=importlib.util.spec_from_file_location('preflight',Path('scripts/test-postgres-connection.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'worker.env';config.write_text('TRADING_DATABASE_URL=postgresql://worker:never-print-this@db.invalid/postgres?sslmode=verify-full')
            output=io.StringIO()
            with patch('sys.argv',['preflight','--env-file',str(config)]),patch('psycopg.connect',side_effect=psycopg.OperationalError('never-print-this')),contextlib.redirect_stderr(output),patch.dict(os.environ):
                self.assertEqual(module.main(),1)
            self.assertNotIn('never-print-this',output.getvalue())
            self.assertIn('OperationalError',output.getvalue())

    def test_imported_summary_does_not_override_current_pin_bar_rules(self):
        from psycopg.types.json import Jsonb
        from pin_bars import load_rules as ema_rules
        from bb_pin_bars import load_rules as bb_rules
        archived={'rules':{'max_short_wick_ratio':0.2}}
        with self.store.connect() as db:
            db.execute("INSERT INTO legacy_artifacts VALUES('summary','pin-bars-live',%s)",(Jsonb(archived),))
            db.execute("INSERT INTO legacy_artifacts VALUES('summary','bb-pin-bars-live',%s)",(Jsonb(archived),))
        for endpoint,rules in (('/api/pin-bars',ema_rules()),('/api/bb-pin-bars',bb_rules())):
            self.assertEqual(self.api(endpoint)['rules']['max_short_wick_ratio'],rules['max_short_wick_ratio'])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT payload FROM legacy_artifacts WHERE key='pin-bars-live'").fetchone()[0],archived)

if __name__=='__main__':unittest.main()
