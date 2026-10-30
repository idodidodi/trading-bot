import io
import json
import unittest
from unittest.mock import MagicMock, patch

import telegram_checklist as checklist


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.store = MagicMock()
        self.db = self.store.connect.return_value.__enter__.return_value
        self.original = {'text': 'Alert 📈\nPrice: 123', 'entities': [{'type': 'bold', 'offset': 0, 'length': 5}]}
        self.db.execute.return_value.fetchone.return_value = (json.dumps(self.original), False)
        self.key = 'a' * 32
        self.query = dict(id='query', data='cl:done:' + self.key,
                          message={'chat': {'id': -123}, 'message_id': 42})

    @patch('postgres_store.assert_worker')
    @patch('telegram_checklist.api')
    def test_done_and_undo_restore_exact_text_and_entities(self, api, guard):
        checklist.handle(self.store, 'token', self.query)
        body = api.call_args_list[0].args[2]
        self.assertEqual(body['text'], '✓ Completed')
        self.assertEqual(body['reply_markup'], checklist.keyboard(self.key, True))
        self.query['data'] = 'cl:undo:' + self.key
        api.reset_mock()
        checklist.handle(self.store, 'token', self.query)
        body = api.call_args_list[0].args[2]
        self.assertEqual(body['text'], self.original['text'])
        self.assertEqual(body['entities'], self.original['entities'])
        self.assertEqual(body['reply_markup'], checklist.keyboard(self.key))
        self.assertEqual(body['chat_id'], '-123')
        self.assertEqual(body['message_id'], 42)

    @patch('telegram_checklist.api')
    def test_wrong_chat_message_or_bot_cannot_edit(self, api):
        self.db.execute.return_value.fetchone.return_value = None
        checklist.handle(self.store, 'token', self.query)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[1], 'answerCallbackQuery')
        args = self.db.execute.call_args.args
        self.assertIn('AND bot=%s AND chat=%s AND message_id=%s', args[0])
        self.assertEqual(args[1], (self.key, checklist.bot_key('token'), '-123', 42))

    @patch('telegram_checklist.api')
    def test_unrelated_or_malformed_callback_is_ignored(self, api):
        for data in ('other', 'cl:done:bad', 'cl:delete:' + self.key):
            checklist.handle(self.store, 'token', dict(self.query, data=data))
        api.assert_not_called()
        self.db.execute.assert_not_called()

    @patch('postgres_store.assert_worker')
    @patch('telegram_checklist.api', side_effect=RuntimeError('offline'))
    def test_failed_edit_does_not_commit_completed_state(self, api, guard):
        with self.assertRaises(RuntimeError):
            checklist.handle(self.store, 'token', self.query)
        self.assertEqual(self.db.execute.call_count, 1)
        self.assertEqual(self.store.connect.return_value.__exit__.call_args.args[0], RuntimeError)

    @patch('telegram_checklist.enabled', return_value=True)
    @patch('telegram_checklist.prepare', return_value='a' * 32)
    @patch('telegram_checklist.bind')
    @patch('platform_app.Store')
    def test_send_attaches_button_and_saves_canonical_chat_id(self, store, bind, prepare, enabled):
        from platform_app import send_telegram
        response = {'ok': True, 'result': {'message_id': 42, 'chat': {'id': -123}}}
        with patch('platform_app.urllib.request.urlopen', return_value=io.StringIO(json.dumps(response))) as request:
            result = send_telegram('token', '@group', 'Alert', source_identity=dict(device='test', environment='dev', sender_id='12345678'))
        self.assertEqual(result, 42)
        self.assertEqual(json.loads(request.call_args.args[0].data)['reply_markup'], checklist.keyboard(self.key))
        bind.assert_called_once_with(store.return_value, self.key, response['result'])

class ListenerLatencyTests(unittest.TestCase):
    @patch('telegram_checklist.enabled',return_value=True)
    @patch('telegram_personal.next_deadline',return_value=None)
    @patch('telegram_personal.process')
    @patch('local_settings.delivery_dry_run',return_value=False)
    @patch('postgres_store.assert_worker')
    @patch('telegram_checklist.api')
    def test_callback_is_processed_without_scanning_due_messages_first(self,api,guard,dry,process,deadline,enabled):
        store=MagicMock()
        db=store.connect.return_value.__enter__.return_value
        db.execute.return_value.fetchone.side_effect=[(10,),('bot_runtime.telegram_personal_messages',)]
        query={'data':'dismiss:done:'+'a'*32}
        api.return_value=[{'update_id':10,'callback_query':query}]
        stop=MagicMock();stop.is_set.side_effect=[False,True]
        checklist._worker(store,'token',False,stop)
        process.assert_called_once_with(store,'token',query)
        self.assertEqual(api.call_args.args[2]['offset'],10)
        self.assertEqual(api.call_args.args[2]['timeout'],10)
        # One initial offset read; no offset lookup on each iteration.
        sql=[call.args[0] for call in db.execute.call_args_list]
        self.assertEqual(sum('SELECT next_update' in q for q in sql),1)

    @patch('telegram_checklist.enabled',return_value=True)
    @patch('telegram_personal.next_deadline',return_value=1000)
    @patch('telegram_personal.process')
    @patch('local_settings.delivery_dry_run',return_value=False)
    @patch('postgres_store.assert_worker')
    @patch('telegram_checklist.time.time',return_value=999)
    @patch('telegram_checklist.api',return_value=[])
    def test_pending_undo_never_uses_a_ten_second_long_poll(self,api,clock,guard,dry,process,deadline,enabled):
        store=MagicMock();db=store.connect.return_value.__enter__.return_value
        db.execute.return_value.fetchone.side_effect=[(10,),('table',)]
        stop=MagicMock();stop.is_set.side_effect=[False,True]
        checklist._worker(store,'token',False,stop)
        self.assertEqual(api.call_args.args[2]['timeout'],0)
        process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
