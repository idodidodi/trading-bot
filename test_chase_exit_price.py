import unittest
from unittest.mock import patch
from chasing_runtime import current_price, text_for
from divergence_chasing import create, step
from position_monitor import start, update
from scanner import Candle
from test_divergence_chasing import signal


class ExitTests(unittest.TestCase):
    def test_only_opposite_original_frame_after_entry_exits_both_directions(self):
        for frame in ('1h','4h'):
            for direction in ('bullish','bearish'):
                opposite='bearish' if direction=='bullish' else 'bullish'
                p,_=update(start(frame,1000000),1060000,100,1060000,direction,(), 'TEST')
                for candidate in (signal('5m',1120000,opposite),
                                  signal('4h' if frame=='1h' else '1h',1120000,opposite),
                                  signal(frame,1120000,direction),
                                  signal(frame,1000000,opposite),
                                  signal(frame,1180000,opposite),
                                  signal(frame,1120000,opposite)|dict(symbol='OTHER')):
                    result,events=update(p,1120000,100,1120000,direction,[candidate],'TEST')
                    self.assertNotEqual(result['status'],'exit_alerted')
                candidate=signal(frame,1120000,opposite)
                result,events=update(p,1120000,100,1120000,direction,[candidate],'TEST')
                self.assertEqual(result['exit_reason'],'opposite_divergence')
                self.assertEqual(events[0]['kind'],'exit_signal')
                self.assertEqual(update(result,1180000,100,1180000,direction,[candidate],'TEST')[1],[])

    def test_chase_frame_does_not_replace_original_and_one_exit_event(self):
        s=create('owner',signal('4h'),1000000)
        s,_=step(s,1300000,price=99,price_at=1300000,signals=[signal('5m',1300000)])
        s,_=step(s,1400000,enter=True)
        s,_=step(s,1460000,price=100,price_at=1460000)
        s,events=step(s,1520000,price=100,price_at=1520000,signals=[signal('4h',1520000,'bearish')])
        self.assertEqual([e['kind'] for e in events],['exit_signal'])
        self.assertEqual(s['position']['exit_reason'],'opposite_divergence')
        self.assertEqual(s['status'],'opposite_found')

    def test_opposite_retained_until_fresh_price_after_outage(self):
        s=create('owner',signal(),1000000)
        s,_=step(s,1400000,enter=True)
        s,events=step(s,1460000,signals=[signal('4h',1460000,'bearish')])
        self.assertEqual([e['kind'] for e in events],['opposite'])
        s,events=step(s,1520000,price=100,price_at=1520000)
        self.assertEqual(s['position']['exit_reason'],'opposite_divergence')
        self.assertNotIn('exit_signal',[e['kind'] for e in events])


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.s=create('owner',signal()|dict(symbol='kraken:Kraken:BTC/USD'),1000000)

    def test_delivery_price_is_separate_from_original_and_timestamped(self):
        for kind in ('started','chase','expired'):
            event=dict(kind=kind,signal=signal('5m'),current_price=dict(price=123.45,at=1200000))
            for automatic in (False,True):
                state=self.s|({'watch_policy':'crypto-descending-v1'} if automatic else {})
                text=text_for(state,event)
                self.assertIn('Original close: 100',text)
                self.assertIn('Current price: 123.45 (latest closed minute,',text)
                self.assertIn('UTC)',text)
        self.assertIn('Current price: unavailable',text_for(self.s,dict(kind='started',current_price=None)))

    def test_fresh_stale_future_invalid_and_slow_price_fetch(self):
        for end,close,expected in ((1200000,123,True),(1000000,123,False),(1260000,123,False),(1200000,0,False)):
            with patch('chasing_runtime.time.time',return_value=1200),patch('shared_kraken.fetch',return_value=[Candle(end-60000,end,close,close,close,close)]):
                self.assertEqual(current_price(self.s) is not None,expected)
        with patch('chasing_runtime.time.time',side_effect=[1200,1400]),patch('shared_kraken.fetch',return_value=[Candle(1140000,1200000,123,123,123,123)]):
            self.assertIsNone(current_price(self.s))
        with patch('shared_kraken.fetch',side_effect=OSError('offline')):
            self.assertIsNone(current_price(self.s))
