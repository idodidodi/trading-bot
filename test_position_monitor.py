import unittest
from decimal import Decimal
from position_monitor import start,update


def sig(at,frame='5m',direction='bullish'):
    return dict(symbol='TEST',direction=direction,timeframe=frame,confirmed_at=at)


def observe(p,price,at=120000,direction='bullish',signals=()):
    return update(p,at,price,at,direction,signals,'TEST')


class PositionTests(unittest.TestCase):
    def test_reference_requires_fresh_post_click_price(self):
        p=start('4h',61000)
        self.assertEqual(update(p,62000,100,60000,'bullish')[0],p)
        self.assertEqual(update(p,400000,100,120000,'bullish')[0],p)
        p,events=observe(p,100)
        self.assertEqual(p['entry_price'],100)
        self.assertEqual(events[0]['kind'],'position_reference')
        self.assertEqual(observe(p,90)[1],[])

    def test_symmetric_fixed_exits_for_both_timeframes(self):
        for frame,width in [('1h',.5),('4h',1)]:
            for direction in ('bullish','bearish'):
                d=1 if direction=='bullish' else -1
                for side,reason in [(1,'profit_target'),(-1,'loss_stop')]:
                    p,_=observe(start(frame,60000),100,direction=direction)
                    p,e=observe(p,Decimal(100)*(1+d*side*Decimal(str(width))/100),180000,direction)
                    self.assertEqual(p['status'],'exit_alerted')
                    self.assertEqual(e[0]['reason'],reason)
                    p,e=observe(p,90,240000,direction)
                    self.assertEqual(e,[])
                    self.assertEqual(p['last_price'],90)

    def test_extension_arms_half_percent_then_widens_and_latches_on_four_hour(self):
        p,_=observe(start('4h',60000),100)
        p,e=observe(p,100.4,180000,signals=[sig(180000)])
        self.assertEqual(e[0]['kind'],'position_extended')
        p,e=observe(p,101,240000)
        self.assertEqual(p['trail_pct'],.5)
        self.assertAlmostEqual(p['trail_stop'],100.495)
        p,e=observe(p,102,300000)
        self.assertEqual(p['trail_pct'],1)
        self.assertAlmostEqual(p['trail_stop'],100.98)
        p,e=observe(p,101.5,360000)
        self.assertEqual(p['trail_pct'],1)
        self.assertEqual(p['status'],'monitoring')
        p,e=observe(p,100.98,420000)
        self.assertEqual(e[0]['reason'],'trailing_stop')

    def test_one_hour_stays_half_percent_and_short_trail_is_mirrored(self):
        p,_=observe(start('1h',60000),100,direction='bearish')
        p,_=observe(p,99.8,180000,'bearish',[sig(180000,direction='bearish')])
        p,_=observe(p,99,240000,'bearish')
        self.assertAlmostEqual(p['trail_stop'],99.495)
        p,_=observe(p,98,300000,'bearish')
        self.assertEqual(p['trail_pct'],.5)
        p,e=observe(p,98.49,360000,'bearish')
        self.assertEqual(e[0]['reason'],'trailing_stop')

    def test_keep_level_option_does_not_move_a_protective_stop_backward(self):
        p,_=observe(start('4h',60000,widen_mode='keep_level'),100)
        p,_=observe(p,100.4,180000,signals=[sig(180000)])
        p,_=observe(p,101.9,240000)
        old=p['trail_stop']
        p,_=observe(p,102,300000)
        self.assertEqual(p['trail_pct'],1)
        self.assertGreaterEqual(p['trail_stop'],old)

    def test_existing_exit_wins_over_new_divergence_same_observation(self):
        p,_=observe(start('1h',60000),100)
        p,e=observe(p,100.5,180000,signals=[sig(180000)])
        self.assertEqual(e[0]['reason'],'profit_target')
        self.assertIsNone(p['additional_divergence'])

    def test_one_minute_wrong_direction_old_and_future_divergences_do_not_extend(self):
        p,_=observe(start('1h',60000),100)
        signals=[sig(180000,'1m'),sig(180000,direction='bearish'),sig(60000),sig(240000)]
        p,e=observe(p,100.1,180000,signals=signals)
        self.assertIsNone(p['additional_divergence'])
        self.assertEqual(e,[])
