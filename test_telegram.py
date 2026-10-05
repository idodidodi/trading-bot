import contextlib
import io
import os
import ssl
import unittest
import urllib.error
from unittest.mock import patch

from platform_app import send_telegram
from telegram_setup import main


class TelegramTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
