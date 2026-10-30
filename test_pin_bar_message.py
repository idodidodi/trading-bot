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
                    'Confirmed: 30 Oct 2026 at 06:00 (Israel time)\n'
                    '--------------------\n'
                    'Provider: Kraken')
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
                self.assertNotIn('Signal only', payload['text'])
                self.assertIn('\n--------------------\nProvider: Kraken\n\nSource:', payload['text'])
                encoded = payload['text'].encode('utf-16-le')
                source_entities = [entity for entity in payload['entities']
                                   if encoded[entity['offset'] * 2:
                                              (entity['offset'] + entity['length']) * 2]
                                   .decode('utf-16-le').startswith(('Provider:', 'Source:'))]
                self.assertEqual([entity['type'] for entity in source_entities],
                                 ['italic', 'italic'])

    def test_provider_names(self):
        providers = [('kraken:Kraken:XRP/USD', 'Kraken'),
                     ('twelvedata:NASDAQ:AAPL', 'Twelve Data'),
                     ('oanda:OANDA-practice:EUR/USD', 'OANDA'),
                     ('tiingo:Tiingo-UTC:XAU/USD', 'Tiingo'),
                     ('alpaca:Alpaca-sip-split:AAPL', 'Alpaca'),
                     ('fmp:configured:SPX', 'FMP'),
                     ('yahoo:configured:GC=F', 'Yahoo'),
                     ('csv:configured:TEST', 'CSV'),
                     ('NewProvider:venue:TEST', 'NewProvider')]
        for source, provider in providers:
            with self.subTest(source=source):
                text = message(dict(direction='bullish', symbol='TEST', timeframe='4h',
                                    close=100, confirmed_at=1793332800000, source=source))
                self.assertEqual(text.splitlines()[-2:],
                                 ['--------------------', f'Provider: {provider}'])


if __name__ == '__main__':
    unittest.main()
