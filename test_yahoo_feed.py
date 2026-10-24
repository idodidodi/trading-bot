from test_postgres_support import TestStore as Store, PostgresTestCase
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from managed_assets import ensure, get, validate_asset
from platform_app import load_rules
from scanner import prepare_store, scan_once, timestamp
from test_scanner import fixture
import yahoo_feed as yahoo


class YahooTests(PostgresTestCase):
    asset = dict(id='DAX', provider='yahoo', symbol='^GDAXI', exchange='XETRA', market='index',
                 feed_confirmed=True, enabled=True, timeframes=['monthly', 'weekly', 'daily', '4h'])

    def chart(self, stamps, interval='1d', sessions=None):
        return dict(meta=dict(symbol='^GDAXI', currency='EUR', instrumentType='INDEX', exchangeName='GER',
                              exchangeTimezoneName='Europe/Berlin', dataGranularity=interval,
                              tradingPeriods=sessions or []), timestamp=[timestamp(t)//1000 for t in stamps],
                    indicators=dict(quote=[dict(open=[10]*len(stamps), high=[13]*len(stamps),
                                                low=[9]*len(stamps), close=[12]*len(stamps))]))

    def test_daily_current_day_and_publication_delay_excluded(self):
        data = self.chart(['2026-10-21T07:00:00Z', '2026-10-22T07:00:00Z'])
        bars = yahoo.candles_from_chart(data, 'daily', timestamp('2026-10-23T00:15:00Z'))
        self.assertEqual(len(bars), 1)
        bars = yahoo.candles_from_chart(data, 'daily', timestamp('2026-10-23T00:16:00Z'))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[-1].end, timestamp('2026-10-23T00:00:00Z'))

    def test_session_alignment_short_final_bar_and_quote_snapshot(self):
        opening, closing = timestamp('2026-10-22T07:00:00Z')//1000, timestamp('2026-10-22T15:30:00Z')//1000
        data = self.chart(['2026-10-22T07:00:00Z','2026-10-22T11:00:00Z',
                           '2026-10-22T15:00:00Z','2026-10-22T15:12:01Z'], '4h',
                          [[dict(start=opening, end=closing)]])
        bars = yahoo.candles_from_chart(data, '4h', timestamp('2026-10-22T15:45:00Z'))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars.next_close_at, timestamp('2026-10-22T15:46:00Z'))
        bars = yahoo.candles_from_chart(data, '4h', timestamp('2026-10-22T15:46:00Z'))
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[-1].end, closing*1000)
        self.assertTrue(any('snapshot' in n for n in bars.notes))

    def test_dst_uses_returned_session_boundaries(self):
        stamps=['2026-10-23T07:00:00Z','2026-10-26T08:00:00Z']
        sessions=[[dict(start=timestamp(t)//1000, end=timestamp(t)//1000+30600)] for t in stamps]
        bars=yahoo.candles_from_chart(self.chart(stamps, '1h', sessions),'1h',timestamp('2026-10-27T00:00:00Z'))
        self.assertEqual([b.end-b.start for b in bars],[3600000]*2)

    def test_weekly_and_monthly_aggregation_and_open_periods(self):
        stamps=[f'2026-09-{n:02}T07:00:00Z' for n in range(1,31)]
        data=self.chart(stamps)
        self.assertEqual(len(yahoo.candles_from_chart(data,'monthly',timestamp('2026-10-01T00:15:00Z'))),0)
        bars=yahoo.candles_from_chart(data,'monthly',timestamp('2026-10-01T00:16:00Z'))
        self.assertEqual(len(bars),1)
        self.assertEqual((bars[0].open,bars[0].high,bars[0].low,bars[0].close),(10,13,9,12))
        self.assertEqual(len(yahoo.candles_from_chart(data,'weekly',timestamp('2026-10-01T00:16:00Z'))),3)

    def test_missing_ohlc_omits_affected_aggregate(self):
        data=self.chart(['2026-09-01T07:00:00Z','2026-09-02T07:00:00Z','2026-10-01T07:00:00Z'])
        data['indicators']['quote'][0]['close'][1]=None
        bars=yahoo.candles_from_chart(data,'monthly',timestamp('2026-11-01T01:00:00Z'))
        self.assertEqual(len(bars),1)
        self.assertEqual(bars[0].start,timestamp('2026-10-01T00:00:00Z'))
        self.assertTrue(any('missing OHLC' in n for n in bars.notes))

    def test_wrong_instrument_interval_and_bad_candles_rejected(self):
        for field,value in [('symbol','DAX'),('currency','USD'),('instrumentType','ETF'),
                            ('exchangeName','NMS'),('exchangeTimezoneName','UTC'),('dataGranularity','3mo')]:
            data=self.chart(['2026-10-21T07:00:00Z']);data['meta'][field]=value
            with self.assertRaises(ValueError):yahoo.candles_from_chart(data,'daily',timestamp('2026-10-23T01:00:00Z'))
        for value in [5,float('inf')]:
            data=self.chart(['2026-10-21T07:00:00Z']);data['indicators']['quote'][0]['high'][0]=value
            if value==5:
                with self.assertRaises(ValueError):yahoo.candles_from_chart(data,'daily',timestamp('2026-10-23T01:00:00Z'))
            else:
                self.assertEqual(len(yahoo.candles_from_chart(data,'daily',timestamp('2026-10-23T01:00:00Z'))),0)
        data=self.chart(['2026-10-21T07:00:00Z']*2)
        with self.assertRaises(ValueError):yahoo.candles_from_chart(data,'daily',timestamp('2026-10-23T01:00:00Z'))

    def test_missing_session_and_invalid_asset_rejected(self):
        data=self.chart(['2026-10-21T07:00:00Z'],'4h')
        with self.assertRaisesRegex(ValueError,'session'):yahoo.candles_from_chart(data,'4h',timestamp('2026-10-23T01:00:00Z'))
        validate_asset(self.asset)
        for asset in [self.asset|dict(symbol='DAX'),self.asset|dict(exchange='NASDAQ'),
                      self.asset|dict(market='etf'),self.asset|dict(feed_confirmed=False)]:
            with self.assertRaises(ValueError):validate_asset(asset)

    def test_request_cache_and_safe_errors(self):
        yahoo._cache.clear()
        data={'chart':{'result':[self.chart(['2026-10-21T07:00:00Z'])],'error':None}}
        with patch('yahoo_feed.urllib.request.urlopen',return_value=io.StringIO(json.dumps(data))) as request:
            yahoo.request_chart('1d');yahoo.request_chart('1d')
            self.assertEqual(request.call_count,1)
        yahoo._cache.clear()
        with patch('yahoo_feed.urllib.request.urlopen',side_effect=urllib.error.HTTPError('secret',429,'secret',{},None)):
            with self.assertRaisesRegex(ValueError,'quota') as caught:yahoo.request_chart('1d')
            self.assertNotIn('secret',str(caught.exception))

    def test_instrument_cache_isolation_and_futures_metadata(self):
        yahoo._cache.clear()
        dax=self.chart(['2026-10-21T07:00:00Z'])
        es=self.chart(['2026-10-21T04:00:00Z'])
        es['meta'].update(symbol='ES=F',currency='USD',instrumentType='FUTURE',exchangeName='CME',
                          exchangeTimezoneName='America/New_York')
        payload=lambda result:io.StringIO(json.dumps(dict(chart=dict(result=[result],error=None))))
        with patch('yahoo_feed.urllib.request.urlopen',side_effect=[payload(dax),payload(es)]) as request:
            self.assertEqual(yahoo.request_chart('1d')['meta']['symbol'],'^GDAXI')
            self.assertEqual(yahoo.request_chart('1d','ES=F')['meta']['symbol'],'ES=F')
            self.assertEqual(yahoo.request_chart('1d')['meta']['symbol'],'^GDAXI')
            self.assertEqual(request.call_count,2)
        bars=yahoo.candles_from_chart(es,'daily',timestamp('2026-10-23T01:00:00Z'),'ES=F')
        self.assertEqual(bars[0].start,timestamp('2026-10-21T00:00:00Z'))
        self.assertTrue(any('roll gaps' in n for n in bars.notes))
        with self.assertRaises(ValueError):yahoo.candles_from_chart(es,'daily',timestamp('2026-10-23T01:00:00Z'))
        for symbol,exchange in [('ES=F','CME'),('CL=F','NYMEX')]:
            validate_asset(self.asset|dict(symbol=symbol,exchange=exchange,market='futures'))
            with self.assertRaises(ValueError):validate_asset(self.asset|dict(symbol=symbol,exchange=exchange))

    def test_futures_overnight_buckets_use_new_york_dates(self):
        stamps=['2026-10-22T04:00:00Z','2026-10-22T08:00:00Z','2026-10-23T00:00:00Z',
                '2026-10-23T02:12:31Z']
        opening=timestamp('2026-10-22T04:00:00Z')//1000
        closing=timestamp('2026-10-23T03:59:00Z')//1000
        chart=self.chart(stamps,'4h',[[dict(start=opening,end=closing)]])
        chart['meta'].update(symbol='ES=F',currency='USD',instrumentType='FUTURE',exchangeName='CME',
                             exchangeTimezoneName='America/New_York')
        bars=yahoo.candles_from_chart(chart,'4h',timestamp('2026-10-23T04:15:00Z'),'ES=F')
        self.assertEqual(len(bars),3)
        self.assertEqual(bars[-1].end,closing*1000)

    def test_scanner_baseline_cache_and_provider_deadline(self):
        from scanner import ProviderCandles
        from scan_schedule import next_due
        bars=ProviderCandles(fixture(),['Yahoo test data']);bars.next_close_at=fixture()[-1].end+900000
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('yahoo_feed.fetch_yahoo',return_value=bars):
                rows=scan_once(store,dict(assets=[self.asset|dict(timeframes=['4h'])],timeframes=['4h']),load_rules(),bars[-1].end)
            self.assertEqual(rows[0]['status'],'baseline set')
            self.assertEqual(rows[0]['provider'],'yahoo')
            self.assertEqual(store.rows(),[])
            with store.connect() as db:self.assertEqual(db.execute('select distinct feed from candle_cache').fetchone()[0],'yahoo:XETRA:^GDAXI')
            self.assertEqual(next_due(self.asset,'4h',bars,bars,bars[-1].end,rows[0]),bars.next_close_at)
            self.assertEqual(next_due(self.asset,'monthly',bars,bars,bars[-1].end,dict(status='unavailable')),
                             bars[-1].end+3600000)

    def activation_module(self):
        spec=importlib.util.spec_from_file_location('activate_yahoo',Path(__file__).parent/'scripts/activate-yahoo-dax.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_failed_activation_preserves_config(self):
        module=self.activation_module()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            ensure(store,dict(assets=[self.asset|dict(provider='csv',enabled=False)],timeframes=['daily']))
            before=get(store)
            for response in [ValueError('unavailable'),[]]:
                kwargs={'side_effect':response} if isinstance(response,Exception) else {'return_value':response}
                with patch.object(module,'fetch_yahoo',**kwargs),self.assertRaises(ValueError):module.activate(store)
                self.assertEqual(get(store),before)

    def test_activation_preserves_other_assets_and_removes_unavailable_reason(self):
        module=self.activation_module()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            other=dict(id='SPX',provider='csv',enabled=False,timeframes=['daily'])
            ensure(store,dict(assets=[self.asset|dict(provider='csv',enabled=False,unavailable_reason='old'),other],timeframes=['daily']))
            with patch.object(module,'fetch_yahoo',return_value=fixture()):result=module.activate(store)
            assets=get(store)['config']['assets']
            self.assertEqual(assets[1]['id'],'SPX');self.assertFalse(assets[1]['enabled'])
            self.assertEqual(assets[0]['provider'],'yahoo');self.assertTrue(assets[0]['enabled'])
            self.assertNotIn('unavailable_reason',assets[0]);self.assertGreater(result['revision'],1)


if __name__=='__main__':unittest.main()
