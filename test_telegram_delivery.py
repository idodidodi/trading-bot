"""Real Postgres reservations with mocked Telegram; never sends network requests."""
import json
import threading
import unittest
from unittest.mock import Mock,patch
from test_postgres_support import TestStore as Store,PostgresTestCase
import telegram_delivery as d

class DeliveryTests(PostgresTestCase):
    def setUp(self):self.store=Store();self.sender=Mock(return_value=42)
    def send(self,event='event',send=None,silent=False):return d.deliver(self.store,'123:secret','1','hello',event,send or self.sender,silent)
    def test_source_identity_is_shared_persistent_and_can_be_explicit(self):
        with patch.dict('os.environ',TELEGRAM_DEVICE_LABEL='test device'):
            first=d.source();self.assertEqual(first,d.source())
            with patch.dict('os.environ',TELEGRAM_SENDER_ID='00000000-0000-0000-0000-000000000009'):self.assertTrue(d.source()['sender_id'].endswith('9'))
        self.assertNotIn('secret',json.dumps(first))
    def test_success_and_restart_do_not_resend(self):
        self.assertEqual(self.send(),'sent');self.store=Store()
        self.assertEqual(self.send(),'suppressed');self.sender.assert_called_once()
    def test_delivery_audit_links_message_to_source_and_event(self):
        self.send()
        with self.store.connect() as db:
            details = json.loads(db.execute("SELECT details FROM activity_log WHERE event='telegram-delivered'").fetchone()[0])
        self.assertEqual(details['message_id'],42)
        self.assertEqual(details['chat_id'],'1')
        self.assertEqual(details['event_id'],'event')
        self.assertEqual(details['original_text'],'hello')
        self.assertTrue(details['sender_id'])
        self.assertTrue(details['device'])
        self.assertIn('environment',details)
        self.assertNotIn('secret',json.dumps(details))
    def test_sound_slot_is_shared_and_quiet_is_preserved(self):
        self.send('1');self.assertFalse(self.sender.call_args.kwargs['silent'])
        self.send('2');self.assertTrue(self.sender.call_args.kwargs['silent'])
        self.send('3',silent=True);self.assertTrue(self.sender.call_args.kwargs['silent'])
    def test_ambiguous_send_retains_lease_then_can_retry(self):
        with self.assertRaises(RuntimeError):self.send(send=Mock(side_effect=RuntimeError('timeout')))
        with self.assertRaises(d.DeliveryPending):self.send()
        self.sender.assert_not_called()
        with self.store.connect() as db:db.execute('UPDATE telegram_delivery SET locked_until=0')
        self.assertEqual(self.send(),'sent')
    def test_concurrent_sender_is_busy_then_suppressed(self):
        entered=threading.Event();finish=threading.Event();errors=[]
        def send(*a,**k):entered.set();finish.wait(3);return 10
        def run():
            try:self.send(send=send)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(d.DeliveryPending):self.send()
        finally:finish.set();thread.join(4)
        self.assertEqual(errors,[]);self.assertEqual(self.send(),'suppressed')
    def test_database_failure_prevents_external_send(self):
        with patch.object(self.store,'connect',side_effect=__import__('psycopg').OperationalError('offline')):
            with self.assertRaises(__import__('psycopg').OperationalError):self.send()
        self.sender.assert_not_called()
