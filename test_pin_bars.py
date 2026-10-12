from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pin_bars
from platform_app import Store, load_rules
from scanner import Candle, prepare_store, scan_once
from dashboard_api import read


def bars():
    start = 1700000000000
    result = [Candle(start+i*14400000,start+(i+1)*14400000,100+i,101+i,99+i,100+i) for i in range(70)]
    b = result[-1]
    result[-1] = replace(b,open=b.close-1,high=b.close+.1,low=b.close-20)
    return result


class PinBarTests(unittest.TestCase):
    def test_source_conditions_and_strict_boundaries(self):
        b = Candle(1000,2000,110,111.1,100,111)
        self.assertEqual(pin_bars.direction(b,108,105,2),'bullish')
        self.assertIsNone(pin_bars.direction(replace(b,low=108),108,105,2))
        self.assertIsNone(pin_bars.direction(b,105,105,2))
        self.assertIsNone(pin_bars.direction(b,115,111,2))
        self.assertIsNone(pin_bars.direction(replace(b,high=121),108,105,2))
        self.assertIsNone(pin_bars.direction(b,99,98,2))
        # Exact touch passes; a red body is allowed for the bullish pattern.
        self.assertEqual(pin_bars.direction(replace(b,open=111,close=110,low=108),108,105,1),'bullish')
        self.assertEqual(pin_bars.direction(replace(b,open=111),108,105,2),'bullish')
        bearish = Candle(1000,2000,91,100,89.9,90)
        self.assertEqual(pin_bars.direction(bearish,93,95,2),'bearish')
        self.assertIsNone(pin_bars.direction(replace(bearish,high=93),93,95,2))
        self.assertIsNone(pin_bars.direction(bearish,101,102,2))
        self.assertEqual(pin_bars.direction(replace(bearish,open=90),93,95,2),'bearish')

    def test_ema_seed_and_confirmation_without_future_candles(self):
        self.assertEqual(pin_bars.ema([10,20,30],3),[10,15,22.5])
        candles=bars();rules=pin_bars.load_rules()
        signal=pin_bars.detect(candles,rules,'csv:test:X','X','4h')[-1]
        self.assertEqual(signal['confirmed_at'],candles[-1].end)
        self.assertEqual(signal['candle_at'],candles[-1].start)
        self.assertEqual(signal['direction'],'bullish')
        mirrored=[replace(b,open=300-b.open,high=300-b.low,low=300-b.high,close=300-b.close) for b in candles]
        self.assertEqual(pin_bars.detect(mirrored,rules,'csv:test:X','X','4h')[-1]['direction'],'bearish')

    def test_scanner_baseline_open_candles_restart_and_dashboard(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'db';store=Store(path);prepare_store(store)
            config=dict(timeframes=['4h'],assets=[dict(id='X',provider='csv',path='unused')])
            candles=bars();rules=load_rules()
            with patch('scanner.read_csv',return_value=candles):
                coverage=scan_once(store,config,rules,candles[-2].end)
                self.assertEqual(coverage[0]['pin_bars']['status'],'baseline set')
                self.assertEqual(read(store,'/api/pin-bars')['signals'],[])
                coverage=scan_once(store,config,rules,candles[-1].end)
                self.assertEqual(coverage[0]['pin_bars']['new_signals'],1)
                scan_once(Store(path),config,rules,candles[-1].end+1)
            data=read(store,'/api/pin-bars')
            self.assertEqual(len(data['signals']),1)
            self.assertEqual(data['signals'][0]['strategy'],'pin-bars')
            self.assertEqual(store.rows(),[]) # No RSI signal or delivery is fabricated.
            self.assertEqual(data['coverage'][0]['asset'],'X')

    def test_feed_and_version_baselines_and_gap_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');candles=bars();rules=pin_bars.load_rules()
            asset=dict(id='X',provider='csv')
            pin_bars.scan(store,asset,'4h',candles[:-1],rules,candles[-2].end)
            changed=pin_bars.scan(store,asset|dict(exchange='another'),'4h',candles,rules,candles[-1].end)
            self.assertEqual(changed['status'],'baseline set')
            self.assertEqual(pin_bars.scan(store,asset,'4h',candles,rules|dict(version='changed'),candles[-1].end)['status'],'baseline set')
            future=[replace(b,start=b.start+200*14400000,end=b.end+200*14400000) for b in candles]
            self.assertEqual(pin_bars.scan(store,asset,'4h',future,rules,future[-1].end)['status'],'unavailable')
            self.assertEqual(pin_bars.view(store)['signals'],[])

    def test_independent_minimum_history_and_unavailable_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            config=dict(timeframes=['4h'],assets=[dict(id='X',provider='csv',path='unused')])
            candles=bars()[-25:]
            with patch('scanner.read_csv',return_value=candles):
                coverage=scan_once(store,config,load_rules(),candles[-1].end)
            self.assertEqual(coverage[0]['status'],'insufficient history')
            self.assertEqual(coverage[0]['pin_bars']['status'],'baseline set')
            with self.assertRaises(ValueError):read(store,'/api/pin-bars?page=-1')

    def test_cloud_snapshot_is_bounded_and_preserves_latest_signals(self):
        import json
        from cloud_sync import snapshot, batch, canonical
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            signal=pin_bars.detect(bars(),pin_bars.load_rules(),'csv:test:X','X','4h')[-1]
            with store.connect() as db:
                pin_bars.initialize(db)
                for i in range(600):
                    db.execute('INSERT INTO pin_bar_signals VALUES(?,?,?)',
                               (str(i),i,json.dumps(signal|dict(confirmed_at=i))))
            snapshot(store)
            outgoing=batch(store)
            payload=next(r['payload'] for r in outgoing['records'] if r['key']=='pin-bars-live')
            self.assertLessEqual(len(canonical(payload).encode()),180100)
            self.assertLessEqual(len(payload['signals']),500)
            self.assertEqual(payload['signals'][0]['confirmed_at'],599)
            self.assertEqual(payload['mirror_limit'],len(payload['signals']))
            self.assertEqual(len(pin_bars.view(store,page=11)['signals']),50)


if __name__=='__main__':unittest.main()
