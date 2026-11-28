"""Read dispatcher parity and permissions, only on a disposable PostgreSQL DB."""
import json
import os
import unittest
from pathlib import Path
import psycopg
from test_postgres_support import test_dsn
from test_signal_query import INST, OWNER, VIEWER, OUTSIDER

@unittest.skipUnless(os.environ.get('TRADING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class DashboardReadTests(unittest.TestCase):
    def setUp(self):
        self.db=psycopg.connect(test_dsn())
        self.addCleanup(self.db.close)
        self.db.execute('TRUNCATE bot_runtime.backtest_runs,bot_runtime.signals,bot_runtime.finding_keys,bot_runtime.legacy_findings,bot_runtime.finding_state,bot_runtime.finding_feedback,bot_runtime.finding_followup,bot_runtime.scanner_status,bot_runtime.runtime_control')
        for uid in (OWNER,VIEWER,OUTSIDER):self.db.execute('INSERT INTO auth.users VALUES(%s) ON CONFLICT DO NOTHING',(uid,))
        self.db.execute('INSERT INTO public.dashboard_installations VALUES(%s,%s) ON CONFLICT DO NOTHING',(INST,OWNER))
        self.db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer') ON CONFLICT DO NOTHING",(INST,VIEWER))
        self.db.execute('INSERT INTO bot_runtime.runtime_control(id,installation,enabled,effects_enabled) VALUES(1,%s,true,false)',(INST,))
        self.optimized=self.db.execute("SELECT pg_get_functiondef('bot_runtime.api(uuid,text,jsonb)'::regprocedure)").fetchone()[0]
        self.baseline=self.optimized.replace("IF source='backtest' THEN RETURN bot_runtime.backtest_page(operation);END IF;",'')
        self.db.execute("INSERT INTO bot_runtime.scanner_status(id,payload) VALUES(1,%s)",(json.dumps(dict(coverage=[dict(asset='TEST',status='ok')],versions=dict(ui='test'))),))

    def call(self,op='/api/findings?source=backtest',user=VIEWER):
        with self.db.transaction():
            self.db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            self.db.execute('SET LOCAL ROLE authenticated')
            value=self.db.execute("SELECT public.dashboard_api(%s,%s,'{}')",(INST,op)).fetchone()[0]
            self.db.execute('RESET ROLE')
            return value

    def report(self,run,signals):
        self.db.execute('INSERT INTO bot_runtime.backtest_runs(id,report) VALUES(%s,%s)',(run,json.dumps(dict(results=[dict(asset='TEST',signals=signals,followups=[],status='ok')]))))
        for s in signals:
            self.db.execute("INSERT INTO bot_runtime.finding_keys(source,evidence_hash,finding_id) VALUES('backtest',bot_runtime.signal_payload_hash(%s),%s) ON CONFLICT DO NOTHING",(json.dumps(s),s['tag']))

    def parity(self,op):
        self.db.execute(self.baseline)
        old=self.call(op)
        self.db.execute(self.optimized)
        new=self.call(op)
        self.assertEqual(new,old)
        return new

    def test_all_sorts_pages_states_and_latest_report_match_previous_query(self):
        signals=[dict(symbol=f'S{i%4}',timeframe=['1h','4h','daily'][i%3],direction=['bullish','bearish'][i%2],confirmed_at=1000+i//2,tag=f'{i:03}') for i in range(108)]
        self.report(1,signals[:3]+[dict(signals[0],tag='old-only')])
        self.report(2,signals+signals[:1])
        self.db.execute("INSERT INTO bot_runtime.finding_state VALUES('001','deleted',1,0),('002','archived',2,0)")
        self.db.execute("INSERT INTO bot_runtime.finding_feedback VALUES('003','backtest','{}',4,'keep',2,0)")
        self.db.execute("INSERT INTO bot_runtime.finding_followup(finding_id,results,calculated_at) VALUES('003',%s,0)",(json.dumps(dict(recovery=dict(rating=3))),))
        self.db.execute("INSERT INTO bot_runtime.legacy_findings VALUES('backtest','003',%s),('backtest','legacy-only',%s)",(json.dumps(dict(id='003',signal=signals[3])),json.dumps(dict(id='legacy-only',signal=signals[0]))))
        for field in ('asset','timeframe','evidence','confirmed_at','rating'):
            for order in ('asc','desc'):
                for page in (0,1,2):
                    with self.subTest(field=field,order=order,page=page):self.parity(f'/api/findings?source=backtest&sort={field}&order={order}&page={page}')
        self.parity('/api/findings?source=backtest&archived=true')
        self.parity('/api/findings?source=backtest&page=999')

    def test_legacy_only_and_empty_latest(self):
        self.db.execute("INSERT INTO bot_runtime.legacy_findings VALUES('backtest','legacy',%s)",(json.dumps(dict(id='legacy',signal=dict(symbol='A',confirmed_at=3))),))
        self.assertEqual(len(self.parity('/api/findings?source=backtest')['findings']),1)
        self.report(1,[])
        self.assertEqual(self.parity('/api/findings?source=backtest')['findings'],[])
        self.db.execute('UPDATE bot_runtime.backtest_runs SET report=NULL')
        self.assertEqual(self.parity('/api/findings?source=backtest')['findings'],[])

    def test_overview_permissions_and_migration_idempotence(self):
        for _ in range(2):self.db.execute((Path(__file__).parent/'cloud/dashboard-read-performance.sql').read_text().replace('BEGIN;','').replace('COMMIT;',''),prepare=False)
        overview=self.call('/api/overview')
        self.assertEqual(overview['summary'],[dict(asset='TEST',status='ok')])
        self.assertEqual(overview['scan']['versions'],dict(ui='test'))
        self.assertNotIn('findings',overview)
        for op in ('/api/overview','/api/findings?source=backtest'):
            with self.assertRaises(psycopg.Error):self.call(op,OUTSIDER)
        with self.db.transaction():
            self.db.execute('SET LOCAL ROLE authenticated')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege),self.db.transaction():self.db.execute("SELECT bot_runtime.backtest_page('/api/findings?source=backtest')")
            self.db.execute('RESET ROLE')
        for op in ('/api/findings?source=backtest&sort=invalid','/api/findings?source=backtest&page=-1'):
            with self.assertRaises(psycopg.Error):self.call(op)
        self.db.execute('UPDATE bot_runtime.runtime_control SET enabled=false')
        # Public wrapper can fall back to historical API when disabled. Direct
        # runtime entry must still refuse the disabled installation.
        with self.assertRaises(psycopg.Error),self.db.transaction():
            self.db.execute("SELECT set_config('test.auth_uid',%s,true)",(OWNER,))
            self.db.execute("SELECT bot_runtime.api(%s,'/api/overview','{}')",(INST,))
