import json
from pathlib import Path
import unittest
from scanner import Candle
from research.weekly_rsi_retest_entries import retest_entry,simulate_retest,funded_audit

RULES=json.loads(Path('research/hyro_one_step_2026_11_28.json').read_text())
def bar(t,o,h,l,c):return Candle(t,t+60000,o,h,l,c)
def entry(t=60000,end=300000):
    return dict(ticker='AAPL',week='2025-11-03',tf='15m',direction='bullish',entry=100,entry_time=t,
        parent_invalidated_at=None,lower_signal=dict(price2=100),outcomes={'5_sessions':dict(target_end=end)})

class RetestTests(unittest.TestCase):
    def test_entry_band_touch_price_not_signal_price(self):
        e=entry();bars=[bar(60000,100,100.5,100,100.4),bar(120000,100.3,100.4,100,100.2)]
        r,why=retest_entry(e,bars)
        self.assertEqual(why['reason'],'filled');self.assertAlmostEqual(r['entry'],100.1)
        self.assertEqual(r['entry_time'],120000);self.assertEqual(e['entry'],100)
        self.assertEqual(r['outcomes']['5_sessions']['target_end'],300000)

    def test_gap_below_band_waits_for_observed_reentry(self):
        e=entry();bars=[bar(60000,100,100.5,100,100.4),bar(120000,99,100,98.9,99.95)]
        r,_=retest_entry(e,bars);self.assertAlmostEqual(r['entry'],99.9)

    def test_invalidated_pending_setup_never_enters(self):
        e=entry();e['parent_invalidated_at']=120000
        bars=[bar(60000,100,100.5,100,100.4),bar(120000,100.3,100.4,100,100.2)]
        r,why=retest_entry(e,bars);self.assertIsNone(r)
        self.assertEqual(why['reason'],'weekly_invalidated_before_retest')

    def test_before_entry_high_is_not_certain_profit(self):
        e=entry(end=240000)
        bars=[bar(60000,100,100.5,100,100.4),bar(120000,100.3,105,100,100.08),bar(180000,100.08,100.09,98,99)]
        r,_=retest_entry(e,bars)
        result=simulate_retest(r,bars,dict(stop_pct=1,target_pct=2))
        optimistic=simulate_retest(r,bars,dict(stop_pct=1,target_pct=2),True)
        self.assertAlmostEqual(result['return_pct'],-1)
        self.assertAlmostEqual(optimistic['return_pct'],2)
        self.assertTrue(result['entry_minute_ambiguous'])

    def test_adverse_entry_bar_low_after_fill_triggers_stop(self):
        e=entry(end=240000)
        bars=[bar(60000,100,100.5,100,100.4),bar(120000,100.3,100.4,98,99),bar(180000,99,105,99,104)]
        r,_=retest_entry(e,bars);o=simulate_retest(r,bars,dict(stop_pct=1,target_pct=2))
        self.assertAlmostEqual(o['return_pct'],-1)

    def test_no_retest_stays_unfilled(self):
        r,why=retest_entry(entry(),[bar(60000,100,100.05,99.5,99.6)])
        self.assertIsNone(r);self.assertEqual(why['reason'],'no_retest')

    def test_inactivity_uses_close_too_but_stops_later_trades(self):
        day=86400000;e=entry(t=day,end=2*day);f=entry(t=40*day,end=41*day)
        outcomes=[dict(return_pct=1,exit_minute=2*day-60000,exit_price=101,reason='time_exit'),dict(return_pct=2,exit_minute=41*day-60000,exit_price=102,reason='time_exit')]
        candles=[bar(day,100,100,100,100),bar(2*day-60000,101,101,101,101),bar(40*day,100,100,100,100),bar(41*day-60000,102,102,102,102)]
        a=funded_audit([e,f],outcomes,{('2025-11-03','AAPL'):candles},RULES)
        self.assertEqual(a['failure']['reason'],'inactivity');self.assertEqual(a['entries_before_failure'],1)
        self.assertEqual(a['failure']['at'],32*day);self.assertEqual(a['pnl_before_failure'],50)

    def test_actual_stop_fill_used_for_close_equity(self):
        e=entry(t=60000,end=180000)
        outcome=dict(return_pct=-1,exit_minute=60000,exit_price=99,reason='stop')
        a=funded_audit([e],[outcome],{('2025-11-03','AAPL'):[bar(60000,100,100,1,1)]},RULES)
        self.assertEqual(a['minimum_marked_equity'],99950)
        self.assertTrue(a['possible_intraminute_loss_breach'])
        self.assertIsNone(a['failure'])

    def test_positive_daily_credit_cap_and_qualifying_day(self):
        e=entry(t=60000,end=180000)
        outcome=dict(return_pct=100,exit_minute=120000,exit_price=200,reason='time_exit')
        a=funded_audit([e],[outcome],{('2025-11-03','AAPL'):[bar(60000,100,100,100,100),bar(120000,200,200,200,200)]},RULES)
        self.assertEqual(a['pnl_before_failure'],5000)
        self.assertEqual(a['counted_target_profit'],4000)
        self.assertEqual(a['qualifying_days'],1)

    def test_utc_reset_uses_prior_equity_not_initial_balance(self):
        day=86400000;e=entry(t=day-60000,end=day+60000)
        outcome=dict(return_pct=-20,exit_minute=day,exit_price=80,reason='time_exit')
        bars=[bar(day-60000,100,100,90,90),bar(day,90,90,80,80)]
        a=funded_audit([e],[outcome],{('2025-11-03','AAPL'):bars},RULES)
        self.assertEqual(a['max_daily_close_loss'],500)
        self.assertEqual(a['minimum_marked_equity'],99000)

if __name__=='__main__':unittest.main()
