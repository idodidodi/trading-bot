import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from platform_app import Store,load_rules
from test_momentum import trend
import momentum
from stock_policy import scan_day,next_scan
from weekly_stock_screen import resolve,POLICY
from paper_trading import plan
from test_paper_trading import signal,ASSET,ACCOUNT,QUOTE,NOW


def instant(day):return int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Jerusalem')).timestamp()*1000)

class StockPolicyTests(unittest.TestCase):
    def test_schedule_uses_israel_weekdays(self):
        for day in ['2026-10-10','2026-10-11']:
            now=instant(day+'T08:15:00');self.assertFalse(scan_day(now))
            self.assertEqual(next_scan(now),instant('2026-10-12T08:00:00'))
        self.assertTrue(scan_day(instant('2026-10-12T08:15:00')))
        self.assertTrue(scan_day(instant('2026-10-09T08:15:00')))
    def test_weekend_momentum_and_rsi_screens_never_request_stock_history(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite3');now=instant('2026-10-11T09:00:00')
            with patch('alpaca_feed.fetch_alpaca_multi') as fetch,patch('weekly_stock_screen.universe') as universe:
                r=momentum.scan(store,dict(assets=[]),now=now,force=True)
                momentum.scan(store,dict(assets=[]),now=now+3600000)
                _,weekly=resolve(store,dict(assets=[],weekly_stock_screen=POLICY),load_rules(),now)
            fetch.assert_not_called();universe.assert_not_called()
            self.assertEqual(r['results'][0]['status'],'weekday only');self.assertEqual(weekly['status'],'weekday only')
    def test_below_five_excluded_in_stock_replay_but_crypto_unaffected(self):
        from scanner import Candle
        bars=[Candle(b.start,b.end,b.open/30,b.high/30,b.low/30,b.close/30) for b in trend()]
        self.assertIsNone(momentum.candidate(bars,'CHEAP','stock'))
        self.assertIsNotNone(momentum.candidate(bars,'CHEAP/USD','crypto'))
    def test_weekend_rsi_stock_assets_never_fetch(self):
        from scanner import scan_once,prepare_store
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite3');prepare_store(store)
            config=dict(timeframes=['daily'],assets=[dict(id='STOCK-X',symbol='X',market='stock',provider='alpaca')])
            with patch('alpaca_feed.fetch_alpaca') as fetch:
                rows=scan_once(store,config,load_rules(),instant('2026-10-11T09:00:00'))
            fetch.assert_not_called();self.assertEqual(rows[0]['status'],'weekday only')
    def test_execution_rejects_stock_price_under_five(self):
        s=signal()|dict(entry_min=4,entry_max=4.9,stop=3.5,target=6)
        with self.assertRaisesRegex(ValueError,'below .5'):
            plan(s,ACCOUNT,ASSET,QUOTE|dict(bp=4.5,ap=4.51),NOW)

if __name__=='__main__':unittest.main()
