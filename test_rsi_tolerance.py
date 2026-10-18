"""RSI tolerance boundaries in detection and delivery validation."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from platform_app import load_rules, validate_signal
from scanner import detect
from test_scanner import fixture


class RsiToleranceTests(unittest.TestCase):
    def test_both_directions_boundaries_and_confirmation(self):
        for direction in ('bullish', 'bearish'):
            bars = fixture()
            if direction == 'bearish':
                bars = [replace(c, open=200-c.open, close=200-c.close,
                                high=200-c.low, low=200-c.high) for c in bars]
            for change, expected in ((-1.001, False), (-1, True), (-.39, True), (0, True), (2, True)):
                rsi = [50.] * len(bars)
                rsi[250] = 50
                rsi[256] = 50 + (change if direction == 'bullish' else -change)
                bands = (rsi, [100.] * len(bars), [100.] * len(bars))
                rules = load_rules()
                for provisional in (False, True):
                    with self.subTest(direction=direction, change=change, provisional=provisional):
                        with patch('scanner.indicators', return_value=bands):
                            found = [s for s in detect(bars, rules, 'TEST', '4h', provisional=provisional)
                                     if s['pivot1'] == bars[250].start and s['pivot2'] == bars[256].start]
                        self.assertEqual(bool(found), expected)
                        if found:
                            validate_signal(found[0], rules)
                            self.assertEqual(found[0]['confirmed_at'], bars[256 if provisional else 257].end)
                            invalid = found[0] | {'rsi2': 48.999 if direction == 'bullish' else 51.001}
                            with self.assertRaises(ValueError):
                                validate_signal(invalid, rules)


if __name__ == '__main__':
    unittest.main()
