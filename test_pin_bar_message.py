"""Pin-Bar alert presentation without a database or live Telegram delivery."""
import io
import json
import unittest
from unittest.mock import patch

from pin_bars import message
from platform_app import send_telegram


class PinBarMessageTests(unittest.TestCase):
    def test_compact_alert_preserves_price_and_footer(self):
        for direction in ('bullish', 'bearish'):
            with self.subTest(direction=direction):
                signal = dict(direction=direction, symbol='XRPUSD', timeframe='4h',
                              close=1.27625123, confirmed_at=1793332800000,
                              source='kraken:Kraken:XRP/USD')
                expected = (
                    f'{direction.upper()} Pin-Bars\n'
                    'XRPUSD · 4h · Price: 1.27625123\n'
                    'Source: kraken:Kraken:XRP/USD\n'
                    'Confirmed: 30 Oct 2026 at 06:00 (Israel time)\n'
                    'Signal only · completed Pin-Bar candle')
                self.assertEqual(message(signal), expected)
                with patch('platform_app.urllib.request.urlopen',
                           return_value=io.StringIO('{"ok":true}')) as request:
                    send_telegram('test-token', '123', message(signal),
                                  source_identity=dict(device='linux-postgres',
                                                       environment='prod', sender_id='3eb68542'))
                payload = json.loads(request.call_args.args[0].data)
                lines = [line for line in payload['text'].splitlines() if line]
                self.assertEqual(lines, expected.splitlines() + [
                    'Source: linux-postgres · prod · 3eb68542'])
                self.assertNotIn('EMA', payload['text'])
                self.assertNotIn('wick', payload['text'])
                self.assertNotIn('Body:', payload['text'])


if __name__ == '__main__':
    unittest.main()
