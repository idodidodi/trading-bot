"""Authorization parity checks against the explicit disposable PostgreSQL fixture."""
import os
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
        cls.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        with psycopg.connect(cls.dsn,autocommit=True) as db:
            cls.original_uid=db.execute("SELECT pg_get_functiondef('auth.uid()'::regprocedure)").fetchone()[0]
            # Model the production auth.uid contract only in the disposable DB.
            db.execute("CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS $$SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$")
            db.execute((Path(__file__).parent/'cloud/local-clerk-api.sql').read_text(),prepare=False)
            for user in cls.users:db.execute('INSERT INTO auth.users VALUES(%s) ON CONFLICT DO NOTHING',(user,))
            db.execute('INSERT INTO public.dashboard_installations VALUES(%s,%s) ON CONFLICT DO NOTHING',(cls.installation,cls.users[0]))
            db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer'),(%s,%s,'editor') ON CONFLICT DO NOTHING",(cls.installation,cls.users[1],cls.installation,cls.users[2]))

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.dsn,autocommit=True) as db:db.execute(cls.original_uid)

    def setUp(self):
        with psycopg.connect(self.dsn) as db:
            db.execute('DELETE FROM api_private.clerk_identities')
            db.execute('DELETE FROM public.telegram_alert_preferences WHERE installation=%s',(self.installation,))
            for index,user in enumerate(self.users):db.execute('INSERT INTO api_private.clerk_identities VALUES(%s,%s,%s,true)',(self.issuer,'user_'+str(index),user))
        info=psycopg.conninfo.conninfo_to_dict(self.dsn);info['user']='dashboard_gateway'
        self.gateway=Gateway(psycopg.conninfo.make_conninfo(**info),self.issuer,[self.origin],self.installation)
        key=jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(),as_dict=True)
        self.gateway.verifier.keys.fetch_data=Mock(return_value={'keys':[dict(key,kid='test',use='sig',alg='RS256')]})

    def token(self,user=0):
        now=int(time.time())
        return 'Bearer '+jwt.encode(dict(iss=self.issuer,sub='user_'+str(user),sid='sess_test',azp=self.origin,iat=now,nbf=now-1,exp=now+60),self.private,algorithm='RS256',headers={'kid':'test'})

    def call(self,user=0,operation='/api/telegram-alerts',inputs=None):
        return self.gateway.dispatch(self.token(user),self.origin,dict(installation=self.installation,operation=operation,input=inputs or {}))

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

    def test_replays_fail_explicitly_until_local_executor_exists(self):
        for path in ['/api/backtests','/api/csv-backtests']:
            with self.assertRaises(ValueError):self.call(0,path,{'action':'run'})


if __name__ == '__main__':unittest.main()
