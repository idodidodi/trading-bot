"""Dedicated group sound tests; disposable DB and mocked Telegram only."""
import json
from pathlib import Path
from unittest.mock import patch, Mock
import psycopg
from test_telegram_personal import PersonalTelegramTests, OWNER, OTHER, STRANGER, INST
from test_postgres_support import PostgresTestCase, test_dsn
from telegram_group_sound import silent_for_event

class GroupSoundTests(PostgresTestCase):
    setUp=PersonalTelegramTests.setUp
    def api(self,user=OWNER,body=None):
        with psycopg.connect(test_dsn()) as db:
            db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_api(%s,%s,%s::jsonb)',(INST,'/api/telegram-group-sound',json.dumps(body or {}))).fetchone()[0]
    def test_persistence_timeframes_isolation_and_retry(self):
        first=self.api();self.assertEqual(len(first['groups']),5)
        body=dict(action='save',revision=first['revision'],silent={'lower':{'5m':True,'4h':False},'higher':{'weekly':True}})
        saved=self.api(body=body);self.assertEqual(saved['silent'],body['silent'])
        for kind,lower,higher,want in [('lower','5m','weekly',True),('lower','4h','weekly',False),('ready','5m','weekly',False),('higher',None,'weekly',True),('higher',None,'daily',False)]:
            self.assertEqual(silent_for_event(self.store,dict(kind=kind,lower=lower,higher=higher)),want)
        with self.assertRaises(psycopg.Error):self.api(body=body)
        body.update(revision=saved['revision'],silent={});self.api(body=body)
        self.assertFalse(silent_for_event(self.store,dict(kind='lower',lower='5m')))
    def test_authorization_validation_and_migration_rerun(self):
        self.assertFalse(self.api(OTHER)['editable'])
        body=dict(action='save',revision=self.api()['revision'],silent={})
        for user in (OTHER,STRANGER):
            with self.assertRaises(psycopg.Error):self.api(user,body)
        for value in ({'wrong':{}},{'lower':{'2h':True}},{'lower':{'5m':'yes'}},{'lower':None},None):
            with self.assertRaises(psycopg.Error):self.api(body={**body,'silent':value})
        body['silent']={'lower':{'5m':True}};saved=self.api(body=body)
        with psycopg.connect(test_dsn()) as db:
            db.execute(Path('cloud/telegram-group-sound.sql').read_text(),prepare=False)
        self.assertEqual(self.api()['silent'],saved['silent'])

    def test_watch_worker_passes_preference_to_sender(self):
        import stock_rsi_watch as watch
        import stock_rsi_runtime as runtime
        from test_stock_rsi_watch import cycle, signal
        c,now=cycle();watch.admit(c,'FAKE',signal(c['period_start']),now)
        frame=c['events'][0]['higher']
        self.api(body=dict(action='save',revision=0,silent={'higher':{frame:True}}))
        runtime.save(self.store,c)
        with patch('local_settings.delivery_dry_run',return_value=False), patch('platform_app.send_telegram',return_value=42) as send:
            runtime.flush(self.store,c,'test-token',now)
        self.assertEqual(send.call_count,1)
        self.assertTrue(send.call_args.kwargs['silent'])
        self.assertTrue(c['events'][0]['delivered'])

    def test_near_sound_is_independent_of_stock_groups(self):
        from telegram_group_sound import silent_for_group
        self.api(body=dict(action='save',revision=0,silent={'near':{'5m':True,'4h':False}}))
        self.assertTrue(silent_for_group(self.store,'near','5m'))
        self.assertFalse(silent_for_group(self.store,'near','4h'))
        self.assertFalse(silent_for_group(self.store,'lower','5m'))

    @patch('telegram_delivery.deliver',return_value='sent')
    def test_near_delivery_uses_each_signal_timeframe(self, deliver):
        from telegram_asset_subscriptions import deliver_subscriptions
        from test_telegram_asset_subscriptions import payload
        with self.store.connect() as db:
            db.execute("INSERT INTO telegram_asset_subscriptions VALUES('-999','NEARUSD',%s,true,to_timestamp(1000))",(['5m','4h'],))
        self.api(body=dict(action='save',revision=0,silent={'near':{'5m':True,'4h':False}}))
        for frame,want in [('5m',True),('4h',False)]:
            deliver_subscriptions(self.store,'token','text','divergence:near',Mock(),payload=payload(timeframe=frame))
            self.assertEqual(deliver.call_args.kwargs['silent'],want)
        deliver_subscriptions(self.store,'token','text','divergence:near',Mock(),silent=True,payload=payload(timeframe='4h'))
        self.assertTrue(deliver.call_args.kwargs['silent'])
