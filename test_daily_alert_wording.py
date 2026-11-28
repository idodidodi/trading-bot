"""Presentation regressions for persisted Alpaca daily divergence alerts."""
from copy import deepcopy
import unittest

from platform_app import message
from scanner import timestamp


class DailyAlertWordingTests(unittest.TestCase):
    def payload(self, day='2026-11-25', close='21:00:00', offset='-05:00'):
        return dict(symbol='alpaca:Alpaca-sip-split:AVGO', timeframe='daily',
                    direction='bullish', price1=357.16, price2=300.86,
                    rsi1=16.70, rsi2=23.77, band2=286.071,
                    rules={'rsi_period': 3}, signal_status='provisional',
                    pivot1=timestamp('2026-09-03T00:00:00-04:00'),
                    pivot1_closed_at=timestamp('2026-09-03T20:00:00Z'),
                    pivot2=timestamp(f'{day}T00:00:00{offset}'),
                    pivot2_closed_at=timestamp(f'{day}T{close}Z'),
                    detected_at=timestamp('2026-11-25T21:16:00Z'))

    def test_actual_avgo_dates_and_roles_without_mutating_signal(self):
        payload = self.payload()
        original = deepcopy(payload)
        text = message(payload)
        self.assertIn('Reference candle (comparison): 03 Sep 2026; regular session 16:30–23:00 (Israel time)', text)
        self.assertIn('Signal candle (trigger): 25 Nov 2026; regular session 16:30–23:00 (Israel time)', text)
        self.assertIn('Detected: 25 Nov 2026 at 23:16 (Israel time)', text)
        self.assertNotIn('07:00', text)
        self.assertEqual(payload, original)

    def test_shortened_session_uses_saved_close(self):
        text = message(self.payload('2026-11-27', '18:00:00'))
        self.assertIn('27 Nov 2026; regular session 16:30–20:00 (Israel time)', text)

    def test_us_israel_dst_mismatch(self):
        text = message(self.payload('2026-10-26', '20:00:00', '-04:00'))
        self.assertIn('26 Oct 2026; regular session 15:30–22:00 (Israel time)', text)

    def test_missing_close_is_explicit(self):
        payload = self.payload()
        del payload['pivot2_closed_at']
        self.assertIn('16:30–close unavailable in saved finding', message(payload))

    def test_other_providers_and_intraday_keep_their_boundaries(self):
        for change in ({'symbol': 'kraken:AVGO'}, {'timeframe': '4h'}):
            with self.subTest(change=change):
                text = message(self.payload() | change)
                self.assertIn('Signal candle 25.11.26 between 07:00 to 23:00', text)
                self.assertNotIn('regular session', text)


if __name__ == '__main__':
    unittest.main()
