import unittest
from divergence_chasing import create, step, percentage, FRAMES


def signal(frame='4h', at=1000000, direction='bullish', price=100):
    return dict(symbol='TEST', timeframe=frame, direction=direction, price2=price,
                pivot1=at-200000, pivot2=at-100000, confirmed_at=at)


class ChaseTests(unittest.TestCase):
    def test_entry_after_chase_uses_existing_followup_without_requiring_third_divergence(self):
        s=create('a',signal(),1000000)
        s,_=step(s,1300000,price=99,price_at=1300000,signals=[signal('5m',1300000)])
        s,_=step(s,1400000,enter=True)
        self.assertEqual(s['position']['additional_divergence']['timeframe'],'5m')
        s,_=step(s,1440000,price=100,price_at=1440000)
        s,events=step(s,1500000,price=101,price_at=1500000)
        self.assertEqual(s['position']['status'],'monitoring')
        self.assertEqual(s['position']['trail_pct'],.5)
        self.assertEqual(events[0]['kind'],'position_trail')

    def test_one_hour_root_is_independent_and_monitoring_uses_original_timeframe(self):
        one=create('a',signal('1h'),1000000);four=create('a',signal('4h'),1000000)
        self.assertNotEqual(one['id'],four['id'])
        self.assertEqual(one['next_due'],1300000)
        one,events=step(one,1300000,price=99,price_at=1300000,signals=[signal('4h',1300000)])
        self.assertEqual(events,[])
        one,_=step(one,1300001,enter=True)
        self.assertEqual(one['position']['stop_pct'],.5)
        self.assertEqual(one['next_due'],1300001)
        one,_=step(one,1360000,price=100,price_at=1360000)
        self.assertEqual(one['next_due'],1420000)

    def test_inclusive_symmetric_stop_and_stale_data(self):
        for direction, price in [('bullish',100.5),('bearish',99.5)]:
            s=create('a',signal(direction=direction),1000000)
            stale, events=step(s,1300000,price=price,price_at=1060000)
            self.assertEqual(stale['status'],'chasing');self.assertEqual(events,[])
            done, events=step(s,1060000,price=price,price_at=1060000)
            self.assertEqual(done['status'],'stopped_favorable')
            self.assertEqual(events[0]['kind'],'favorable_stop')
            self.assertEqual(step(done,1120000,enter=True)[0]['status'],'stopped_favorable')

    def test_first_divergence_final_click_and_user_independence(self):
        s=create('a',signal(),1000000)
        other=create('b',signal(),1000000)
        now=1060000
        s,events=step(s,now,price=99,price_at=now,signals=[signal('15m',now)])
        self.assertEqual(s['stage'],'15m')
        self.assertEqual(s['status'],'complete_first')
        s,events=step(s,1400000,enter=True)
        self.assertEqual(s['status'],'opposite');self.assertEqual(other['status'],'chasing')
        duplicate,_=step(s,1400001,enter=True)
        self.assertEqual(duplicate['entry_at'],1400000)
        s,events=step(s,1700000,signals=[signal(at=1690000,direction='bearish')])
        self.assertEqual(s['status'],'opposite_found')
        self.assertEqual(step(s,2000000)[1],[])

    def test_smallest_timeframe_wins_within_check_and_no_later_chase(self):
        s=create('a',signal(),1000000)
        now=1060000
        s,events=step(s,now,price=99,price_at=now,
                      signals=[signal('1h',now),signal('1m',now),signal('5m',now)])
        self.assertEqual(s['stage'],'5m')
        self.assertEqual(len(events),1)
        self.assertEqual(step(s,1120000,price=98,price_at=1120000,signals=[signal('1m',1120000)])[1],[])

    def test_one_minute_signal_is_ignored_but_minute_price_stop_still_applies(self):
        s=create('a',signal(),1000000)
        s,events=step(s,1060000,price=99,price_at=1060000,signals=[signal('1m',1060000)])
        self.assertEqual(s['status'],'chasing')
        self.assertEqual(events,[])
        s,events=step(s,1120000,price=100.5,price_at=1120000,signals=[signal('1m',1120000)])
        self.assertEqual(s['status'],'stopped_favorable')

    def test_no_future_or_preexisting_child_and_adverse_gate(self):
        s=create('a',signal(),1000000)
        for price, at in [(100.1,1060000),(99,1000000),(99,1120001)]:
            result,events=step(s,1120000,price=price,price_at=1120000,signals=[signal('1h',at)])
            self.assertEqual(result['stage'],'4h')

    def test_recovery_keeps_chasing_and_custom_setting(self):
        s=create('a',signal(),1000000,stop_pct=1)
        s,_=step(s,1060000,price=99,price_at=1060000)
        s,events=step(s,1120000,price=100.5,price_at=1120000)
        self.assertEqual(s['status'],'chasing');self.assertEqual(events[0]['kind'],'return_to_close')
        s,events=step(s,1180000,price=101,price_at=1180000)
        self.assertEqual(s['status'],'stopped_favorable')
        for bad in (True,0,-1,100,float('nan'),float('inf'),'x'):
            with self.assertRaises(ValueError):percentage(bad)


if __name__=='__main__':unittest.main()
