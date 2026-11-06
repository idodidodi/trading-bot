import json
import os
import time
import unittest
from unittest.mock import patch
from test_postgres_support import PostgresTestCase,TestStore
from managed_assets import validate_asset

OWNER='00000000-0000-0000-0000-000000000001'
INST='00000000-0000-0000-0000-000000000002'
VIEWER='00000000-0000-0000-0000-000000000003'
OUTSIDER='00000000-0000-0000-0000-000000000004'

class ColmexValidation(unittest.TestCase):
    def test_exact_hash_symbol_and_timeframe_validation(self):
        asset=dict(id='SPX500',provider='colmex',symbol='SPX500#',exchange='Colmex-MT4',
                   enabled=True,feed_confirmed=True,timeframes=['4h'])
        validate_asset(asset)
        for bad in (dict(timeframes=['weekly']),dict(symbol='../file'),dict(exchange='Yahoo'),dict(tradingview_symbol='TVC:SPX'),dict(timeframe_providers={'4h':'yahoo'})):
            with self.assertRaises(ValueError):validate_asset(asset|bad)

class ColmexRequests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        with self.store.connect() as db:
            for user in (VIEWER,OUTSIDER):db.execute('INSERT INTO auth.users VALUES(%s) ON CONFLICT DO NOTHING',(user,))
            db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer') ON CONFLICT DO NOTHING",(INST,VIEWER))
            db.execute('INSERT INTO colmex_status VALUES(1,%s)',(json.dumps(dict(status='collecting',updated_at=time.time(),catalogue=[dict(symbol=s,export_supported=True,forex=s!='SPX500#') for s in ('EURUSD','GBPUSD','SPX500#')],reserved_symbols=['SPX500#'])),))
    def api(self,user,body=None,inst=INST):
        with self.store.connect() as db:
            db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute("SELECT public.dashboard_api(%s,'/api/colmex',%s::jsonb)",(inst,json.dumps(body or {}))).fetchone()[0]
    def test_viewer_requests_are_private_and_union_is_deduplicated(self):
        self.api(OWNER,dict(action='set',revision=0,symbols=['EURUSD']))
        out=self.api(VIEWER,dict(action='set',revision=0,symbols=['EURUSD','GBPUSD']))
        self.assertEqual(out['symbols'],['EURUSD','GBPUSD'])
        self.assertEqual(self.api(OWNER)['symbols'],['EURUSD'])
        self.api(VIEWER,dict(action='set',revision=1,symbols=[]))
        self.assertEqual(self.api(OWNER)['symbols'],['EURUSD'])
    def test_no_impersonation_stale_revision_or_unsupported_symbols(self):
        for body in (dict(action='set',revision=0,symbols=['EURUSD'],user_id=OWNER),dict(action='set',revision=99,symbols=['EURUSD']),dict(action='set',revision=0,symbols=['FAKEFX']),dict(action='set',revision=0,symbols=['SPX500#'])):
            with self.assertRaises(Exception):self.api(VIEWER,body)
        with self.assertRaises(Exception):self.api(OUTSIDER)
        with self.assertRaises(Exception):self.api(OWNER,inst='00000000-0000-0000-0000-000000000099')
    def test_removal_allowed_when_feed_stale_new_request_rejected(self):
        self.api(VIEWER,dict(action='set',revision=0,symbols=['EURUSD']))
        with self.store.connect() as db:db.execute("UPDATE colmex_status SET payload=jsonb_set(payload,'{updated_at}','0')")
        with self.assertRaises(Exception):self.api(VIEWER,dict(action='set',revision=1,symbols=['GBPUSD']))
        self.assertEqual(self.api(VIEWER,dict(action='set',revision=1,symbols=[]))['symbols'],[])
    def test_sql_and_python_accept_colmex_primary(self):
        asset=dict(id='SPX500',provider='colmex',symbol='SPX500#',exchange='Colmex-MT4',enabled=True,feed_confirmed=True,timeframes=['4h'])
        with self.store.connect() as db:db.execute('SELECT public.dashboard_validate_provider_asset(%s::jsonb)',(json.dumps(asset),))
        for bad in (dict(timeframes=['weekly']),dict(timeframe_providers={'4h':'yahoo'})):
            with self.assertRaises(Exception):
                with self.store.connect() as db:db.execute('SELECT public.dashboard_validate_provider_asset(%s::jsonb)',(json.dumps(asset|bad),))
    def test_revoked_membership_stops_collection_and_worker_cannot_write_requests(self):
        self.api(VIEWER,dict(action='set',revision=0,symbols=['EURUSD']))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT symbol FROM colmex_requested_symbols()').fetchall(),[('EURUSD',)])
            db.execute('DELETE FROM public.dashboard_members WHERE installation=%s AND user_id=%s',(INST,VIEWER))
            self.assertEqual(db.execute('SELECT symbol FROM colmex_requested_symbols()').fetchall(),[])
        with self.assertRaises(Exception):
            with self.store.connect() as db:
                db.execute('SET LOCAL ROLE trading_worker')
                db.execute("DELETE FROM bot_runtime.colmex_requests")

    def test_shared_capacity_and_no_direct_browser_table_access(self):
        with self.store.connect() as db:
            db.execute("UPDATE colmex_status SET payload=jsonb_set(payload,'{reserved_symbols}',%s::jsonb)",(json.dumps(['X'+str(i) for i in range(50)]),))
        with self.assertRaises(Exception):self.api(VIEWER,dict(action='set',revision=0,symbols=['EURUSD']))
        with self.assertRaises(Exception):
            with self.store.connect() as db:
                db.execute('SET LOCAL ROLE authenticated')
                db.execute('SELECT * FROM bot_runtime.colmex_requests')

    def test_runtime_reconciles_union_without_touching_portfolio(self):
        from colmex_runtime import refresh
        from unittest.mock import Mock
        self.api(VIEWER,dict(action='set',revision=0,symbols=['EURUSD']))
        reader=Mock();reader.status.return_value=({'server':'s'}, {'EURUSD'})
        reader.read_json.side_effect=[{'symbols':[{'symbol':'EURUSD','export_supported':True}]},{'subscribers':{'portfolio':['SPX500#'],'scanner':['EURUSD']}}]
        reader.set_subscription.return_value={'revision':'r'}
        with patch.dict(os.environ,{'COLMEX_FILES_DIR':'/test'}),patch('colmex_runtime.bridge',return_value=reader):
            result=refresh(self.store,{'assets':[]})
        reader.set_subscription.assert_called_once_with('/test','scanner',['EURUSD'])
        self.assertEqual(result['assets'][0]['symbol'],'EURUSD')
        self.assertEqual(result['assets'][0]['timeframes'],['4h'])


    def test_scanner_colmex_baseline_and_hash_symbol_signal(self):
        from scanner import scan_once,prepare_store
        from platform_app import load_rules
        from test_scanner import fixture
        from dataclasses import replace
        prepare_store(self.store)
        bars=fixture()
        asset=dict(id='SPX500',provider='colmex',symbol='SPX500#',exchange='Colmex-MT4',market='index',scan_strategies=['rsi'])
        config=dict(assets=[asset],timeframes=['4h'])
        with patch('colmex_feed.fetch_colmex',return_value=bars[:256]),patch.dict(os.environ,{'COLMEX_FILES_DIR':''}):
            rows=scan_once(self.store,config,load_rules(),bars[255].end)
        self.assertEqual(rows[0]['status'],'baseline set',rows)
        self.assertEqual(self.store.rows(),[])
        with patch('colmex_feed.fetch_colmex',return_value=bars[:257]),patch.dict(os.environ,{'COLMEX_FILES_DIR':''}):
            rows=scan_once(self.store,config,load_rules(),bars[256].end)
        self.assertEqual(rows[0]['new_signals'],1,rows)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT DISTINCT feed FROM candle_cache').fetchone()[0],'colmex:Colmex-MT4:SPX500#')


    def test_owner_disabled_pair_is_not_reenabled_by_subscriber(self):
        from colmex_runtime import refresh
        from unittest.mock import Mock
        self.api(VIEWER,dict(action='set',revision=0,symbols=['EURUSD']))
        reader=Mock();reader.status.return_value=({'server':'s'}, {'EURUSD'})
        reader.read_json.side_effect=[{'symbols':[]},{'subscribers':{'portfolio':['SPX500#'],'scanner':[]}}]
        reader.set_subscription.return_value={'revision':'r'}
        config={'assets':[dict(id='EURUSD',provider='colmex',symbol='EURUSD',enabled=False)]}
        with patch.dict(os.environ,{'COLMEX_FILES_DIR':'/test'}),patch('colmex_runtime.bridge',return_value=reader):
            result=refresh(self.store,config)
        reader.set_subscription.assert_called_once_with('/test','scanner',[])
        self.assertEqual(result,config)

    def test_stock_with_six_letter_name_is_not_a_forex_pair(self):
        with self.store.connect() as db:
            db.execute("UPDATE colmex_status SET payload=jsonb_set(payload,'{catalogue}',%s::jsonb)",(json.dumps([dict(symbol='GOOGLE#',export_supported=True,forex=False)]),))
        with self.assertRaises(Exception):self.api(VIEWER,dict(action='set',revision=0,symbols=['GOOGLE#']))
