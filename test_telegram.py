from test_postgres_support import TestStore as Store, PostgresTestCase
import contextlib
import io
import json
import os
import ssl
import unittest
import urllib.error
from unittest.mock import patch

from platform_app import send_telegram
from telegram_setup import main
from telegram_formatting import presentation, utf16_length


class TelegramTests(PostgresTestCase):
    def test_presentation_preserves_content_for_all_message_families(self):
        samples = [
            'Good morning, traders! ☀️\n\nMorning market prices · 20 Oct 2026 10:30 (Israel time)\n\n'
            'S&P 500: 7,690.73 points\n  As of 19 Oct 23:00 · older quote\n'
            'DAX: 25,144.79 points\n  As of 20 Oct 10:14\nBitcoin: unavailable\n\n'
            'Source: Yahoo Finance · quotes may be delayed.\nAll quote times are Israel time.',
            'BULLISH RSI divergence (provisional)\nAAPL · 1d\nPrice: 100 → 99\nRSI(14): 20 → 30\n'
            'Second pivot closed: 20 Oct 2026 at 10:30 (Israel time)\nSignal only · provisional pivot · closed candle',
            'BEARISH Pin-Bars\nBTC/USD · 4h\nEMA 8/21: 100 / 101\nBody: 2 · lower wick: 1 · upper wick: 5',
            'BULLISH BB Pin-Bars\nEUR/USD · 1d\nBB 20/2: 1.1 / 1.2\nSource: <feed> & [asset]',
            'Catch-up completed\n2 new divergence findings and 1 Pin-Bar signals saved.\nOpen Signals for details.',
            'Trading signal platform: Telegram connection test.',
        ]
        for original in samples:
            with self.subTest(title=original.split('\n')[0]):
                display, entities = presentation(original)
                self.assertEqual([s for s in display.split('\n') if s],
                                 [s for s in original.split('\n') if s])
                self.assertEqual(entities[0]['type'], 'bold')
                encoded = display.encode('utf-16-le')
                for entity in entities:
                    fragment = encoded[entity['offset']*2:(entity['offset']+entity['length'])*2].decode('utf-16-le')
                    self.assertTrue(fragment)
                    self.assertNotIn('\n', fragment)

    def test_quote_grouping_and_emoji_entity_offsets(self):
        text = 'Good morning! ☀️ 📈\n\nS&P 500: 7,690.73 points\n  As of 19 Oct 23:00 · older quote\nDAX: unavailable'
        display, entities = presentation(text)
        self.assertIn('points\n  As of 19 Oct 23:00 · older quote\n\nDAX:', display)
        quote = next(e for e in entities if e['type'] == 'italic')
        self.assertEqual(quote['offset'], utf16_length(display[:display.index('As of ')]))
        self.assertEqual(quote['length'], utf16_length('As of 19 Oct 23:00 · older quote'))

    def test_spacing_does_not_exceed_message_limit_or_entity_limit(self):
        text = 'Header\n' + '\n'.join(['x' * 40] * 99)
        display, entities = presentation(text)
        self.assertEqual(display, text)
        display, entities = presentation('Title\n' + '\n'.join(['Price: 123'] * 110))
        self.assertLessEqual(len(entities), 100)

    def test_sender_uses_native_entities_for_literal_metacharacters(self):
        identity = {'device':'test <pc>', 'environment':'prod', 'sender_id':'12345678'}
        text = 'Market 📈\nS&P 500: 7,690.73 points\n  As of 19 Oct 23:00 · older quote'
        with patch('platform_app.urllib.request.urlopen', return_value=io.StringIO('{"ok":true}')) as request:
            send_telegram('private-token', '123', text, source_identity=identity)
        payload = json.loads(request.call_args.args[0].data)
        self.assertIn('entities', payload)
        self.assertNotIn('parse_mode', payload)
        self.assertIn('S&P 500: 7,690.73 points', payload['text'])
        self.assertTrue(payload['text'].endswith('Source: test <pc> · prod · 12345678'))

    def test_connection_errors_are_actionable_and_token_safe(self):
        token = 'private-test-token'
        errors = [
            (urllib.error.URLError(ssl.SSLCertVerificationError(1, token)),
             'TLS certificate verification failed'),
            (urllib.error.URLError(TimeoutError(token)), 'connection timed out'),
            (TimeoutError(token), 'connection timed out'),
            (urllib.error.URLError(f'https://api.telegram.org/bot{token}/sendMessage'),
             'connection failed'),
            (urllib.error.HTTPError(f'https://api.telegram.org/bot{token}/sendMessage',
                                   401, token, {}, None), 'HTTP 401'),
        ]
        for error, expected in errors:
            with self.subTest(error=type(error).__name__):
                with patch('platform_app.urllib.request.urlopen', side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        send_telegram(token, '123', 'Test')
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn(token, str(caught.exception))
                if isinstance(error, urllib.error.HTTPError):
                    error.close()

    def test_setup_failure_exits_without_success_output(self):
        for command in ('chats', 'test'):
            with self.subTest(command=command):
                output = io.StringIO()
                error = urllib.error.URLError(ssl.SSLCertVerificationError(1, 'private-token'))
                with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='private-token', TELEGRAM_CHAT_ID='123'):
                    with patch('sys.argv', ['telegram_setup.py', command]), patch('telegram_setup.load_env'):
                        with patch('platform_app.urllib.request.urlopen', side_effect=error):
                            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                                main()
                self.assertIn('TLS certificate verification failed', str(caught.exception))
                self.assertNotIn('private-token', str(caught.exception))
                self.assertEqual(output.getvalue(), '')

    def test_send_success(self):
        with patch('platform_app.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'
            send_telegram('private-token', '123', 'Test')
            request.assert_called_once()

    def test_notification_sound_flag_is_sent_to_telegram(self):
        for silent in (False, True):
            with patch('platform_app.urllib.request.urlopen', return_value=io.StringIO('{"ok":true}')) as request:
                send_telegram('private-token', '123', 'Test', silent=silent,
                              source_identity={'device':'test', 'environment':'dev', 'sender_id':'12345678'})
            self.assertIs(json.loads(request.call_args.args[0].data)['disable_notification'], silent)


if __name__ == '__main__':
    unittest.main()
