import unittest
from unittest.mock import patch
from scanner import Candle
from research.weekly_rsi_exits import retest,simulate,configurations,metrics,best_index,split_entries,load_paths

def entry(side='bullish',end=300000):
    return dict(entry=100,entry_time=60000,direction=side,lower_signal=dict(price2=100),
                outcomes={'5_sessions':dict(target_end=end)})
def bar(t,o,h,l,c):return Candle(t,t+60000,o,h,l,c)
C=dict(stop_pct=1,target_pct=2,reward_risk=2)

class ExitsTests(unittest.TestCase):
    def test_retest_requires_departure_and_later_bar(self):
        bars=[bar(60000,100,100.4,99.9,100.3)]
        self.assertFalse(retest(entry(),bars)['retested'])
        bars.append(bar(120000,100.3,100.4,100.05,100.1))
        r=retest(entry(),bars)
        self.assertTrue(r['retested']);self.assertEqual(r['retest_minute'],120000)
        self.assertEqual(r['departed_at'],120000)

    def test_short_retest_and_adverse_departure(self):
        bars=[bar(60000,100,100.1,99.5,99.6),bar(120000,99.6,100,99.5,99.9)]
        self.assertTrue(retest(entry('bearish'),bars)['retested'])
        self.assertFalse(retest(entry(),bars)['retested'])

    def test_gap_over_retest_zone_is_not_touch(self):
        bars=[bar(60000,100,101,100,100.5),bar(120000,99,99.5,98,99.2)]
        self.assertFalse(retest(entry(),bars)['retested'])

    def test_entry_candle_and_future_excluded(self):
        bars=[bar(0,100,120,80,100),bar(60000,100,100.2,99.8,100.1),bar(120000,100.1,150,50,100)]
        r=simulate(entry(end=120000),bars,C)
        self.assertEqual(r['reason'],'time_exit');self.assertAlmostEqual(r['return_pct'],.1)

    def test_both_hit_stop_first_and_sensitivity(self):
        bars=[bar(60000,100,103,98,101)]
        a=simulate(entry(),bars,C);b=simulate(entry(),bars,C,True)
        self.assertEqual(a['return_pct'],-1);self.assertEqual(b['return_pct'],2)
        self.assertTrue(a['ambiguous']);self.assertTrue(b['ambiguous'])

    def test_open_order_precedes_intraminute_range(self):
        r=simulate(entry(),[bar(60000,103,104,98,100)],C)
        self.assertEqual(r['reason'],'target');self.assertEqual(r['exit_price'],102)
        self.assertFalse(r['ambiguous'])

    def test_adverse_gap_and_short_math(self):
        r=simulate(entry(),[bar(60000,97,99,96,98)],C)
        self.assertEqual(r['return_pct'],-3);self.assertTrue(r['gap_stop'])
        r=simulate(entry('bearish'),[bar(60000,103,104,102,103)],C)
        self.assertEqual(r['return_pct'],-3);self.assertTrue(r['gap_stop'])
        r=simulate(entry('bearish'),[bar(60000,100,100.5,97,98)],C)
        self.assertEqual(r['return_pct'],2);self.assertEqual(r['pnl_usd'],20)

    def test_exact_stop_open_not_adverse_gap(self):
        r=simulate(entry(),[bar(60000,99,100,98,99)],C)
        self.assertFalse(r['gap_stop'])

    def test_no_stop_and_no_target_controls(self):
        bars=[bar(60000,100,103,95,101)]
        r=simulate(entry(end=120000),bars,dict(stop_pct=None,target_pct=None))
        self.assertEqual(r['reason'],'time_exit');self.assertEqual(r['return_pct'],1)
        self.assertIsNone(r['realized_r']);self.assertIsNone(r['stop_price'])
        r=simulate(entry(),bars,dict(stop_pct=None,target_pct=2))
        self.assertEqual(r['return_pct'],2);self.assertIsNone(metrics([r])['mean_r'])
        r=simulate(entry(),bars,dict(stop_pct=1,target_pct=None))
        self.assertEqual(r['return_pct'],-1)

    def test_retest_outside_horizon_excluded(self):
        bars=[bar(60000,100,100.5,100,100.4),bar(120000,100.3,100.3,100,100)]
        self.assertFalse(retest(entry(end=120000),bars)['retested'])

    def test_stale_terminal_price_not_filled(self):
        self.assertEqual(simulate(entry(end=600000),[bar(60000,100,100.2,99.8,100)],C)['status'],'incomplete')

    def test_purge_unobservable_training_outcomes(self):
        a=entry(end=120000);b=entry(end=240000);c=entry(end=360000);c['entry_time']=240000
        self.assertEqual(split_entries([a,b,c],180000),([0],[2],[1]))

    def test_selection_cannot_use_holdout_outcomes(self):
        configs=[dict(stop_pct=1,target_pct=2),dict(stop_pct=2,target_pct=4)]
        outcomes=[[dict(pnl_usd=10),dict(pnl_usd=-1000)],[dict(pnl_usd=9),dict(pnl_usd=1000)]]
        self.assertEqual(best_index(configs,outcomes,[0]),0)
        self.assertEqual(best_index(configs,outcomes,[0,1]),1)

    def test_grid_is_unique_and_bounded(self):
        configs=configurations();pairs={(c['stop_pct'],c['target_pct']) for c in configs}
        self.assertEqual(len(configs),len(pairs));self.assertGreater(len(configs),143)
        self.assertTrue(all(0<c['target_pct']<=20 for c in configs))
        self.assertIn((15,2.5),pairs)
        targets={t for s,t in pairs};stops={s for s,t in pairs}
        self.assertEqual(len(pairs),len(targets)*len(stops))

    def test_cache_only_loader_rejects_missing_input(self):
        with patch('alpaca_feed.request_page',side_effect=AssertionError('Must never request prices')):
            with self.assertRaises(FileNotFoundError):
                load_paths(dict(entries=[dict(week='2025-11-03',ticker='AAPL')],data_receipts=[]),'/private/tmp/no-such-rsi-cache')

if __name__=='__main__':unittest.main()
