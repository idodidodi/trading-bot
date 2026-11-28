"""Server filtering, pagination and access checks on a disposable database only."""
import json
import os
import unittest
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb
from test_postgres_support import test_dsn

INST='00000000-0000-0000-0000-000000000002'
OWNER='00000000-0000-0000-0000-000000000001'
VIEWER='00000000-0000-0000-0000-000000000003'
OUTSIDER='00000000-0000-0000-0000-000000000004'

@unittest.skipUnless(os.environ.get('TRADING_TEST_DATABASE_URL'),'Requires disposable PostgreSQL')
class SignalQueryTests(unittest.TestCase):
    def setUp(self):
        self.db=psycopg.connect(test_dsn())
        self.addCleanup(self.db.close)
        self.db.execute('TRUNCATE bot_runtime.signals,bot_runtime.finding_keys,bot_runtime.legacy_findings,bot_runtime.finding_state,bot_runtime.finding_feedback,bot_runtime.finding_followup,bot_runtime.pin_bar_signals,bot_runtime.bb_pin_bar_signals,bot_runtime.rsi_pb_signals,bot_runtime.ma_cross_signals,bot_runtime.runtime_control')
        for uid in (OWNER,VIEWER,OUTSIDER):self.db.execute('INSERT INTO auth.users VALUES(%s) ON CONFLICT DO NOTHING',(uid,))
        self.db.execute('INSERT INTO public.dashboard_installations VALUES(%s,%s) ON CONFLICT DO NOTHING',(INST,OWNER))
        self.db.execute("INSERT INTO public.dashboard_members(installation,user_id,role) VALUES(%s,%s,'viewer') ON CONFLICT DO NOTHING",(INST,VIEWER))
        self.db.execute('INSERT INTO bot_runtime.runtime_control(id,installation,enabled,effects_enabled) VALUES(1,%s,true,false)',(INST,))
        self.db.commit()

    def signal(self,key,**extra):
        signal=dict(symbol='A&B/É#',timeframe='4h',direction='bullish',confirmed_at=1795773659000,tag=key,**extra)
        return signal

    def insert(self,key,signal,table='signals'):
        payload=json.dumps(signal)
        if table=='signals':
            self.db.execute('INSERT INTO bot_runtime.signals(id,payload,received) VALUES(%s,%s,0)',(key,payload))
            self.db.execute("INSERT INTO bot_runtime.finding_keys(source,evidence_hash,finding_id) VALUES('live',bot_runtime.signal_payload_hash(%s),%s)",(payload,key))
        else:
            self.db.execute(psycopg.sql.SQL('INSERT INTO bot_runtime.{} VALUES(%s,%s,%s)').format(psycopg.sql.Identifier(table)),(key,signal['confirmed_at'],payload))
        self.db.commit()

    def call(self,options=None,user=OWNER,installation=INST):
        with psycopg.connect(test_dsn()) as db:
            db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_signal_query(%s,%s)',(installation,Jsonb(options or {}))).fetchone()[0]

    def test_combined_filters_and_catalog_for_all_strategies(self):
        for strategy,table in [('rsi','signals'),('pin-bars','pin_bar_signals'),('bb-pin-bars','bb_pin_bar_signals'),('rsi-pb','rsi_pb_signals'),('ma-cross','ma_cross_signals')]:
            self.insert(strategy,self.signal(strategy),table)
            other=self.signal(strategy+'other');other.update(symbol='OTHER',timeframe='1h',direction='bearish')
            self.insert(strategy+'other',other,table)
            field='findings' if strategy=='rsi' else 'signals'
            filters=dict(strategy=strategy,asset='A&B/É#',query='a&b',timeframe='4h',direction='bullish',**{'from':'2026-11-27T12:00','to':'2026-11-27T12:00'})
            self.assertEqual(len(self.call(filters,user=VIEWER)[field]),1)
            self.assertEqual(self.call(dict(filters,direction='bearish'))[field],[])
            self.assertEqual(self.call(dict(filters,asset="' OR true --"))[field],[])
            self.assertEqual(self.call(dict(filters,catalog=True))['assets'],['A&B/É#','OTHER'])

    def test_cursor_has_no_duplicates_at_equal_timestamps(self):
        for n in range(105):self.insert(f'{n:03}',self.signal(str(n)))
        cursor=None;ids=[];pages=[]
        while True:
            page=self.call(dict(cursor=cursor));pages.append(len(page['findings']))
            ids.extend(r['id'] for r in page['findings'])
            if not page['has_more']:break
            cursor=page['next_cursor']
        self.assertEqual(pages,[50,50,5]);self.assertEqual(ids,[f'{n:03}' for n in range(105)])

    def test_pagination_merges_current_and_legacy_before_limiting(self):
        for n in range(30):self.insert(f'c{n:03}',self.signal(str(n)))
        for n in range(30):
            self.db.execute("INSERT INTO bot_runtime.legacy_findings VALUES('live',%s,%s)",(f'l{n:03}',json.dumps(dict(id=f'l{n:03}',signal=self.signal(str(n))))))
        self.db.commit()
        first=self.call();second=self.call(dict(cursor=first['next_cursor']))
        ids=[r['id'] for r in first['findings']+second['findings']]
        self.assertEqual(len(first['findings']),50);self.assertEqual(len(second['findings']),10)
        self.assertEqual(len(set(ids)),60);self.assertEqual(ids,sorted(ids))

    def test_legacy_deduplication_archive_delete_and_feedback(self):
        signal=self.signal('current');self.insert('current',signal)
        for key in ['current','legacy','archived','deleted']:
            self.db.execute("INSERT INTO bot_runtime.legacy_findings VALUES('live',%s,%s)",(key,json.dumps(dict(id=key,signal=signal))))
        self.db.execute("INSERT INTO bot_runtime.finding_state VALUES('archived','archived',2,0),('deleted','deleted',1,0)")
        self.db.execute("INSERT INTO bot_runtime.finding_feedback VALUES('current','live','{}',4,'Keep me',3,0)")
        self.db.commit()
        result=self.call();self.assertEqual([r['id'] for r in result['findings']],['current','legacy'])
        self.assertEqual(result['findings'][0]['feedback']['comment'],'Keep me')
        result=self.call(dict(archived=True));self.assertEqual([r['id'] for r in result['findings']],['archived','current','legacy'])
        self.assertTrue(result['findings'][0]['archived']);self.assertEqual(result['findings'][0]['state_revision'],2)

    def test_israel_repeated_and_nonexistent_minutes(self):
        # Israel repeats 01:30 at the end of DST in 2026.
        for n,utc in enumerate(['2026-10-24T22:30:20+00:00','2026-10-24T23:30:40+00:00','2026-10-24T23:31:00+00:00']):
            s=self.signal(str(n));s['confirmed_at']=int(datetime.fromisoformat(utc).timestamp()*1000);self.insert(str(n),s)
        result=self.call({'from':'2026-10-25T01:30','to':'2026-10-25T01:30'})
        self.assertEqual(len(result['findings']),2)
        self.assertEqual(self.call({'from':'2026-03-27T02:30','to':'2026-03-27T02:30'})['findings'],[])

    def test_permissions_and_invalid_input(self):
        for user,inst in [(OUTSIDER,INST),(OWNER,'ffffffff-ffff-ffff-ffff-ffffffffffff')]:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.call(user=user,installation=inst)
        for filters in [{'strategy':'invalid'},{'direction':'sideways'},{'action':'delete'},{'from':'yesterday'}, {'from':'2026-11-28T00:00','to':'2026-11-27T00:00'},{'cursor':{}},{'asset':None},{'archived':'true'}]:
            with self.subTest(filters=filters),self.assertRaises(psycopg.Error):self.call(filters)
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE authenticated')
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute('SELECT * FROM bot_runtime.signal_search_rows')
        self.db.execute('UPDATE bot_runtime.runtime_control SET enabled=false');self.db.commit()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):self.call()

    def test_catalog_excludes_deleted_archived_and_unmapped_only_assets(self):
        for name in ['ACTIVE','ARCHIVED','DELETED']:
            signal=self.signal(name);signal['symbol']=name;self.insert(name,signal)
        self.db.execute("INSERT INTO bot_runtime.finding_state VALUES('ARCHIVED','archived',1,0),('DELETED','deleted',1,0)")
        self.db.execute("INSERT INTO bot_runtime.signals(id,payload,received) VALUES('orphan',%s,0)",(json.dumps(dict(symbol='UNMAPPED',timeframe='4h',direction='bullish',confirmed_at=1)),))
        self.db.execute("INSERT INTO bot_runtime.legacy_findings VALUES('live','old',%s)",(json.dumps(dict(id='old',signal=dict(symbol='LEGACY',confirmed_at=1))),))
        self.db.commit()
        self.assertEqual(self.call(dict(catalog=True))['assets'],['ACTIVE','LEGACY'])
        self.assertEqual(self.call(dict(catalog=True,archived=True))['assets'],['ACTIVE','ARCHIVED','LEGACY'])

    def test_scanner_can_maintain_search_index(self):
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE trading_worker')
            db.execute("INSERT INTO bot_runtime.signals(id,payload,received) VALUES('worker-write',%s,0)",(json.dumps(self.signal('worker-write')),))

if __name__=='__main__':unittest.main()
