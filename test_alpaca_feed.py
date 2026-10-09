import io
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from alpaca_feed import fallback_asset, fetch_alpaca, request_page, supported
from backtest import run_backtest
from platform_app import Store, load_rules
from scanner import ProviderCandles, TwelveDataRateLimit, fetch_twelve_data, prepare_store, scan_once
from test_scanner import fixture


class AlpacaTests(unittest.TestCase):
    asset = dict(id='NVDA', symbol='NVDA', exchange='NASDAQ', provider='twelvedata',
                 feed_confirmed=True, enabled=True, timeframes=['daily'])

    def payload(self, date='2020-01-02T05:00:00Z', token=None):
        return dict(bars={'NVDA':[dict(t=date, o=2,h=3,l=1,c=2)]},next_page_token=token)

    def test_pagination_exact_symbol_and_closed_history(self):
        with patch('alpaca_feed.request_page', side_effect=[self.payload(token='next'), self.payload('2020-01-03T05:00:00Z')]) as request:
            bars=fetch_alpaca(fallback_asset(self.asset), 'daily', start='2019-01-01T00:00:00Z', end='2020-01-04T05:00:00Z')
        self.assertEqual(len(bars),2)
        self.assertEqual(request.call_count,2)
        self.assertEqual(request.call_args.args[0]['page_token'],'next')
        self.assertEqual(request.call_args.args[0]['adjustment'],'split')
        self.assertEqual(request.call_args.args[0]['feed'],'sip')
        for payload in (dict(bars={'AAPL':[]}), self.payload(token='stuck')):
            with patch('alpaca_feed.request_page',return_value=payload), self.assertRaises(ValueError):
                fetch_alpaca(fallback_asset(self.asset),'daily',start='2019-01-01T00:00:00Z')

    def test_multi_symbol_weekly_screen_batches_and_closes_native_bars(self):
        from datetime import datetime,timezone
        from alpaca_feed import fetch_alpaca_multi
        symbols=[f'T{i:03d}' for i in range(101)]
        def response(params):
            values={symbol:[dict(t='2020-01-06T05:00:00Z',o=2,h=3,l=1,c=2)]
                    for symbol in params['symbols'].split(',')}
            return dict(bars=values)
        end=int(datetime(2020,1,15,tzinfo=timezone.utc).timestamp()*1000)
        with patch.dict(os.environ,{'ALPACA_DATA_FEED':'sip'}),patch('alpaca_feed.request_page',side_effect=response) as request:
            bars=fetch_alpaca_multi(symbols,'weekly',end=end)
        self.assertEqual(set(bars),set(symbols))
        self.assertTrue(all(len(values)==1 and values[0].end<=end for values in bars.values()))
        self.assertEqual(request.call_count,2)

    def test_live_feed_follows_short_pages_instead_of_reporting_missing_history(self):
        with patch('alpaca_feed.request_page',side_effect=[self.payload('2020-01-03T05:00:00Z',token='next'),self.payload()]) as request:
            bars=fetch_alpaca(fallback_asset(self.asset),'4h')
        self.assertEqual(request.call_count,2)
        self.assertEqual(len(bars),2)

    def test_dst_and_invalid_ohlc(self):
        with patch('alpaca_feed.request_page',return_value=self.payload('2020-03-09T04:00:00Z')):
            bars=fetch_alpaca(fallback_asset(self.asset),'daily',end='2020-03-10T00:00:00Z')
        self.assertEqual(bars[0].end-bars[0].start,16*3600000)
        payload=self.payload();payload['bars']['NVDA'][0]['h']=0
        with patch('alpaca_feed.request_page',return_value=payload), self.assertRaisesRegex(ValueError,'OHLC'):
            fetch_alpaca(fallback_asset(self.asset),'daily',end='2020-01-05T00:00:00Z')

    def test_safe_auth_and_errors(self):
        with patch.dict(os.environ,{'ALPACA_API_KEY':'test-key','ALPACA_SECRET_KEY':'test-secret'}), patch('alpaca_feed.urllib.request.urlopen',return_value=io.StringIO('{}')) as request:
            request_page({'symbols':'NVDA'})
        req=request.call_args.args[0]
        self.assertNotIn('test-secret',req.full_url)
        self.assertTrue(req.full_url.startswith('https://data.alpaca.markets/'))
        with patch.dict(os.environ,{'ALPACA_API_KEY':'test-key','ALPACA_SECRET_KEY':'test-secret'}), patch('alpaca_feed.urllib.request.urlopen',side_effect=urllib.error.HTTPError('private',401,'private',{},None)):
            with self.assertRaisesRegex(ValueError,'credentials') as error:request_page({})
        self.assertNotIn('private',str(error.exception))

    def test_only_quota_errors_trigger_fallback_and_isolated_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            cfg=dict(assets=[self.asset],timeframes=['daily'])
            with patch('scanner.fetch_twelve_data',return_value=fixture()):
                scan_once(store,cfg,load_rules(),fixture()[-1].end)
            with store.connect() as db:db.execute('DELETE FROM scan_schedule')
            with patch('scanner.fetch_twelve_data',side_effect=TwelveDataRateLimit('quota')), patch('alpaca_feed.fetch_alpaca',return_value=fixture()) as fallback:
                coverage=scan_once(store,cfg,load_rules(),fixture()[-1].end)
            self.assertEqual(coverage[0]['provider'],'alpaca')
            self.assertEqual(coverage[0]['status'],'baseline set')
            self.assertEqual(store.rows(),[])
            with store.connect() as db:
                self.assertEqual(db.execute('select count(distinct feed) from candle_cache').fetchone()[0],2)
            for asset,err in ((self.asset,ValueError('bad key')),(self.asset|dict(symbol='EUR/USD',exchange='',market='forex'),TwelveDataRateLimit('quota'))):
                with patch('scanner.fetch_twelve_data',side_effect=err),patch('alpaca_feed.fetch_alpaca') as fallback:
                    with store.connect() as db:db.execute('DELETE FROM scan_schedule')
                    scan_once(store,dict(assets=[asset],timeframes=['daily']),load_rules(),fixture()[-1].end)
                    fallback.assert_not_called()
            with store.connect() as db:db.execute('DELETE FROM scan_schedule')
            with patch('scanner.fetch_twelve_data',side_effect=TwelveDataRateLimit('quota')),patch('alpaca_feed.fetch_alpaca') as fallback:
                scan_once(store,cfg|dict(alpaca_rate_limit_fallback=False),load_rules(),fixture()[-1].end)
                fallback.assert_not_called()

    def test_indices_and_other_markets_are_excluded(self):
        for market in ('forex','crypto','index','indices','commodity'):
            self.assertFalse(supported(self.asset | dict(market=market)))

    def test_direct_etf_hourly_routing_has_its_own_baseline(self):
        from managed_assets import validate_asset, for_timeframe
        asset = self.asset | dict(id='DAX-ETF', symbol='DAX', market='etf', provider='alpaca',
                                 timeframes=['1h'], tradingview_symbol='NASDAQ:DAX')
        validate_asset(asset)
        feed = for_timeframe(asset, '1h')
        self.assertEqual(feed['exchange'], 'Alpaca-sip-split')
        self.assertEqual(feed['tradingview_symbol'], 'NASDAQ:DAX')
        with self.assertRaisesRegex(ValueError, 'US equity'):
            validate_asset(asset | dict(market='index'))
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'db'); prepare_store(store)
            with patch('alpaca_feed.fetch_alpaca', return_value=fixture()) as fetch, patch('scanner.fetch_twelve_data') as twelve:
                coverage = scan_once(store, dict(assets=[asset], timeframes=['1h']), load_rules(), fixture()[-1].end)
            twelve.assert_not_called()
            self.assertEqual(fetch.call_args.args[1], '1h')
            self.assertEqual(coverage[0]['status'], 'baseline set')
            self.assertEqual(coverage[0]['provider'], 'alpaca')
            self.assertEqual(store.rows(), [])
            with store.connect() as db:
                self.assertEqual(db.execute('select distinct feed from candle_cache').fetchone()[0], 'alpaca:Alpaca-sip-split:DAX')

    def test_hourly_historical_replay_is_supported(self):
        from backtest import replay
        from backtest_jobs import validate
        self.assertEqual(validate(dict(assets=['DAX-ETF'], timeframes=['1h'], strategy='confirmed'), ['DAX-ETF'])[1], ['1h'])
        self.assertIn(replay(fixture(), load_rules(), 'alpaca:Alpaca-sip-split:DAX', '1h')['status'], ('partial history', 'replayed'))

    def test_twelve_http_and_payload_quota_classification(self):
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':'test'}):
            for response in (io.StringIO(json.dumps(dict(status='error',code='429'))),None):
                ctx=patch('scanner.urllib.request.urlopen',return_value=response) if response else patch('scanner.urllib.request.urlopen',side_effect=urllib.error.HTTPError('secret',429,'secret',{},None))
                with ctx,self.assertRaises(TwelveDataRateLimit):fetch_twelve_data(self.asset,'daily')

    def test_alpaca_backtest_never_sends_alerts(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('alpaca_feed.fetch_alpaca',return_value=ProviderCandles(fixture(),['Alpaca sip'])) as fetch:
                report=run_backtest(store,dict(assets=[fallback_asset(self.asset)],timeframes=['daily']),load_rules())
            self.assertEqual(report['results'][0]['source']['provider'],'Alpaca')
            self.assertEqual(fetch.call_args.kwargs['start'],'2016-01-01T00:00:00Z')
            self.assertEqual(store.rows(),[])

    def test_backtest_chart_uses_alpaca_evidence_not_csv(self):
        from backtest import START
        from candle_views import detail
        from finding_feedback import finding_id
        bars=fixture()
        delta=START+14400000-bars[258].end
        bars=ProviderCandles([replace(c,start=c.start+delta,end=c.end+delta) for c in bars],['Alpaca sip'])
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('alpaca_feed.fetch_alpaca',return_value=bars):
                report=run_backtest(store,dict(assets=[fallback_asset(self.asset)],timeframes=['4h']),load_rules()|{'max_band_slope_pct':1.5})
            signal=report['results'][0]['signals'][0]
            self.assertTrue(signal['symbol'].startswith('alpaca:Alpaca-sip-split:'))
            chart=detail(store,Path(folder),'backtest',finding_id('backtest',signal))
            self.assertTrue(chart['candles'])
            self.assertEqual(chart['candles'][0]['close'],next(c.close for c in bars if c.start==chart['candles'][0]['start']))

    def test_live_incomplete_candles_excluded_and_native_intervals(self):
        for frame,expected in [('1h','1Hour'),('4h','4Hour'),('daily','1Day'),('weekly','1Week'),('monthly','1Month')]:
            with patch('alpaca_feed.request_page',return_value=self.payload()) as request:
                fetch_alpaca(fallback_asset(self.asset),frame,end='2020-01-02T05:30:00Z')
            self.assertEqual(request.call_args.args[0]['timeframe'],expected)
        with patch('alpaca_feed.request_page',return_value=self.payload()):
            self.assertEqual(fetch_alpaca(fallback_asset(self.asset),'1h',end='2020-01-02T05:30:00Z'),[])
