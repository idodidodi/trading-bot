import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from platform_app import Store, load_rules
from scanner import Candle
from weekly_stock_screen import POLICY, local_day, resolve, week_start


def instant(local):
    return int(datetime.fromisoformat(local).replace(tzinfo=ZoneInfo('Asia/Jerusalem')).timestamp()*1000)


class WeeklyStockScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'screen.sqlite3')
        self.config={'assets':[],'weekly_stock_screen':dict(POLICY)}
        start=int(datetime(2025,1,6,tzinfo=timezone.utc).timestamp()*1000)
        self.history={f'T{i:03d}':[Candle(start+j*604800000,start+(j+1)*604800000,100,101,99,100) for j in range(24)] for i in range(30)}

    def test_selects_distinct_band_groups_and_rotates_daily(self):
        with patch('weekly_stock_screen.universe',return_value=(list(self.history),[])), \
             patch('weekly_stock_screen._market_snapshot',return_value=self.history):
            first,one=resolve(self.store,self.config,load_rules(),instant('2026-10-05T09:00:00'))
            self.assertEqual(one['status'],'selected')
            self.assertEqual(len(one['selected']['upper']),10)
            self.assertEqual(len(one['selected']['lower']),10)
            self.assertFalse(set(one['symbols'])-set(self.history))
            self.assertEqual(len(one['symbols']),20)
            self.assertEqual(len([a for a in first['assets'] if a.get('weekly_screen_selected')]),20)
            _,two=resolve(self.store,self.config,load_rules(),instant('2026-10-06T09:00:00'))
            self.assertEqual(two['status'],'partial')
            self.assertFalse(set(two['symbols']) & set(one['symbols']))

    def test_five_weekdays_have_100_distinct_stocks(self):
        symbols=[f'Q{i:03d}' for i in range(120)]
        histories={symbol:self.history['T000'] for symbol in symbols}
        with patch('weekly_stock_screen.universe',return_value=(symbols,[])), \
             patch('weekly_stock_screen._market_snapshot',return_value=histories):
            selected=[]
            for offset,day in enumerate(['2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-09']):
                _,state=resolve(self.store,self.config,load_rules(),instant(day+'T09:00:00'))
                self.assertEqual(state['status'],'selected')
                selected.extend(state['symbols'])
        self.assertEqual(len(selected),100)
        self.assertEqual(len(set(selected)),100)

    def test_can_repeat_after_week_rollover(self):
        self.assertNotEqual(week_start(POLICY,'2026-10-09')[1],week_start(POLICY,'2026-10-12')[1])
        with patch('weekly_stock_screen.universe',return_value=(list(self.history),[])), \
             patch('weekly_stock_screen._market_snapshot',return_value=self.history):
            _,one=resolve(self.store,self.config,load_rules(),instant('2026-10-09T09:00:00'))
            _,next_week=resolve(self.store,self.config,load_rules(),instant('2026-10-12T09:00:00'))
        self.assertEqual(set(one['symbols']),set(next_week['symbols']))

    def test_stock_market_weekdays_hold_fridays_selection_through_weekend(self):
        self.assertEqual(local_day(POLICY,instant('2026-10-10T12:00:00'))[0],'2026-10-09')
        self.assertEqual(local_day(POLICY,instant('2026-10-11T12:00:00'))[0],'2026-10-09')


if __name__=='__main__': unittest.main()
