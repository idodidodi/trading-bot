"""Authorization parity checks against the explicit disposable PostgreSQL fixture."""
import os
import secrets
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID
import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from clerk_auth import AuthenticationError, MembershipError
from clerk_dashboard_api import Gateway
from test_postgres_support import test_dsn


@unittest.skipUnless(os.environ.get('TRADING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL fixture')
class GatewayTests(unittest.TestCase):
    issuer = 'https://clerk.example.com'
    origin = 'https://dashboard.example.com'
    installation = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
    users = ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','cccccccc-cccc-4ccc-8ccc-cccccccccccc']

    @classmethod
    def setUpClass(cls):
        cls.dsn=test_dsn()
        cls.gateway_password=secrets.token_urlsafe(32)
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        with psycopg.connect(cls.dsn,autocommit=True) as db:
            cls.original_uid=db.execute("SELECT pg_get_functiondef('auth.uid()'::regprocedure)").fetchone()[0]
            cls.original_api=db.execute("SELECT pg_get_functiondef('bot_runtime.api(uuid,text,jsonb)'::regprocedure)").fetchone()[0]
            # Model the production auth.uid contract only in the disposable DB.
            db.execute("CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS $$SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$")
            db.execute((Path(__file__).parent/'cloud/local-clerk-api.sql').read_text(),prepare=False)
            db.execute((Path(__file__).parent/'cloud/local-replays.sql').read_text(),prepare=False)
            db.execute(psycopg.sql.SQL('ALTER ROLE dashboard_gateway PASSWORD {}').format(psycopg.sql.Literal(cls.gateway_password)))
            for user in cls.users:db.execute('INSERT INTO auth.users VALUES(%s) ON CONFLICT DO NOTHING',(user,))
            db.execute('INSERT INTO public.dashboard_installations VALUES(%s,%s) ON CONFLICT DO NOTHING',(cls.installation,cls.users[0]))
            db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer'),(%s,%s,'editor') ON CONFLICT DO NOTHING",(cls.installation,cls.users[1],cls.installation,cls.users[2]))

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.dsn,autocommit=True) as db:
            db.execute(cls.original_uid)
            db.execute(cls.original_api)

    def setUp(self):
        with psycopg.connect(self.dsn) as db:
            db.execute('DELETE FROM api_private.clerk_identities')
            db.execute('DELETE FROM public.telegram_alert_preferences WHERE installation=%s',(self.installation,))
            for index,user in enumerate(self.users):db.execute('INSERT INTO api_private.clerk_identities VALUES(%s,%s,%s,true)',(self.issuer,'user_'+str(index),user))
        info=psycopg.conninfo.conninfo_to_dict(self.dsn);info['user']='dashboard_gateway'
        info['password']=self.gateway_password
        self.gateway=Gateway(psycopg.conninfo.make_conninfo(**info),self.issuer,[self.origin],self.installation)
        key=jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(),as_dict=True)
        self.gateway.verifier.keys.fetch_data=Mock(return_value={'keys':[dict(key,kid='test',use='sig',alg='RS256')]})

    def token(self,user=0):
        now=int(time.time())
        return 'Bearer '+jwt.encode(dict(iss=self.issuer,sub='user_'+str(user),sid='sess_test',azp=self.origin,iat=now,nbf=now-1,exp=now+60),self.private,algorithm='RS256',headers={'kid':'test'})

    def call(self,user=0,operation='/api/telegram-alerts',inputs=None):
        return self.gateway.dispatch(self.token(user),self.origin,dict(installation=self.installation,operation=operation,input=inputs or {}))

    def test_signal_query_preserves_clerk_mapping_and_viewer_access(self):
        with psycopg.connect(self.dsn) as db:
            original=db.execute('SELECT installation,enabled FROM bot_runtime.runtime_control WHERE id=1').fetchone()
            db.execute('INSERT INTO bot_runtime.runtime_control(id,installation,enabled,effects_enabled) VALUES(1,%s,true,false) ON CONFLICT(id) DO UPDATE SET installation=excluded.installation,enabled=true',(self.installation,))
        try:
            for user in range(3):
                result=self.call(user,operation='/api/signal-query',inputs={'strategy':'rsi','timeframe':'4h'})
                self.assertIn('findings',result)
            with psycopg.connect(self.dsn) as db:db.execute("UPDATE api_private.clerk_identities SET enabled=false WHERE subject='user_1'")
            with self.assertRaises(MembershipError):self.call(1,operation='/api/signal-query')
        finally:
            with psycopg.connect(self.dsn) as db:
                if original:db.execute('UPDATE bot_runtime.runtime_control SET installation=%s,enabled=%s WHERE id=1',original)
                else:db.execute('DELETE FROM bot_runtime.runtime_control WHERE id=1')

    def test_owner_viewer_editor_keep_personal_preference_identity(self):
        for user in range(3):
            value=self.call(user);self.assertEqual(value['revision'],0)
            self.call(user,inputs={'action':'save','revision':0,'enabled':{'other-signals':False}})
        with psycopg.connect(self.dsn) as db:
            rows=db.execute('SELECT user_id,revision FROM public.telegram_alert_preferences WHERE installation=%s ORDER BY user_id',(self.installation,)).fetchall()
        self.assertEqual(rows,[(UUID(user),1) for user in self.users])

    def test_browser_cannot_write_another_users_preferences(self):
        with self.assertRaises(psycopg.Error):
            self.call(1,inputs={'action':'save','revision':0,'enabled':{},'user_id':self.users[0]})
        self.assertEqual(self.call(0)['revision'],0)

    def test_membership_revocation_is_authoritative_even_with_valid_mapping(self):
        with psycopg.connect(self.dsn) as db:db.execute('DELETE FROM public.dashboard_members WHERE installation=%s AND user_id=%s',(self.installation,self.users[1]))
        try:
            with self.assertRaises(psycopg.Error):self.call(1)
        finally:
            with psycopg.connect(self.dsn) as db:db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer')",(self.installation,self.users[1]))

    def test_viewer_cannot_edit_global_configuration(self):
        with self.assertRaises(psycopg.Error):self.call(1,'/api/assets',{'action':'save'})

    def test_mapping_disabled_unknown_subject_and_wrong_installation_denied(self):
        with psycopg.connect(self.dsn) as db:db.execute('UPDATE api_private.clerk_identities SET enabled=false WHERE subject=%s',('user_0',))
        with self.assertRaises(MembershipError):self.call(0)
        with self.assertRaises(MembershipError):self.call(9)
        with self.assertRaises(MembershipError):
            self.gateway.dispatch(self.token(1),self.origin,dict(installation=self.users[0],operation='/api/logs'))

    def test_scanner_or_admin_credentials_are_rejected(self):
        admin=Gateway(self.dsn,self.issuer,[self.origin],self.installation)
        with self.assertRaises(ValueError):admin.connect()
        with self.assertRaises(ValueError):Gateway('postgresql://remote.example/db',self.issuer,[self.origin],self.installation)

    def test_gateway_cannot_create_or_edit_identity_mappings(self):
        with self.gateway.connect() as db:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("UPDATE api_private.clerk_identities SET subject='user_attacker'")

    def test_viewer_cannot_start_csv_computation(self):
        self.gateway.replay_csv=Mock()
        with self.assertRaises(psycopg.Error):self.call(1,'/api/csv-backtests',{'csv':'untrusted'})
        self.gateway.replay_csv.assert_not_called()

    def test_csv_save_rechecks_mapping_after_computation(self):
        def revoke(inputs):
            with psycopg.connect(self.dsn) as db:
                db.execute("UPDATE api_private.clerk_identities SET enabled=false WHERE subject='user_0'")
            return {'id':'computed','signals':[]}
        self.gateway.replay_csv=revoke
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.call(0,'/api/csv-backtests',{'csv':'fixture'})

    def test_stored_replay_is_queued_for_existing_local_worker(self):
        from psycopg.types.json import Jsonb
        with psycopg.connect(self.dsn) as db:
            with db.transaction():
                db.execute('DELETE FROM bot_runtime.backtest_jobs')
                db.execute('INSERT INTO bot_runtime.runtime_control(id,installation,enabled) VALUES(1,%s,true) ON CONFLICT(id) DO UPDATE SET installation=excluded.installation,enabled=true',(self.installation,))
                db.execute("INSERT INTO bot_runtime.backtest_candle_cache VALUES('test-local','daily',0,1,1,1,1,1) ON CONFLICT DO NOTHING")
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)",(self.users[0],))
                db.execute('SET LOCAL ROLE authenticated')
                result=db.execute("SELECT public.dashboard_api(%s,'/api/backtests',%s)",(self.installation,Jsonb({'assets':['test-local'],'timeframes':['daily'],'strategy':'confirmed'}))).fetchone()[0]
                self.assertEqual(result['status'],'pending')
                self.assertEqual(result['executor'],'local')
                raise psycopg.Rollback()


if __name__ == '__main__':unittest.main()
