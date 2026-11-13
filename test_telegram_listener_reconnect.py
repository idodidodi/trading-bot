"""Regression coverage for transport errors that previously killed Done polling."""
import http.client
import io
import unittest
from unittest.mock import MagicMock, patch

import telegram_checklist as checklist


class ListenerReconnectTests(unittest.TestCase):
    @patch('telegram_checklist.urllib.request.urlopen')
    def test_direct_transport_errors_are_sanitized_and_retryable(self, request):
        for error in (http.client.RemoteDisconnected('secret URL'),
                      ConnectionResetError('secret URL'),
                      http.client.IncompleteRead(b'secret response'),
                      http.client.BadStatusLine('secret response')):
            with self.subTest(error=type(error).__name__):
                request.side_effect = error
                with self.assertRaisesRegex(RuntimeError, '^Telegram checklist connection failed$'):
                    checklist.api('secret-token', 'getUpdates', {})

    @patch('telegram_checklist.urllib.request.urlopen')
    def test_interrupted_body_and_invalid_json_are_retryable(self, request):
        response = MagicMock()
        request.return_value.__enter__.return_value = response
        response.read.side_effect = http.client.IncompleteRead(b'partial secret')
        with self.assertRaisesRegex(RuntimeError, 'connection failed'):
            checklist.api('secret-token', 'getUpdates', {})
        request.return_value = io.StringIO('invalid secret response')
        with self.assertRaisesRegex(RuntimeError, '^Telegram checklist invalid response$'):
            checklist.api('secret-token', 'getUpdates', {})

    @patch('telegram_checklist.urllib.request.urlopen')
    @patch('telegram_checklist._worker')
    def test_worker_resumes_polling_after_remote_disconnect(self, poll, request):
        request.side_effect = http.client.RemoteDisconnected('closed')
        def first_poll(*args):
            poll.side_effect = None
            return checklist.api('secret-token', 'getUpdates', {})
        poll.side_effect = first_poll
        stop = MagicMock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        checklist.worker(MagicMock(), 'secret-token', False, stop)
        self.assertEqual(poll.call_count, 2)
        stop.wait.assert_called_once_with(1)


if __name__ == '__main__':
    unittest.main()
