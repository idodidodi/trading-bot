import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_app import Store,load_rules
from managed_assets import ensure,change,get
from scanner import prepare_store,scan_once
from cloud_sync import snapshot,batch,apply,initialize
from candle_views import detail
from finding_feedback import finding_id
from test_scanner import fixture
from test_platform import example


class AssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'db.sqlite3')
        self.config={'assets':[{'id':'EURUSD','provider':'csv','path':str(self.root/'missing.csv')}],'timeframes':['monthly','weekly','daily','4h']}
        ensure(self.store,self.config);prepare_store(self.store)
    def tearDown(self):self.tmp.cleanup()
    def test_bulk_dedup_validation_and_independent_frames(self):
        result=change(self.store,{'action':'bulk','tickers':' nvda, NVDA, , BTCUSD, bad space','revision':1})
        self.assertEqual(len(result['config']['assets']),3)
        asset=result['config']['assets'][1];asset.update(enabled=True,timeframes=['daily'])
        result=change(self.store,{'action':'save','asset':asset,'revision':2})
        self.assertEqual(result['config']['assets'][0]['timeframes'],self.config['timeframes'])
        self.assertEqual(result['config']['assets'][1]['timeframes'],['daily'])
        with self.assertRaises(LookupError):change(self.store,{'action':'bulk','tickers':'MSFT','revision':1})
        self.assertFalse(result['config']['assets'][2]['enabled'])
    def test_cycle_uses_asset_frames_and_applies_revision(self):
        asset=get(self.store)['config']['assets'][0];asset['timeframes']=['4h']
        change(self.store,{'action':'save','asset':asset,'revision':1})
        with patch('scanner.read_csv',return_value=fixture()):
            coverage=scan_once(self.store,self.config,load_rules(),fixture()[-1].end)
        self.assertEqual([r['timeframe'] for r in coverage],['4h'])
        self.assertEqual(get(self.store)['applied_revision'],2)
        self.assertEqual(self.store.rows(),[])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM candle_cache').fetchone()[0],260)
    def test_live_confirmation_uses_candle_end_and_pivot_links(self):
        signal=example();signal.pop('secret');bars=fixture()
        signal.update(symbol='csv:configured:EURUSD',timeframe='4h',pivot1=bars[250].start,pivot2=bars[256].start,confirmed_at=bars[258].end)
        self.store.insert(signal)
        from managed_assets import cache_candles
        cache_candles(self.store,self.config['assets'][0],'4h',bars)
        data=detail(self.store,self.root,'live',finding_id('live',signal),'confirmation')
        self.assertTrue(any(c['end']==signal['confirmed_at'] for c in data['candles']))
        self.assertIsNone(data['tradingview_url'])
    def test_cloud_ack_crash_retry_and_remote_configuration(self):
        with patch('cloud_sync.ROOT',self.root):snapshot(self.store)
        outgoing=batch(self.store);self.assertLessEqual(len(outgoing['records']),100)
        self.assertEqual(batch(self.store),outgoing) # Unacknowledged batch survives a retry.
        cloud=copy.deepcopy(get(self.store));cloud['revision']=2;cloud['config']['assets'][0]['timeframes']=['weekly']
        apply(self.store,{'ack':[r['event_id'] for r in outgoing['records']],'config':cloud,'feedback':[]})
        self.assertEqual(get(self.store)['config']['assets'][0]['timeframes'],['weekly'])
        with patch('cloud_sync.ROOT',self.root):snapshot(self.store)
        self.assertFalse(any(r['kind']=='config' for r in batch(self.store)['records']))
    def test_synced_backtest_report_identifies_its_findings(self):
        signal={'symbol':'BTCUSD','timeframe':'4h','direction':'bearish','pivot1':1000,'pivot2':2000,
                'confirmed_at':3000,'price1':10,'price2':11,'rsi1':80,'rsi2':70,'band1':10,'band2':11,
                'band_slope_pct':0.1,'rules':{'price_source':'close'}}
        report={'year':2026,'start_at':1,'end_at':5000,'rules':{'price_source':'close'},'sources':['alpaca'],
                'results':[{'asset':'BTCUSD','timeframe':'4h','status':'replayed','signals':[signal]}]}
        with self.store.connect() as db:
            db.execute('CREATE TABLE backtest_runs(id INTEGER PRIMARY KEY,report TEXT)')
            db.execute('INSERT INTO backtest_runs(report) VALUES(?)',(json.dumps(report),))
            second_signal=signal|{'pivot1':4000,'pivot2':5000,'confirmed_at':6000}
            second_report=report|{'generated_at':2,'results':[{'asset':'BTCUSD','timeframe':'4h','status':'replayed','signals':[second_signal]}]}
            db.execute('INSERT INTO backtest_runs(report) VALUES(?)',(json.dumps(second_report),))
        with patch('cloud_sync.ROOT',self.root):snapshot(self.store)
        records=batch(self.store)['records']
        runs=[row['payload'] for row in records if row['kind']=='backtest_run']
        self.assertEqual(len(runs),2)
        by_generation={run['generated_at']:run for run in runs}
        self.assertEqual(by_generation[5]['year'],2026)
        self.assertEqual(by_generation[5]['summary'][0]['signals'],1)
        self.assertEqual(by_generation[5]['finding_ids'],[finding_id('backtest',signal)])
        self.assertEqual(by_generation[2]['finding_ids'],[finding_id('backtest',second_signal)])
    def test_remote_configuration_cannot_replace_pending_local_edit(self):
        with patch('cloud_sync.ROOT',self.root):snapshot(self.store)
        cloud=copy.deepcopy(get(self.store));cloud['revision']=2;cloud['config']['assets'][0]['timeframes']=['weekly']
        apply(self.store,{'ack':[],'config':cloud,'feedback':[],'conflicts':['config']})
        self.assertEqual(get(self.store)['revision'],1)
        accepted=change(self.store,{'action':'accept_online','revision':1})
        self.assertEqual(accepted['config']['assets'][0]['timeframes'],['weekly'])

    def test_edit_during_inflight_upload_is_not_overwritten(self):
        with patch('cloud_sync.ROOT',self.root):snapshot(self.store)
        outgoing=batch(self.store)
        remote=copy.deepcopy(get(self.store))
        asset=get(self.store)['config']['assets'][0]
        asset['timeframes']=['4h']
        change(self.store,{'action':'save','asset':asset,'revision':1})
        apply(self.store,{'ack':[r['event_id'] for r in outgoing['records']],'config':remote,'feedback':[]})
        self.assertEqual(get(self.store)['config']['assets'][0]['timeframes'],['4h'])
