import copy
from datetime import datetime
import json
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer
import urllib.request
import urllib.error

import signal_discovery as d
from signal_service import register,publish,handler
from signal_contract import signal_event
from test_postgres_support import PostgresTestCase,TestStore

NOW=1794052800000
SECRET='test-signing-secret-not-a-real-token'
SOURCE='kraken:Kraken:BTC/USD'


def policy(now=NOW,**extra):
    return dict(id='fixture-crypto',version='fixture-1',timezone='UTC',verified=True,
        evidence='Test fixture only; not a production calendar',valid_from=now-86400000,
        valid_until=now+86400000,continuous=True,sessions=[])|extra


class DiscoveryTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        self.project=register(self.store,dict(project='finder'))
        self.token=self.project['token']
        d.administer(self.store,'finder',dict(enabled=True,filters={},max_lookback_minutes=1440))
        asset=dict(id='BTC',provider='kraken',exchange='Kraken',symbol='BTC/USD',market='crypto',enabled=True,timeframes=['4h'],scan_strategies=['rsi'])
        self.config=dict(assets=[asset],timeframes=['4h'])
        self.coverage=asset|dict(asset='BTC',timeframe='4h',status='scanned',checked_at=NOW-1000,last_closed_at=NOW-120000,next_close_at=NOW+100000,limited_history=False)
        with self.store.connect() as db:
            db.execute('INSERT INTO managed_config VALUES(1,1,1,%s)',(json.dumps(self.config),))
            db.execute('INSERT INTO scanner_status VALUES(1,%s)',(json.dumps(dict(coverage=[self.coverage])),))
            db.execute('INSERT INTO signal_discovery_calendars VALUES(%s,%s)',(SOURCE,json.dumps(policy())))
        self.signal=dict(symbol=SOURCE,source=SOURCE,market='crypto',timeframe='4h',direction='bullish',confirmed_at=NOW-120000,pivot2=NOW-200000,version='fixture-rsi',signal_status='provisional')
        self.emit()

    def emit(self,value=None,kind='detected',origin='live'):
        with patch('signal_service.time.time',return_value=(NOW-1000)/1000):
            with self.store.connect() as db:publish(db,'rsi',value or self.signal,kind=kind,origin=origin,instrument='BTC/USD')

    def find(self,params=None,**kwargs):
        return d.discover(self.store,self.token,params or {},SECRET,now=NOW,**kwargs)

    def state(self):
        with self.store.connect() as db:return db.execute('SELECT floor,ack,delivered FROM signal_subscriptions WHERE project=%s',('finder',)).fetchone()

    def set_coverage(self,**updates):
        self.coverage.update(updates)
        with self.store.connect() as db:db.execute('UPDATE scanner_status SET payload=%s',(json.dumps(dict(coverage=[self.coverage])),))

    def test_read_only_search_and_catalogue_do_not_advance_delivery(self):
        before=self.state()
        with patch('signal_service.consume',side_effect=AssertionError('Execution feed called')),patch('subprocess.Popen',side_effect=AssertionError('Worker started')):
            result=self.find();catalogue=self.find(route='catalogue')
        self.assertEqual(len(result['items']),1);self.assertEqual(self.state(),before)
        item=result['items'][0]
        self.assertEqual(item['instrument']['source'],SOURCE)
        self.assertEqual(item['instrument']['exchange'],'Kraken')
        self.assertNotIn('delivery_mode',item);self.assertFalse(item['execution_authorized'])
        self.assertEqual(catalogue['items'],[]);self.assertFalse(result['coverage']['complete'])
        self.assertEqual(catalogue['filters']['strategy'],['rsi'])

    def test_auth_revocation_and_explicit_grant(self):
        other=register(self.store,dict(project='no-access'))
        with self.assertRaises(d.DiscoveryError) as error:d.discover(self.store,other['token'],{},SECRET,now=NOW)
        self.assertEqual(error.exception.status,403)
        with self.assertRaises(PermissionError):d.discover(self.store,'invalid',{},SECRET,now=NOW)
        with self.store.connect() as db:db.execute("UPDATE signal_subscriptions SET enabled=false WHERE project='finder'")
        with self.assertRaises(PermissionError):self.find()

    def test_discovery_grant_is_independent_of_execution_subscription_floor(self):
        later=register(self.store,dict(project='later',filters={'symbol':['OTHER']}))
        d.administer(self.store,'later',dict(enabled=True,filters={'source':[SOURCE]},max_lookback_minutes=60))
        result=d.discover(self.store,later['token'],{},SECRET,now=NOW)
        self.assertEqual(len(result['items']),1)
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT floor,ack,delivered FROM signal_subscriptions WHERE project='later'").fetchone(),(1,1,1))

    def test_scope_and_empty_filters(self):
        self.assertEqual(len(self.find({'marketCategory':[''],'strategy':[]})['items']),1)
        self.assertEqual(self.find({'marketCategory':['Stocks & ETFs']})['items'],[])
        d.administer(self.store,'finder',dict(enabled=True,filters={'source':['different']}))
        result=self.find({'source':[SOURCE]})
        self.assertEqual(result['items'],[]);self.assertEqual(result['coverage']['combinations'],[])
        with self.assertRaises(ValueError):self.find({'marketCategory':['stock']})
        with self.assertRaises(ValueError):self.find({'strategy':['invented']})

    def test_latest_invalidation_cannot_be_hidden_by_filtering(self):
        self.emit(kind='invalidated')
        result=self.find({'strategy':['rsi']})
        self.assertEqual(result['items'],[]);self.assertEqual(result['excluded']['invalidated'],1)
        sid=signal_event('rsi',self.signal)['signal_id']
        detail=self.find(signal_id=sid)['items'][0]
        self.assertFalse(detail['eligible']);self.assertEqual(detail['lifecycle'],'invalidated')

    def test_old_signal_not_refreshed_by_update_or_publication(self):
        old=self.signal|dict(confirmed_at=NOW-7200000,pivot2=NOW-7300000,observed_at=NOW-1000)
        self.emit(old,kind='updated')
        result=self.find()
        self.assertEqual(len(result['items']),1);self.assertEqual(result['excluded']['stale_signal'],1)

    def test_unavailable_stale_and_missing_coverage_fail_closed(self):
        for change,reason in [(dict(checked_at=NOW-300001),'stale_feed_check'),(dict(checked_at=NOW-1000,status='unavailable'),'not_ready'),(dict(status='scanned',limited_history=True),'insufficient_history')]:
            self.set_coverage(**change)
            self.assertEqual(self.find()['excluded'][reason],1)

    def test_explicit_closed_market_and_missing_calendar_remain_excluded(self):
        with self.store.connect() as db:
            db.execute('UPDATE signal_discovery_calendars SET payload=%s',(json.dumps(policy(continuous=False,sessions=[[NOW+1000,NOW+2000]])),))
        self.assertEqual(self.find({'marketCategory':['Crypto']})['excluded']['outside_session'],1)
        with self.store.connect() as db:db.execute('DELETE FROM signal_discovery_calendars')
        self.assertEqual(self.find()['excluded']['missing_calendar'],1)

    def test_signed_pagination_snapshot_changes_and_revocation(self):
        self.emit(self.signal|dict(pivot2=NOW-300000))
        first=self.find({'limit':['1']});token=first['next_page_token']
        self.assertIsNotNone(token)
        second=self.find({'limit':['1'],'page_token':[token]})
        self.assertNotEqual(first['items'][0]['signal_id'],second['items'][0]['signal_id'])
        self.assertTrue(second['page_complete'])
        with self.assertRaises(ValueError):self.find({'page_token':[token+'bad']})
        self.emit(kind='invalidated')
        with self.assertRaises(d.DiscoveryError) as error:self.find({'limit':['1'],'page_token':[token]})
        self.assertEqual(error.exception.status,409)
        with self.assertRaises(d.DiscoveryError) as error:
            d.discover(self.store,self.token,{'limit':['1'],'page_token':[token]},SECRET,now=NOW+60001)
        self.assertEqual(error.exception.status,410)

    def test_page_crossing_session_close_requires_refresh(self):
        self.emit(self.signal|dict(pivot2=NOW-300000))
        with self.store.connect() as db:db.execute('UPDATE signal_discovery_calendars SET payload=%s',(json.dumps(policy(continuous=False,sessions=[[NOW-5000,NOW+5000]])),))
        token=self.find({'limit':['1']})['next_page_token']
        with self.assertRaises(d.DiscoveryError) as error:
            d.discover(self.store,self.token,{'limit':['1'],'page_token':[token]},SECRET,now=NOW+5000)
        self.assertEqual(error.exception.status,409)

    def test_replay_and_disabled_asset_are_not_candidates(self):
        self.emit(self.signal|dict(pivot2=NOW-300000),origin='replay')
        self.assertEqual(self.find()['excluded']['replay_only'],1)
        self.config['assets'][0]['enabled']=False
        with self.store.connect() as db:db.execute('UPDATE managed_config SET payload=%s',(json.dumps(self.config),))
        self.assertEqual(self.find()['items'],[])

    def test_capacity_is_explicit_not_silent_truncation(self):
        with patch('signal_discovery.MAX_ROWS',0),self.assertRaises(d.DiscoveryError) as error:self.find()
        self.assertEqual(error.exception.code,'discovery_capacity_exceeded')

    def test_unapplied_or_changed_provider_configuration_cannot_reuse_old_coverage(self):
        with self.store.connect() as db:db.execute('UPDATE managed_config SET revision=2')
        result=self.find()
        self.assertEqual(result['items'],[])
        self.assertFalse(result['coverage']['configuration_applied'])
        self.config['assets'][0]['provider']='other-provider'
        with self.store.connect() as db:db.execute('UPDATE managed_config SET applied_revision=2,payload=%s',(json.dumps(self.config),))
        self.assertEqual(self.find()['items'],[])

    def test_missing_coverage_and_future_observation_do_not_look_fresh(self):
        self.set_coverage(last_closed_at=NOW-300000)
        self.assertEqual(self.find()['excluded']['coverage_behind_signal'],1)
        self.emit(self.signal|dict(observed_at=NOW+1),kind='updated')
        self.assertEqual(self.find()['excluded']['invalid_timestamp'],1)

    def test_http_adapter_and_admin_grant_do_not_use_event_cursor(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store,SECRET))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            before=self.state()
            req=urllib.request.Request(base+'/v1/discovery?marketCategory=',headers={'Authorization':'Bearer '+self.token})
            with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['discovery_version'],1)
            self.assertEqual(self.state(),before)
            req=urllib.request.Request(base+'/v1/projects/discovery',headers={'Authorization':'Bearer '+SECRET},data=json.dumps(dict(project='finder',discovery=dict(enabled=False))).encode())
            with urllib.request.urlopen(req) as response:self.assertFalse(json.load(response)['discovery']['enabled'])
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(base+'/v1/discovery',headers={'Authorization':'Bearer '+self.token}))
            self.assertEqual(error.exception.code,403)
        finally:server.shutdown();server.server_close();thread.join()


