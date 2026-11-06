import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from telegram_checklist import api
from telegram_dismissal import DismissibleMessage, MessageCannotBeDeleted
from telegram_personal import process
from telegram_personal import next_deadline
from telegram_checklist import bot_key
from test_postgres_support import PostgresTestCase, TestStore


class RecoveryTests(unittest.TestCase):
    @patch('telegram_checklist.urllib.request.urlopen')
    def test_only_explicit_undeletable_response_triggers_fallback(self, request):
        for description, expected in (("Bad Request: message can't be deleted", MessageCannotBeDeleted),
                                      ('Too Many Requests', RuntimeError)):
            request.side_effect = urllib.error.HTTPError('redacted', 400, 'error', {},
                io.BytesIO(json.dumps({'description': description}).encode()))
            with self.assertRaises(expected) as caught:
                api('token', 'deleteMessage', {})
            self.assertEqual(type(caught.exception), expected)
            self.assertNotIn('token', str(caught.exception))

    @patch('telegram_checklist.urllib.request.urlopen')
    def test_already_missing_is_success(self, request):
        request.side_effect = urllib.error.HTTPError('redacted', 400, 'error', {},
            io.BytesIO(json.dumps({'description': 'Bad Request: message to delete not found'}).encode()))
        self.assertTrue(api('token', 'deleteMessage', {}))

    @patch('telegram_personal.time.time', return_value=100)
    @patch('postgres_store.assert_worker')
    @patch('telegram_checklist.api')
    def test_failed_item_does_not_block_other_pending_messages(self, call, guard, clock):
        store = MagicMock()
        db = store.connect.return_value.__enter__.return_value
        items = [DismissibleMessage(1, n, {'text': 'original'}, phase='pending', delete_at=1)
                 for n in (1, 2)]
        db.execute.return_value.fetchall.return_value = [(i.key, json.dumps(i.snapshot())) for i in items]
        call.side_effect = [RuntimeError('offline'), True]
        process(store, 'token')
        states = [json.loads(c.args[1][0]) for c in db.execute.call_args_list
                  if c.args[0].startswith('UPDATE')]
        self.assertEqual(states[0]['phase'], 'pending')
        self.assertEqual(states[0]['retry_at'], 130)
        self.assertEqual(states[1]['phase'], 'deleted')
        self.assertEqual(call.call_count, 2)


class DatabaseRecoveryTests(PostgresTestCase):
    @patch('postgres_store.assert_worker')
    @patch('telegram_checklist.api')
    def test_restart_recovers_old_pending_rows_and_persists_independent_retry(self, call, guard):
        store = TestStore()
        items = [DismissibleMessage(1, n, {'text': 'original'}, phase='pending', delete_at=1)
                 for n in (1, 2, 3)]
        with store.connect() as db:
            for item in items:
                state = item.snapshot()
                state.pop('retry_at')  # Records written before this upgrade.
                db.execute('INSERT INTO telegram_personal_messages VALUES(%s,%s,%s)',
                           (item.key, bot_key('token'), json.dumps(state)))
        self.assertEqual(next_deadline(store, 'token'), 1)

        def respond(token, method, body):
            if body['message_id'] == 1:
                raise RuntimeError('offline')
            if body['message_id'] == 2 and method == 'deleteMessage':
                raise MessageCannotBeDeleted('too old')
            return True

        call.side_effect = respond
        process(store, 'token')
        with store.connect() as db:
            states = {s['message_id']: s for (raw,) in db.execute('SELECT state FROM telegram_personal_messages')
                      for s in [json.loads(raw)]}
        self.assertEqual(states[1]['phase'], 'pending')
        self.assertEqual(states[2]['phase'], 'completed')
        self.assertEqual(states[3]['phase'], 'deleted')
        self.assertEqual(next_deadline(store, 'token'), states[1]['retry_at'])
        call.reset_mock()
        process(store, 'token')
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
