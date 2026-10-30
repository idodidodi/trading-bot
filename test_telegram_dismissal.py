import json
import unittest
from unittest.mock import Mock

from telegram_dismissal import DismissibleMessage


class DismissalTests(unittest.TestCase):
    def setUp(self):
        self.original = {'text': 'Signal 📈\nPrice: 123', 'entities': [{'type': 'bold', 'offset': 0, 'length': 6}]}
        self.item = DismissibleMessage(123, 42, self.original, user_id=123)
        self.call = Mock()
        self.now = 100.
        self.clock = lambda: self.now

    def query(self, action):
        return dict(id='q', data=f'dismiss:{action}:{self.item.key}',
                    message={'chat': {'id': 123}, 'message_id': 42}, **{'from': {'id': 123}})

    def done(self):
        self.item.handle(self.query('done'), self.call, self.clock)

    def test_done_collapses_then_deletes_after_three_seconds(self):
        self.done()
        self.assertEqual(self.item.delete_at, 103)
        self.assertEqual(self.call.call_args_list[0].args[1]['reply_markup'], self.item.keyboard(True))
        self.now = 102.99
        self.assertFalse(self.item.expire(self.call, self.clock))
        self.now = 103
        self.assertTrue(self.item.expire(self.call, self.clock))
        self.call.assert_called_with('deleteMessage', {'chat_id': 123, 'message_id': 42})
        self.assertEqual(self.item.phase, 'deleted')
        self.assertFalse(self.item.expire(self.call, self.clock))

    def test_undo_restores_original_text_and_entities_and_cancels_deletion(self):
        self.done()
        self.now = 102
        self.call.reset_mock()
        self.item.handle(self.query('undo'), self.call, self.clock)
        body = self.call.call_args_list[0].args[1]
        self.assertEqual(body['text'], self.original['text'])
        self.assertEqual(body['entities'], self.original['entities'])
        self.assertEqual(self.item.phase, 'active')
        self.now = 104
        self.assertFalse(self.item.expire(self.call, self.clock))

    def test_late_undo_does_not_restore(self):
        self.done()
        self.now = 103
        self.call.reset_mock()
        self.item.handle(self.query('undo'), self.call, self.clock)
        self.assertEqual(self.call.call_args_list[0].args[0], 'deleteMessage')
        self.assertEqual(self.item.phase, 'deleted')

    def test_duplicate_done_does_not_extend_grace_period(self):
        self.done()
        self.now = 102
        self.done()
        self.assertEqual(self.item.delete_at, 103)

    def test_other_chats_messages_users_and_keys_are_ignored(self):
        for field in ('chat', 'message', 'user', 'key'):
            query = self.query('done')
            if field == 'chat': query['message']['chat']['id'] = 456
            if field == 'message': query['message']['message_id'] = 43
            if field == 'user': query['from']['id'] = 456
            if field == 'key': query['data'] = 'dismiss:done:wrong'
            self.assertFalse(self.item.handle(query, self.call, self.clock))
        self.call.assert_not_called()

    def test_pending_state_survives_json_roundtrip(self):
        self.done()
        restored = DismissibleMessage.restore(json.loads(json.dumps(self.item.snapshot())))
        self.assertEqual(restored.snapshot(), self.item.snapshot())
        self.now = 104
        self.assertTrue(restored.expire(self.call, self.clock))

    def test_failed_delete_remains_pending_for_retry(self):
        self.done()
        self.now = 104
        self.call.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError): self.item.expire(self.call, self.clock)
        self.assertEqual(self.item.phase, 'pending')
        self.call.side_effect = None
        self.assertTrue(self.item.expire(self.call, self.clock))

    def test_failed_edit_does_not_start_deletion(self):
        self.call.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError): self.done()
        self.assertEqual(self.item.phase, 'active')
        self.assertIsNone(self.item.delete_at)


if __name__ == '__main__':
    unittest.main()
