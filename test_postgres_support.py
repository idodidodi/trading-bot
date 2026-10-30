"""Explicit disposable-Postgres fixtures; imported by tests only."""
import os
import unittest
from unittest.mock import Mock
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from platform_app import Store as RuntimeStore


_dsn=None

def test_dsn():
    global _dsn
    if _dsn:return _dsn
    dsn=os.environ.get('TRADING_TEST_DATABASE_URL','')
    options=conninfo_to_dict(dsn)
    if options.get('dbname')!='trading_bot_test' or options.get('host') not in ('127.0.0.1','localhost','/private/tmp','/tmp'):
        raise RuntimeError('Tests require an explicit loopback TRADING_TEST_DATABASE_URL with dbname=trading_bot_test')
    _dsn=dsn
    return dsn


def reset():
    with psycopg.connect(test_dsn()) as db:
        tables=[r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='bot_runtime' AND tablename<>'schema_version'")]
        db.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(sql.SQL(',').join(sql.Identifier('bot_runtime',t) for t in tables)))
    with psycopg.connect(test_dsn()) as db:
        db.execute("DELETE FROM public.telegram_routes WHERE installation='00000000-0000-0000-0000-000000000002'")
        for table in ('telegram_recipients','telegram_alert_preferences'):
            if db.execute('SELECT to_regclass(%s)', ('public.'+table,)).fetchone()[0]:
                db.execute(sql.SQL('DELETE FROM public.{} WHERE installation=%s').format(sql.Identifier(table)),
                           ('00000000-0000-0000-0000-000000000002',))
    os.environ['TRADING_DATABASE_URL']=test_dsn()
    os.environ['TRADING_DB_TEST']='1'


class PostgresTestCase(unittest.TestCase):
    def _callSetUp(self):
        # unittest checks skip flags on the class/method, not the instance.
        # This hook also runs when subclasses provide their own setUp method.
        if not os.environ.get('TRADING_TEST_DATABASE_URL'):
            self.skipTest('Use the explicit disposable Postgres test runner')
        reset()
        super()._callSetUp()


class TestStore(RuntimeStore):
    def __init__(self,path=None):
        os.environ['TRADING_DATABASE_URL']=test_dsn()
        os.environ['TRADING_DB_TEST']='1'
        with psycopg.connect(test_dsn()) as db:
            db.execute("INSERT INTO auth.users VALUES('00000000-0000-0000-0000-000000000001') ON CONFLICT DO NOTHING")
            db.execute("INSERT INTO public.dashboard_installations VALUES('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001') ON CONFLICT DO NOTHING")
            db.execute("INSERT INTO bot_runtime.runtime_control(id,installation,enabled,effects_enabled) VALUES(1,'00000000-0000-0000-0000-000000000002',true,true) ON CONFLICT DO NOTHING")
        super().__init__()
        self.path=path
        # Regression workers use mocked Telegram/brokers. Ownership itself is
        # tested independently with REAL sessions in test_postgres_runtime.
        self.guard=Mock()
