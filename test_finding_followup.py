import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from finding_followup import calculate, run
from finding_feedback import finding_id
from platform_app import Store, load_rules
from scanner import Candle
from dashboard_api import read
from test_platform import example

START = 1577836800000
MINUTE = 60000


def bars():
    return [Candle(START+i*MINUTE, START+(i+1)*MINUTE, op, high, low, close)
            for i,(op,high,low,close) in enumerate([(100,120,100,110),(100,105,90,95),(95,104,92,101),(101,130,80,85),(85,110,84,101)])]


def signal():
    return example() | dict(direction='bullish',price2=100,pivot2=START,symbol='TEST',timeframe='1h')


class FollowupTests(unittest.TestCase):
    def test_recovery_window_and_entire_history(self):
        short=calculate(signal(),bars())
        self.assertEqual((short['drawdown_pct'],short['gain_pct'],short['candles']),(10,5,2))
        self.assertEqual(short['drawdown_elapsed_ms'],2*MINUTE)
        self.assertEqual(short['gain_elapsed_ms'],2*MINUTE)
        self.assertEqual(short['recovery_elapsed_ms'],3*MINUTE)
        self.assertEqual(short['rating'],2)
        full=calculate(signal(),bars(),'all')
        self.assertEqual((full['drawdown_pct'],full['gain_pct'],full['rating']),(20,30,3))
        self.assertEqual(full['recovery_elapsed_ms'],5*MINUTE)

    def test_bearish_symmetry(self):
        mirrored=[replace(c,open=200-c.open,high=200-c.low,low=200-c.high,close=200-c.close) for c in bars()]
        for mode in ('recovery','all'):
            bullish=calculate(signal(),bars(),mode)
            bearish=calculate(signal()|dict(direction='bearish'),mirrored,mode)
            for key in ('drawdown_pct','gain_pct','rating','gain_elapsed_ms','drawdown_elapsed_ms','recovery_elapsed_ms'):
                self.assertEqual(bullish[key],bearish[key])

    def test_missing_data_no_move_and_same_bar_ambiguity(self):
        self.assertIsNone(calculate(signal(),bars()[1:])['rating'])
        self.assertEqual(calculate(signal(),bars()[:1])['status'],'unavailable')
        one=calculate(signal(),[bars()[0],replace(bars()[1],close=100)])
        self.assertEqual(one['status'],'ongoing')
        self.assertIsNone(one['recovery_at'])
        flat=calculate(signal(),[bars()[0],replace(bars()[1],open=100,high=100,low=100,close=100)])
        self.assertEqual((flat['rating'],flat['drawdown_pct'],flat['gain_pct']),(1,0,0))
        favorable=calculate(signal(),[bars()[0],replace(bars()[1],low=100,close=105)])
        self.assertEqual((favorable['rating'],favorable['status']),(5,'no drawdown'))

    def test_live_on_demand_persistence_and_recalculation(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'signals.sqlite3');s=signal();store.insert(s);key=finding_id('live',s)
            with store.connect() as db:
                                db.executemany('INSERT INTO candle_cache VALUES(?,?,?,?,?,?,?,?,?)',[(s['symbol'],'TEST','1h',c.start,c.end,c.open,c.high,c.low,c.close) for c in bars()[:2]])
            self.assertIsNone(read(store,'/api/findings')['findings'][0]['followup'])
            result=run(store,Path(folder),'live',key)['followup']
            self.assertEqual(result['status'],'ongoing')
            self.assertEqual(read(Store(store.path),'/api/findings')['findings'][0]['followup'],result)
            with store.connect() as db:
                c=bars()[2];db.execute('INSERT INTO candle_cache VALUES(?,?,?,?,?,?,?,?,?)',(s['symbol'],'TEST','1h',c.start,c.end,c.open,c.high,c.low,c.close))
            self.assertEqual(run(store,Path(folder),'live',key)['followup']['status'],'recovered')
            self.assertEqual(finding_id('live',s),key)

    def test_backtest_precomputes_without_changing_evidence(self):
        from backtest import replay
        s=signal()|dict(confirmed_at=START+2*MINUTE);key=finding_id('backtest',s)
        with patch('backtest.detect',return_value=[s]):
            result=replay(bars(),load_rules(),'TEST','1h')
        self.assertEqual(result['signals'],[s])
        self.assertEqual(result['followups'][key]['recovery']['rating'],2)