class CalendarTests(unittest.TestCase):
    def test_no_unknown_futures_or_continuous_equity_assumption(self):
        self.assertEqual(d.session(dict(marketCategory='Futures'),None,NOW)['status'],'unknown')
        self.assertEqual(d.session(dict(marketCategory='Stocks & ETFs'),policy(),NOW)['status'],'unknown')
        self.assertEqual(d.session(dict(marketCategory='Crypto'),policy(valid_until=NOW),NOW)['reason'],'expired_calendar')

    def test_reviewed_timezone_dst_early_close_holiday_and_weekend(self):
        ny=ZoneInfo('America/New_York')
        def ms(day,hour,minute=0):return int(datetime.fromisoformat(day).replace(hour=hour,minute=minute,tzinfo=ny).timestamp()*1000)
        intervals=[[ms('2026-03-06',9,30),ms('2026-03-06',16)], [ms('2026-03-09',9,30),ms('2026-03-09',13)]]
        p=policy(valid_from=ms('2026-03-01',0),valid_until=ms('2026-03-20',0),continuous=False,sessions=intervals,timezone='America/New_York')
        asset=dict(marketCategory='Stocks & ETFs')
        for day in ('2026-03-06','2026-03-09'):self.assertEqual(d.session(asset,p,ms(day,10))['status'],'open')
        for day,hour in [('2026-03-07',10),('2026-03-08',10),('2026-03-09',13),('2026-03-10',10)]:self.assertEqual(d.session(asset,p,ms(day,hour))['status'],'closed')
        self.assertNotEqual(intervals[0][0]%86400000,intervals[1][0]%86400000)

    def test_unreviewed_overlapping_or_malformed_calendars_rejected(self):
        for bad in (policy(verified=False),policy(continuous=False,sessions=[[NOW,NOW+100],[NOW+50,NOW+200]]),policy(timezone='not/a-zone')):
            with self.assertRaises(ValueError):d.calendar(bad)
