import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from platform_app import Store, delivery_worker, send_telegram
import telegram_delivery as delivery


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'a').mkdir()
        (self.root / 'b').mkdir()
        self.store = Store(self.root / 'a' / 'signals.db')
        self.other = Store(self.root / 'b' / 'signals.db')

    def send(self, store=None, text='alert', event='signal:one', chat='123', send=None):
        return delivery.deliver(store or self.store, '123:secret', chat, text, event, send or self.sender)

    def test_source_survives_restart_separates_data_dirs_and_labels_every_message(self):
        with patch.dict(os.environ, TELEGRAM_DEVICE_LABEL='home\npc', TRADING_BOT_ENV='dev'):
            identity = delivery.source(self.root / 'a')
            self.assertEqual(identity, delivery.source(self.root / 'a'))
            self.assertNotEqual(identity['sender_id'], delivery.source(self.root / 'b')['sender_id'])
            with patch('platform_app.urllib.request.urlopen', return_value=io.StringIO('{"ok":true,"result":{"message_id":42}}')) as post:
                self.assertEqual(send_telegram('secret', '123', 'hello', source_identity=identity), 42)
            self.assertEqual(json.loads(post.call_args.args[0].data)['text'],
                             'hello\n\nSource: home pc · dev · ' + identity['sender_id'][:8])

    def test_accepted_send_completes_after_restart_without_resending(self):
        self.sender = Mock(return_value=42)
        with patch('telegram_delivery.coordinator', side_effect=[{'status':'acquired'}, {'status':'acquired'}, delivery.DeliveryPending('offline')]):
            with self.assertRaises(delivery.DeliveryPending): self.send()
        with patch('telegram_delivery.coordinator', return_value={'status':'completed'}) as cloud:
            self.assertEqual(self.send(Store(self.store.path)), 'sent')
            self.assertEqual(cloud.call_args.args[0]['action'], 'complete')
            self.assertEqual(cloud.call_args.args[0]['message_id'], 42)
        self.sender.assert_called_once()

    def test_busy_outage_and_lost_lease_never_send(self):
        self.sender = Mock()
        for response in [delivery.DeliveryPending('offline'), {'status':'busy'}]:
            with patch('telegram_delivery.coordinator', side_effect=[response]):
                with self.assertRaises(delivery.DeliveryPending): self.send()
        with patch('telegram_delivery.coordinator', side_effect=[{'status':'acquired'}, {'status':'busy'}]):
            with self.assertRaises(delivery.DeliveryPending): self.send()
        self.sender.assert_not_called()

    def test_timeout_releases_and_retries_with_new_owner(self):
        self.sender = Mock(side_effect=[RuntimeError('Telegram connection timed out'), 10])
        with patch('telegram_delivery.coordinator', side_effect=[{'status':'acquired'}, {'status':'acquired'}, {'status':'released'}, {'status':'acquired'}, {'status':'acquired'}, {'status':'completed'}]) as cloud:
            with self.assertRaises(RuntimeError): self.send()
            self.assertEqual(self.send(), 'sent')
        self.assertNotEqual(cloud.call_args_list[0].args[0]['owner'], cloud.call_args_list[3].args[0]['owner'])
        self.assertEqual(self.sender.call_count, 2)

    def test_concurrent_senders_one_sends_and_other_suppresses_after_success(self):
        claimed = threading.Event()
        proceed = threading.Event()
        state = {'owner':None, 'sent':False}
        mutex = threading.Lock()
        def coordinate(p):
            with mutex:
                if state['sent']: return {'status':'already_sent'}
                if p['action']=='acquire':
                    if state['owner'] and state['owner']!=p['owner']: return {'status':'busy'}
                    state['owner']=p['owner']
                if p['action']=='complete': state['sent']=True;return {'status':'completed'}
                return {'status':'acquired'}
        def send(*args, **kwargs):
            claimed.set()
            if not proceed.wait(5): raise RuntimeError('test timed out')
            return 42
        results=[]
        def run():
            try: results.append(self.send(send=send))
            except Exception as exc: results.append(exc)
        with patch('telegram_delivery.coordinator', side_effect=coordinate):
            thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(claimed.wait(5))
                with self.assertRaises(delivery.DeliveryPending): self.send(self.other, send=Mock())
                # Two local workers must not share the active claim token either.
                with self.assertRaises(delivery.DeliveryPending): self.send(send=Mock())
            finally:
                proceed.set();thread.join(5)
            self.assertEqual(results, ['sent'])
            suppressed=Mock()
            self.assertEqual(self.send(self.other, send=suppressed), 'suppressed')
            suppressed.assert_not_called()

    def test_hashes_ignore_source_and_format_for_stable_events_but_scope_destination(self):
        self.sender=Mock()
        with patch('telegram_delivery.coordinator', return_value={'status':'already_sent'}) as cloud:
            self.send(text='first')
            a=cloud.call_args.args[0]
            with patch.dict(os.environ, TELEGRAM_DEVICE_LABEL='other', TRADING_BOT_ENV='prod'):
                self.send(self.other, text='changed')
            b=cloud.call_args.args[0]
            self.assertEqual(a['event_hash'], b['event_hash'])
            self.assertNotEqual(a['body_hash'], b['body_hash'])
            self.send(chat='456');c=cloud.call_args.args[0]
            self.assertNotEqual(a['destination_hash'],c['destination_hash'])
            self.send(event='signal:two');d=cloud.call_args.args[0]
            self.assertNotEqual(a['event_hash'],d['event_hash'])
            self.send(event=None);e=cloud.call_args.args[0]
            self.assertEqual(e['body_hash'],e['event_hash'])
        self.sender.assert_not_called()

    def test_missing_configuration_and_network_errors_are_safe(self):
        with patch.dict(os.environ, {}, clear=True), patch('telegram_delivery.urllib.request.urlopen') as network:
            with self.assertRaisesRegex(delivery.DeliveryPending, 'not configured'): delivery.coordinator({})
            network.assert_not_called()
        with patch.dict(os.environ, CLOUD_SYNC_URL='https://example.test/functions/v1/sync', CLOUD_SYNC_TOKEN='private', DEV_CLOUD_SYNC_ENABLED='false'), \
             patch('telegram_delivery.urllib.request.urlopen', return_value=io.StringIO('{"status":"busy"}')) as network:
            self.assertEqual(delivery.coordinator({}), {'status':'busy'})
            self.assertEqual(network.call_args.args[0].full_url, 'https://example.test/functions/v1/telegram-delivery')
        with patch.dict(os.environ, CLOUD_SYNC_URL='https://example.test/sync', CLOUD_SYNC_TOKEN='private'), \
             patch('telegram_delivery.urllib.request.urlopen', side_effect=RuntimeError('private')):
            with self.assertRaisesRegex(delivery.DeliveryPending, '^Telegram coordination unavailable$'): delivery.coordinator({})

    @patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod'})
    def test_worker_dry_run_does_not_contact_coordinator_and_suppression_is_explicit(self):
        from scanner_control import initialize
        with self.store.connect() as db:
            initialize(db)
            db.execute("INSERT INTO notification_queue(id,text) VALUES('one','hello')")
        stop=Mock();stop.is_set.side_effect=[False,True]
        with patch('telegram_delivery.coordinator') as cloud, patch('platform_app.send_telegram') as send:
            delivery_worker(self.store,'test','123',True,stop)
            cloud.assert_not_called();send.assert_not_called()
        with self.store.connect() as db:
            db.execute("UPDATE notification_queue SET status='pending'")
        stop.is_set.side_effect=[False,True]
        with patch('telegram_delivery.coordinator',return_value={'status':'already_sent'}), patch('platform_app.send_telegram') as send:
            delivery_worker(self.store,'test','123',False,stop)
            send.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM notification_queue').fetchone()[0],'suppressed')


if __name__=='__main__': unittest.main()
