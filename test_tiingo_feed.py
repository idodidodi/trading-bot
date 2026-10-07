from datetime import datetime, timezone
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from tiingo_feed import fetch_tiingo, request_json
from managed_assets import ensure, for_timeframe, get, validate_asset
from platform_app import Store, load_rules
from scanner import Candle, ProviderCandles, prepare_store, scan_once, timestamp
from test_scanner import fixture


class TiingoTests(unittest.TestCase):
    asset = dict(id='EURUSD', symbol='EUR/USD', provider='tiingo', exchange='Tiingo',
                 enabled=True, feed_confirmed=True, timeframes=['daily'])

    def data(self, stamp='2026-10-05T00:00:00Z', ticker='eurusd'):
        return [dict(ticker=ticker,date=stamp,open=1.1,high=1.2,low=1,close=1.15)]

    def test_auth_exact_feed_cache_and_persistent_quota(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,dict(DATA_DIR=folder,TIINGO_API_KEY='secret',TIINGO_REQUESTS_PER_HOUR='1',TIINGO_REQUESTS_PER_DAY='1')):
            with patch('tiingo_feed.urllib.request.urlopen',return_value=io.StringIO(json.dumps(self.data()))) as request:
                self.assertEqual(request_json('EUR/USD','1hour','2026-10-01','2026-10-06'),self.data())
            req=request.call_args.args[0]
            self.assertEqual(req.get_method(),'GET')
            self.assertEqual(req.get_header('Authorization'),'Token secret')
            self.assertNotIn('secret',req.full_url)
            self.assertTrue(req.full_url.startswith('https://api.tiingo.com/tiingo/fx/eurusd/prices?'))
            with patch('tiingo_feed.urllib.request.urlopen') as request:
                request_json('EUR/USD','1hour','2026-10-01','2026-10-06')
                request.assert_not_called()
                with self.assertRaisesRegex(ValueError,'budget'):
                    request_json('GBP/USD','1hour','2026-10-01','2026-10-06')
            contents=Path(folder,'tiingo-cache.sqlite3').read_bytes()
            self.assertNotIn(b'secret',contents)

    def test_native_frames_pair_validation_and_incomplete_bar(self):
        for frame,freq in [('1h','1hour'),('4h','4hour'),('daily','1day')]:
            with patch('tiingo_feed.request_json',return_value=self.data()) as request:
                bars=fetch_tiingo(self.asset,frame,end='2026-10-05T00:30:00Z')
                self.assertEqual(bars,[])
                self.assertEqual(request.call_args.args[1],freq)
        for data,expected in [(self.data(ticker='eurjpy'),'different'),(self.data()+self.data(),'Duplicate'),(self.data('2026-10-05T00:00:00'),'timezone')]:
            with patch('tiingo_feed.request_json',return_value=data),self.assertRaisesRegex(ValueError,expected):
                fetch_tiingo(self.asset,'1h')
        bad=self.data();bad[0]['low']=2
        with patch('tiingo_feed.request_json',return_value=bad),self.assertRaisesRegex(ValueError,'OHLC'):
            fetch_tiingo(self.asset,'daily')
        with self.assertRaises(ValueError):fetch_tiingo(self.asset|dict(symbol='DAX'),'daily')

    def test_calendar_aggregation_excludes_partial_months(self):
        data=[]
        for stamp,op,cl in [('2026-08-31T00:00:00Z',1.1,1.15),('2026-09-01T00:00:00Z',1.11,1.16),('2026-09-30T00:00:00Z',1.13,1.17),('2026-10-01T00:00:00Z',1.14,1.18)]:
            row=self.data(stamp)[0];row.update(open=op,close=cl);data.append(row)
        with patch('tiingo_feed.request_json',return_value=data):
            bars=fetch_tiingo(self.asset,'monthly',end='2026-10-07T00:00:00Z')
        self.assertEqual(len(bars),1)
        self.assertEqual((bars[0].open,bars[0].close,bars[0].high,bars[0].low),(1.11,1.17,1.2,1))
        self.assertEqual(bars[0].start,timestamp('2026-09-01T00:00:00Z'))
        self.assertEqual(bars[0].end,timestamp('2026-10-01T00:00:00Z'))
        with patch('tiingo_feed.request_json',return_value=self.data('2026-09-01T01:00:00Z')),self.assertRaisesRegex(ValueError,'midnight'):
            fetch_tiingo(self.asset,'weekly')

    def test_errors_sanitized_and_auth_classified(self):
        for code,body,expected in [(403,b'{"detail":"Invalid token. private"}','token'),(403,b'private','access'),(429,b'private','quota')]:
            with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,dict(DATA_DIR=folder,TIINGO_API_KEY='secret')):
                error=urllib.error.HTTPError('private',code,'private',{},io.BytesIO(body))
                with patch('tiingo_feed.urllib.request.urlopen',side_effect=error),self.assertRaisesRegex(ValueError,expected) as caught:
                    request_json('EUR/USD','1hour','2026-10-01','2026-10-06')
                self.assertNotIn('private',str(caught.exception))

    def test_routing_cache_identity_and_baseline(self):
        validate_asset(self.asset)
        with self.assertRaises(ValueError):validate_asset(self.asset|dict(symbol='DAX'))
        mixed=self.asset|dict(provider='twelvedata',exchange='',tradingview_symbol='OANDA:EURUSD',timeframe_providers={'daily':'tiingo'})
        validate_asset(mixed)
        self.assertEqual(for_timeframe(mixed,'daily')['exchange'],'Tiingo-UTC')
        self.assertEqual(for_timeframe(mixed,'daily')['tradingview_symbol'],'')
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('tiingo_feed.fetch_tiingo',return_value=fixture()):
                rows=scan_once(store,dict(assets=[self.asset],timeframes=['daily']),load_rules(),fixture()[-1].end)
            self.assertEqual(rows[0]['status'],'baseline set')
            self.assertEqual(rows[0]['provider'],'tiingo')
            self.assertEqual(store.rows(),[])
            with store.connect() as db:self.assertEqual(db.execute('select distinct feed from candle_cache').fetchone()[0],'tiingo:Tiingo-UTC:EUR/USD')

    def test_failed_activation_preserves_config_and_verified_pairs_only(self):
        spec=importlib.util.spec_from_file_location('activate_tiingo',Path(__file__).parent/'scripts/activate-tiingo.py')
        activation=importlib.util.module_from_spec(spec);spec.loader.exec_module(activation)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            assets=[self.asset|dict(provider='twelvedata',exchange='',market='forex'),self.asset|dict(id='USDCNY',symbol='USD/CNY',provider='twelvedata',exchange='',market='forex')]
            ensure(store,dict(assets=assets,timeframes=['daily'],daily_universe=dict(enabled=True)))
            before=get(store)
            with patch.object(activation,'fetch_tiingo',side_effect=ValueError('Tiingo API token is invalid')),self.assertRaisesRegex(ValueError,'token'):
                activation.activate(store)
            self.assertEqual(get(store),before)
            def fetch(asset,frame):
                if asset['symbol']=='USD/CNY':raise ValueError('Exact pair unavailable')
                return fixture()
            with patch.object(activation,'fetch_tiingo',side_effect=fetch),patch.object(activation.time,'sleep'):
                result=activation.activate(store)
            self.assertEqual(result['moved'],['EURUSD'])
            config=get(store)['config']
            self.assertEqual(config['assets'][1]['provider'],'twelvedata')
            self.assertEqual(config['daily_universe']['tiingo_verified_pairs'],['EUR/USD'])
            self.assertNotIn('1h',config['assets'][0]['timeframes'])

    def test_backtest_source_and_chart_keep_tiingo_history(self):
        from backtest import START, run_backtest
        from candle_views import detail
        from finding_feedback import finding_id
        bars=fixture();delta=START+14400000-bars[258].end
        bars=ProviderCandles([replace(c,start=c.start+delta,end=c.end+delta) for c in bars],['Tiingo UTC'])
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('tiingo_feed.fetch_tiingo',return_value=bars):
                report=run_backtest(store,dict(assets=[self.asset],timeframes=['4h']),load_rules())
            row=report['results'][0];signal=row['signals'][0]
            self.assertEqual(row['source']['provider'],'Tiingo')
            self.assertEqual(signal['symbol'],'tiingo:Tiingo-UTC:EUR/USD')
            self.assertTrue(detail(store,Path(folder),'backtest',finding_id('backtest',signal))['candles'])
            self.assertEqual(store.rows(),[])
