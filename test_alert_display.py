"""Public alert names and source privacy, without a database or Telegram network."""
import io
import json
import unittest
from unittest.mock import patch

from alert_display import display_name, public_text
from platform_app import send_telegram


class AlertDisplayTests(unittest.TestCase):
    def test_map_names_and_futures_remain_distinct(self):
        expected = {'yahoo:SNP:^GSPC': 'S&P 500', 'SPX': 'S&P 500',
                    'yahoo:NYB:DX-Y.NYB': 'U.S. Dollar Index',
                    'DOLLAR_INDEX': 'U.S. Dollar Index', 'XAUUSD': 'Gold',
                    'tiingo:Tiingo:XAU/USD': 'Gold', 'USOIL': 'U.S. Oil (WTI)',
                    'tiingo:Tiingo:USOIL/USD': 'U.S. Oil (WTI)',
                    'CL=F': 'WTI Oil Futures', 'GC=F': 'Gold Futures',
                    'ES=F': 'S&P 500 E-mini Futures', 'AAPL': 'AAPL'}
        for symbol, name in expected.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(display_name(symbol), name)

    def test_real_rsi_header_and_old_source_footer(self):
        raw = ('BULLISH RSI divergence · candle closed\nyahoo:SNP:^GSPC · 4h\n'
               'Price: 7708.5 → 7579.2\nRSI(3): 11.43 → 11.45\n'
               'Detected: 30 Oct 2026 at 19:47 (Israel time)\n\n'
               'Source: linux-postgres · prod · 3eb68542')
        with patch('platform_app.urllib.request.urlopen', return_value=io.StringIO(
                '{"ok":true,"result":{"message_id":42}}')) as request:
            self.assertEqual(send_telegram('test-token', '123', raw), 42)
        payload = json.loads(request.call_args.args[0].data)
        self.assertIn('S&P 500 · 4h', payload['text'])
        self.assertIn('Price: 7708.5 → 7579.2', payload['text'])
        self.assertTrue(payload['text'].endswith('Provider: Yahoo'))
        for internal in ('Source:', 'linux-postgres', '3eb68542', 'yahoo:SNP:'):
            self.assertNotIn(internal, payload['text'])
        encoded = payload['text'].encode('utf-16-le')
        for entity in payload['entities']:
            self.assertTrue(encoded[entity['offset']*2:(entity['offset']+entity['length'])*2].decode('utf-16-le'))

    def test_alias_headers_and_feed_sources_for_other_strategies(self):
        for title in ('BULLISH BB Pin-Bars', 'BEARISH RSI + PB', 'BULLISH Pin-Bars'):
            text = public_text(title + '\nXAUUSD · 4h\nSource: tiingo:Tiingo:XAU/USD\nConfirmed: today')
            self.assertEqual(text.splitlines()[1], 'Gold · 4h')
            self.assertTrue(text.endswith('Provider: Tiingo'))
            self.assertNotIn('Source:', text)
            self.assertEqual(public_text(text), text)

    def test_generic_source_hidden_and_unknown_ticker_readable(self):
        self.assertEqual(public_text('Test\nSource: private machine · prod · id'), 'Test')
        self.assertEqual(public_text('Alert\nnew:venue:TEST · 1d'),
                         'Alert\nTEST · 1d\n--------------------\nProvider: new')


if __name__ == '__main__':
    unittest.main()
