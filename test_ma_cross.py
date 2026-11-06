"""Crossover qualification and durable delivery, using disposable Postgres only."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import ma_cross
from scanner import Candle, check_candles, prepare_store, scan_once
from platform_app import load_rules, TelegramDestinationError
from test_postgres_support import PostgresTestCase, TestStore
from telegram_routing import remember, select, deliver_routed


def bars(prices):
    start = 1704067200000
    return [Candle(start+i*86400000,start+(i+1)*86400000,p,p,p,p) for i,p in enumerate(prices)]


class DetectorTests(unittest.TestCase):
    def detect(self, prices):
        return ma_cross.detect(bars(prices),ma_cross.load_rules(),'test','TEST','daily')

    def test_both_directions_and_equality(self):
        for price, direction in ((101,'bullish'),(99,'bearish')):
            signals=self.detect([100]*200+[price])
            self.assertEqual([s['direction'] for s in signals],[direction])
            self.assertEqual(signals[0]['previous_fast_sma'],100)
            self.assertAlmostEqual(signals[0]['fast_sma'],(4900+price)/50)
            self.assertAlmostEqual(signals[0]['slow_sma'],(19900+price)/200)
            self.assertIn('Golden cross' if price>100 else 'Death cross',ma_cross.message(signals[0]))
        self.assertEqual(self.detect([100]*201),[])
        self.assertEqual(ma_cross.detect(bars([100]*200+[101]),ma_cross.load_rules(),'test','TEST','4h'),[])
        self.assertEqual(self.detect([100]*199+[101]),[])

    def test_cross_from_strict_side_and_rearming(self):
        signals=self.detect([100]*200+[110]*60+[90]*100+[110]*100)
        self.assertEqual([s['direction'] for s in signals],['bullish','bearish','bullish'])
        first=self.detect([100]*200+[110])[0]
        self.assertEqual(signals[0],first)

    def test_invalid_and_open_candles(self):
        self.assertEqual(self.detect([100]*199+[float('nan'),101]),[])
        candles=bars([100]*200+[101])
        closed=check_candles(candles,candles[-1].end-1)
        self.assertEqual(ma_cross.detect(closed,ma_cross.load_rules(),'test','TEST','daily'),[])


class PersistenceTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore();prepare_store(self.store)
        self.asset=dict(id='TEST',provider='csv',enabled=True,timeframes=['daily'],scan_strategies=['ma-cross'])
        self.rules=ma_cross.load_rules()

    def test_baseline_restart_catchup_and_history(self):
        candles=bars([100]*201+[101]);now=candles[-1].end
        self.assertEqual(ma_cross.scan(self.store,self.asset,'daily',candles[:200],self.rules,now)['status'],'insufficient history')
        self.assertEqual(ma_cross.scan(self.store,self.asset,'daily',candles[:-1],self.rules,now)['status'],'baseline set')
        self.assertEqual(ma_cross.scan(self.store,self.asset,'daily',candles,self.rules,now)['new_signals'],1)
        self.assertEqual(ma_cross.scan(self.store,self.asset,'daily',candles,self.rules,now)['new_signals'],0)
        other=self.asset|dict(id='OTHER')
        ma_cross.scan(self.store,other,'daily',candles,self.rules,now)
        catch=self.asset|dict(id='CATCH')
        ma_cross.scan(self.store,catch,'daily',candles[:-1],self.rules,now)
        ma_cross.scan(self.store,catch,'daily',candles,self.rules,now,catchup=True)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],1)
        from dashboard_api import read
        self.assertEqual(len(read(self.store,'/api/ma-cross')['signals']),2)

    def test_scanner_all_markets_and_closed_candles(self):
        candles=bars([100]*301+[101])
        assets=[self.asset|dict(id=m,market=m) for m in ('crypto','forex','stock','commodity')]
        config=dict(timeframes=['4h'],assets=[a|dict(timeframes=['4h']) for a in assets])
        with patch('scanner.read_csv',return_value=candles):
            first=scan_once(self.store,config,load_rules(),candles[-2].end)
            self.assertTrue(all(r['ma_cross']['status']=='baseline set' for r in first))
            second=scan_once(self.store,config,load_rules(),candles[-1].end)
            self.assertTrue(all(r['ma_cross']['new_signals']==1 for r in second))

    def test_migration_once_and_optout(self):
        from managed_assets import get
        config=dict(assets=[self.asset|dict(enabled=False,scan_strategies=['pin-bars'])],rsi_pb_defaults_applied=True)
        with self.store.connect() as db:
            db.execute('INSERT INTO managed_config VALUES(1,1,0,%s)',(json.dumps(config),))
        result=get(self.store)
        self.assertFalse(result['config']['assets'][0]['enabled'])
        self.assertEqual(result['config']['assets'][0]['scan_strategies'],['pin-bars','ma-cross'])
        result['config']['assets'][0]['scan_strategies']=['pin-bars']
        with self.store.connect() as db:db.execute('UPDATE managed_config SET payload=%s',(json.dumps(result['config']),))
        self.assertEqual(get(self.store)['config']['assets'][0]['scan_strategies'],['pin-bars'])
        self.assertEqual(get(self.store)['revision'],result['revision'])

    def test_dedicated_routing_no_general_fallback(self):
        from psycopg.types.json import Jsonb
        with self.store.connect() as db:
            db.execute("INSERT INTO public.telegram_routes VALUES('00000000-0000-0000-0000-000000000002',1,%s)",(Jsonb({'general':'-111','other-signals':'-222'}),))
            for market in ('crypto','forex','stock','unknown'):remember(db,market,market,'ma-cross')
        for market in ('crypto','forex','stock','unknown'):
            self.assertEqual(select(self.store,market,'-333'),('-222',''))
        with patch('telegram_delivery.deliver',side_effect=TelegramDestinationError('unavailable')) as send:
            with self.assertRaises(TelegramDestinationError):deliver_routed(self.store,'token','-333','text','crypto',lambda *a:None)
            self.assertEqual(send.call_count,1)
        with self.store.connect() as db:
            db.execute("UPDATE public.telegram_routes SET routes='{}'")
            remember(db,'missing','crypto','ma-cross')
        with self.assertRaisesRegex(RuntimeError,'Other Signals'):select(self.store,'missing','-333')
        from telegram_personal import route_for
        self.assertEqual(route_for('crypto','ma-cross'),'other-signals')

    def test_route_provisioning_preview_and_scoped_cleanup(self):
        import importlib.util
        import io
        import os
        import tempfile
        from psycopg.rows import dict_row
        spec=importlib.util.spec_from_file_location('configure_other_signals',Path('scripts/configure-other-signals.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        inst='00000000-0000-0000-0000-000000000002'
        with self.store.connect() as db:
            db.execute("INSERT INTO public.telegram_routes VALUES(%s,2,%s::jsonb)",(inst,json.dumps({'general':'-111','forex.rsi':'-222','crypto.rsi':'-333'})))
            db.execute("INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES(%s,%s,1,%s::jsonb)",
                       (inst,'00000000-0000-0000-0000-000000000001',json.dumps({'forex.rsi':'-222','crypto.rsi':'-444'})))
        queries=[]
        def local_query(request,timeout):
            sql=json.loads(request.data)['query'];queries.append(sql)
            with self.store.connect() as db:
                with db.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(sql,prepare=False)
                    result=cursor.fetchall() if cursor.description else []
            return io.StringIO(json.dumps(result))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'cloud').mkdir()
            (root/'cloud/browser-config.json').write_text(json.dumps(dict(supabaseUrl='https://test.example',installationId=inst)))
            mapping=root/'groups.json';mapping.write_text(json.dumps({'routes':{'other-signals':'-222'}}))
            with patch.object(module,'ROOT',root), patch('platform_app.load_env'), patch.dict(os.environ,{'SUPABASE_ACCESS_TOKEN':'test-token'}), patch.object(module.urllib.request,'urlopen',side_effect=local_query), patch('sys.stdout',new_callable=io.StringIO):
                with patch('sys.argv',['configure','--group-map',str(mapping)]):module.main()
                self.assertEqual(len(queries),1)
                self.assertTrue(queries[0].lstrip().startswith('SELECT'))
                with patch('sys.argv',['configure','--group-map',str(mapping),'--apply']):module.main()
        with self.store.connect() as db:
            routes=db.execute('SELECT routes FROM public.telegram_routes WHERE installation=%s',(inst,)).fetchone()[0]
            self.assertEqual(routes,{'general':'-111','forex.rsi':'','crypto.rsi':'-333','other-signals':'-222'})
            personal=db.execute('SELECT routes FROM public.telegram_recipients WHERE installation=%s',(inst,)).fetchone()[0]
            self.assertEqual(personal,{'crypto.rsi':'-444'})

    def test_sql_migration_rerun_and_authenticated_ui(self):
        from psycopg.types.json import Jsonb
        with self.store.connect() as db:
            config=dict(assets=[self.asset|dict(enabled=False,scan_strategies=['pin-bars'])])
            db.execute('INSERT INTO managed_config VALUES(1,1,0,%s)',(json.dumps(config),))
            db.execute(Path('cloud/ma-cross.sql').read_text(),prepare=False)
            row=db.execute('SELECT revision,payload FROM managed_config').fetchone()
            self.assertEqual(row[0],2)
            config=json.loads(row[1])
            self.assertEqual(config['assets'][0]['scan_strategies'],['pin-bars','ma-cross'])
            self.assertFalse(config['assets'][0]['enabled'])
            config['assets'][0]['scan_strategies']=['pin-bars']
            db.execute('UPDATE managed_config SET payload=%s',(json.dumps(config),))
            db.execute(Path('cloud/ma-cross.sql').read_text(),prepare=False)
            row=db.execute('SELECT revision,payload FROM managed_config').fetchone()
            self.assertEqual(row[0],2)
            self.assertEqual(json.loads(row[1])['assets'][0]['scan_strategies'],['pin-bars'])
            db.execute("SET LOCAL ROLE authenticated")
            db.execute("SELECT set_config('test.auth_uid','00000000-0000-0000-0000-000000000001',true)")
            result=db.execute("SELECT public.dashboard_api('00000000-0000-0000-0000-000000000002','/api/ma-cross')").fetchone()[0]
            self.assertEqual(result['strategy'],'ma-cross')
            routes=db.execute("SELECT public.dashboard_api('00000000-0000-0000-0000-000000000002','/api/telegram-routing',%s)",(Jsonb(dict(action='save',revision=0,routes={'other-signals':'-222'})),)).fetchone()[0]
            self.assertEqual(routes['routes']['other-signals'],'-222')


if __name__=='__main__':unittest.main()
