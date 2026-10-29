from test_postgres_support import TestStore as Store, PostgresTestCase
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bb_pin_bars
import pin_bars
from csv_backtests import run, save, view
from dashboard_api import read
from managed_assets import validate_asset
from platform_app import load_rules
from scanner import Candle, prepare_store, scan_once


def bars(mirror=False):
    start=1700000000000
    candles=[Candle(start+i*14400000,start+(i+1)*14400000,100,100,100,100) for i in range(70)]
    candles[-1]=replace(candles[-1],open=100,high=105,low=99.99,close=100.1)
    return [replace(b,open=200-b.open,high=200-b.low,low=200-b.high,close=200-b.close) for b in candles] if mirror else candles


def csv(candles):
    return 'time,open,high,low,close\n'+'\n'.join(f'{b.start},{b.open},{b.high},{b.low},{b.close}' for b in candles)


class BBPinBarsTests(PostgresTestCase):
    def test_shape_color_exact_touch_and_wrong_band(self):
        bullish=Candle(1000,2000,104,104.1,100,103)
        self.assertEqual(bb_pin_bars.direction(bullish,100,110,2),'bullish')
        self.assertIsNone(bb_pin_bars.direction(bullish,99.9,110,2))
        self.assertIsNone(bb_pin_bars.direction(bullish,90,104.1,2))
        bearish=Candle(1000,2000,103,107,102.9,104)
        self.assertEqual(bb_pin_bars.direction(bearish,90,107,2),'bearish')
        self.assertIsNone(bb_pin_bars.direction(bearish,90,107.1,2))
        self.assertIsNone(bb_pin_bars.direction(replace(bearish,high=106),90,106,2))

    def test_bands_warmup_and_no_future_data(self):
        bands=bb_pin_bars.bollinger([1,2,3],3,2)
        self.assertEqual(bands[:2],[(None,None),(None,None)])
        self.assertAlmostEqual(bands[2][0],2-2*(2/3)**.5)
        candles=bars();rules=bb_pin_bars.load_rules()
        signals=bb_pin_bars.detect(candles,rules,'test','TEST','4h')
        self.assertEqual(len(signals),1)
        self.assertEqual(signals[0]['direction'],'bearish')
        self.assertEqual(signals[0]['strategy'],'bb-pin-bars')
        self.assertNotIn('fast_ema',signals[0])
        self.assertEqual(pin_bars.detect(candles,pin_bars.load_rules(),'test','TEST','4h'),[])
        future=replace(candles[-1],start=candles[-1].end,end=candles[-1].end+14400000,high=200,close=190)
        self.assertEqual(bb_pin_bars.detect(candles+[future],rules,'test','TEST','4h')[0],signals[0])
        # The 20th candle is eligible; the BB strategy does not inherit EMA warm-up.
        short=candles[-20:]
        self.assertEqual(len(bb_pin_bars.detect(short,rules,'test','TEST','4h')),1)
        self.assertEqual(bb_pin_bars.detect(short[:-1],rules,'test','TEST','4h'),[])

    def test_independent_baselines_alerts_restart_and_catchup(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            candles=bars(True);now=candles[-1].end;asset=dict(id='TEST',provider='csv')
            for engine in (pin_bars,bb_pin_bars):
                engine.scan(store,asset,'4h',candles[:-1],engine.load_rules(),now)
                result=engine.scan(store,asset,'4h',candles,engine.load_rules(),now)
                self.assertEqual(result['new_signals'],1)
                self.assertEqual(engine.scan(Store(store.path),asset,'4h',candles,engine.load_rules(),now)['new_signals'],0)
            with store.connect() as db:
                alerts=db.execute('SELECT id,text FROM notification_queue').fetchall()
            self.assertEqual(len(alerts),2)
            self.assertTrue(any(r[0].startswith('bb-pin-bars:') and 'BB Pin-Bars' in r[1] for r in alerts))
            self.assertEqual(read(store,'/api/bb-pin-bars')['signals'][0]['strategy'],'bb-pin-bars')
            self.assertEqual(read(store,'/api/pin-bars')['signals'][0]['strategy'],'pin-bars')
            asset=asset|dict(exchange='other')
            bb_pin_bars.scan(store,asset,'4h',candles[:-1],bb_pin_bars.load_rules(),now)
            bb_pin_bars.scan(store,asset,'4h',candles,bb_pin_bars.load_rules(),now,catchup=True)
            with store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_queue').fetchone()[0],2)

    def test_backtest_reports_are_separate_and_never_alert(self):
        candles=bars();body=dict(csv=csv(candles),symbol='TEST',timeframe='4h',strategy='bb-pin-bars')
        report=run(body,candles[-1].end)
        self.assertEqual(report['signal_count'],1)
        self.assertEqual(report['warmup_candles'],19)
        self.assertEqual(run(body|dict(strategy='pin-bars'),candles[-1].end)['signal_count'],0)
        self.assertEqual(run(body,candles[-2].end)['signal_count'],0)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');save(store,body)
            self.assertIsNotNone(view(store,strategy='bb-pin-bars')['report'])
            self.assertIsNone(view(store,strategy='pin-bars')['report'])
            with store.connect() as db:self.assertIsNone(db.execute("SELECT 1 FROM notification_queue LIMIT 1").fetchone())

    def test_scanner_opt_in_and_strategy_validation(self):
        candles=bars();asset=dict(id='TEST',provider='csv',enabled=True,timeframes=['4h'],scan_strategies=['bb-pin-bars'])
        validate_asset(asset)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('scanner.read_csv',return_value=candles):
                row=scan_once(store,dict(timeframes=['4h'],assets=[asset]),load_rules(),candles[-2].end)[0]
                self.assertEqual(row['bb_pin_bars']['status'],'baseline set')
                self.assertEqual(row['pin_bars']['status'],'disabled')
                row=scan_once(store,dict(timeframes=['4h'],assets=[asset]),load_rules(),candles[-1].end)[0]
                self.assertEqual(row['bb_pin_bars']['new_signals'],1)
            with self.assertRaises(ValueError):read(store,'/api/bb-pin-bars?page=-1')


if __name__=='__main__':unittest.main()
