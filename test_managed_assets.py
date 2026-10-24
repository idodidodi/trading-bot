from test_postgres_support import TestStore as Store, PostgresTestCase
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from platform_app import Store,load_rules
from managed_assets import ensure,change,get
from scanner import prepare_store,scan_once
from candle_views import detail
from finding_feedback import finding_id
from test_scanner import fixture
from test_platform import example


class AssetsTests(PostgresTestCase):
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
    def test_promoted_stock_market_persists_and_feed_settings_remain_valid(self):
        state=change(self.store,dict(action='bulk',tickers='STOCK-AAPL',revision=1))
        asset=dict(id='STOCK-AAPL',provider='alpaca',symbol='AAPL',exchange='Alpaca-sip-split',market='stock',feed_confirmed=True,enabled=True,timeframes=['weekly','daily'])
        state=change(self.store,dict(action='save',revision=state['revision'],asset=asset))
        saved=next(a for a in get(self.store)['config']['assets'] if a['id']=='STOCK-AAPL')
        self.assertEqual(saved['market'],'stock')
        from managed_assets import for_timeframe
        self.assertEqual(for_timeframe(saved,'daily')['provider'],'alpaca')
        state=change(self.store,dict(action='save',revision=state['revision'],asset=saved|dict(enabled=False,timeframes=['1h'])))
        self.assertFalse(state['config']['assets'][-1]['enabled'])
        self.assertEqual(state['config']['assets'][-1]['timeframes'],['1h'])
        with self.assertRaisesRegex(ValueError,'Invalid asset market'):
            change(self.store,dict(action='save',revision=state['revision'],asset=saved|dict(market='unknown')))


    def test_strategy_selection_gates_scanners_and_invalidates_schedule(self):
        from test_pin_bars import bars
        candles=bars()[-25:] # Enough for Pin-Bars, fewer than RSI requires.
        asset=get(self.store)['config']['assets'][0]
        asset.update(timeframes=['4h'],scan_strategies=['pin-bars'])
        change(self.store,dict(action='save',revision=1,asset=asset))
        with patch('scanner.read_csv',return_value=candles), patch('scanner.detect') as rsi:
            coverage=scan_once(self.store,self.config,load_rules(),candles[-1].end)
            rsi.assert_not_called()
        self.assertEqual(coverage[0]['status'],'baseline set')
        self.assertEqual(coverage[0]['rsi_status'],'disabled')
        asset['scan_strategies']=['rsi']
        change(self.store,dict(action='save',revision=2,asset=asset))
        with patch('scanner.read_csv',return_value=fixture()), patch('pin_bars.scan') as pins, patch('scanner.detect',return_value=[]) as rsi:
            coverage=scan_once(self.store,self.config,load_rules(),candles[-1].end)
            pins.assert_not_called()
            self.assertEqual(rsi.call_count,2)
        self.assertEqual(coverage[0]['pin_bars']['status'],'disabled')
        self.assertNotIn('check_state',coverage[0])
        asset['scan_strategies']=['rsi','pin-bars']
        change(self.store,dict(action='save',revision=3,asset=asset))
        with patch('scanner.read_csv',return_value=fixture()), patch('pin_bars.scan',return_value=dict(status='scanned',new_signals=0)) as pins, patch('scanner.detect',return_value=[]) as rsi:
            scan_once(self.store,self.config,load_rules(),candles[-1].end)
            pins.assert_called_once()
            self.assertEqual(rsi.call_count,2)

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
