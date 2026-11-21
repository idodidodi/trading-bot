import json
import psycopg
from test_postgres_support import PostgresTestCase,TestStore,test_dsn
from test_telegram_personal import INST,OWNER,OTHER,STRANGER

class NearPreferencesTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        with self.store.connect() as db:
            db.execute("INSERT INTO telegram_asset_subscriptions VALUES('-1004484031372','NEARUSD',ARRAY['5m','15m'],true,to_timestamp(1000))")
            db.execute('INSERT INTO auth.users VALUES(%s),(%s) ON CONFLICT DO NOTHING',(OTHER,STRANGER))
            db.execute("INSERT INTO public.dashboard_members VALUES(%s,%s,'viewer',now()) ON CONFLICT DO NOTHING",(INST,OTHER))
    def api(self,body=None,user=OWNER):
        with psycopg.connect(test_dsn()) as db:
            db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_api(%s,%s,%s::jsonb)',(INST,'/api/near-alerts',json.dumps(body or {}))).fetchone()[0]
    def test_save_empty_stale_and_restore(self):
        initial=self.api();self.assertEqual(initial['subscriptions']['rsi'],['5m','15m'])
        body=dict(action='save',revision=0,subscriptions={'rsi':[],'trend-pin-bars':['5m']})
        saved=self.api(body);self.assertEqual(saved['revision'],1)
        with self.store.connect() as db:
            self.assertFalse(db.execute('SELECT enabled FROM telegram_asset_subscriptions').fetchone()[0])
        with self.assertRaises(psycopg.Error):self.api(body)
        self.api(body|{'revision':1,'subscriptions':{'rsi':['15m'],'trend-pin-bars':[]}})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT timeframes,enabled FROM telegram_asset_subscriptions').fetchone(),(['15m'],True))
    def test_owner_scope_and_validation(self):
        for user in (OTHER,STRANGER):
            with self.assertRaises(psycopg.Error):self.api(user=user)
        for subscriptions in ({'rsi':['bad'],'trend-pin-bars':[]},{'rsi':['5m','5m'],'trend-pin-bars':[]},{'rsi':[]}, {'rsi':[5],'trend-pin-bars':[]}):
            with self.assertRaises(psycopg.Error):self.api(dict(action='save',revision=0,subscriptions=subscriptions))
    def test_group_migration_and_enrollment(self):
        body=dict(action='save',revision=0,subscriptions={'rsi':['5m','15m'],'trend-pin-bars':[]})
        self.api(body)
        with self.store.connect() as db:
            starts=db.execute('SELECT enrolled FROM near_group_preferences').fetchone()[0]
            self.assertEqual(starts['rsi:5m'],1000)
            db.execute("UPDATE telegram_asset_subscriptions SET chat='-1009999999999'")
            self.assertEqual(db.execute('SELECT chat FROM near_group_preferences').fetchone()[0],'-1009999999999')
        self.assertEqual(self.api()['revision'],1)
