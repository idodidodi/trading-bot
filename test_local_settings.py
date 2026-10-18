import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import cloud_sync
import local_settings
import runtime_env
from platform_app import Store, delivery_worker, handler_factory, load_rules
from test_platform import example


CREDENTIALS = {'TELEGRAM_BOT_TOKEN': 'test-token', 'TELEGRAM_CHAT_ID': '123',
               'CLOUD_SYNC_URL': 'https://sync.example.test', 'CLOUD_SYNC_TOKEN': 'test-token'}


class LocalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, CREDENTIALS, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'signals.sqlite3'
        self.store = Store(self.path)

    def save(self, telegram=False, cloud=False):
        return local_settings.save(self.store, dict(telegram_enabled=telegram, cloud_sync_enabled=cloud))

    def test_preferences_survive_reopening_override_environment_and_are_not_cloud_records(self):
        with patch.dict(os.environ, {'DEV_TELEGRAM_ENABLED': 'true', 'DEV_CLOUD_SYNC_ENABLED': 'true'}):
            self.assertTrue(local_settings.read(self.store)['telegram_enabled'])
            self.save()
            self.assertFalse(local_settings.read(Store(self.path))['telegram_enabled'])
            self.assertFalse(runtime_env.cloud_sync_enabled(self.store))
        self.save(telegram=True, cloud=True)
        self.assertTrue(local_settings.read(Store(self.path))['telegram_enabled'])
        other = Store(Path(self.folder.name) / 'other.sqlite3')
        self.assertFalse(local_settings.read(other)['telegram_enabled'])
        with patch('cloud_sync.ROOT', Path(self.folder.name)):
            cloud_sync.snapshot(self.store)
        with self.store.connect() as db:
            records = list(db.execute('SELECT kind,payload FROM cloud_pending'))
        self.assertNotIn('local_runtime_settings', json.dumps(records))
        self.assertNotIn('telegram_enabled', json.dumps(records))

    def test_production_keeps_its_existing_behavior_and_rejects_development_writes(self):
        self.save(telegram=True)
        with patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod'}):
            self.assertFalse(local_settings.read(self.store)['editable'])
            self.assertTrue(local_settings.delivery_dry_run(self.store, True))
            self.assertFalse(local_settings.delivery_dry_run(self.store, False))
            self.assertTrue(runtime_env.cloud_sync_enabled(self.store))
            with self.assertRaises(ValueError):self.save()

    def test_validation_is_atomic_and_never_returns_credentials(self):
        for body in ({'telegram_enabled': 'false', 'cloud_sync_enabled': False}, {},
                     {'telegram_enabled': False, 'cloud_sync_enabled': False, 'token': 'x'}):
            with self.assertRaises(ValueError):local_settings.save(self.store, body)
        for missing, key in [('TELEGRAM_BOT_TOKEN', 'telegram_enabled'), ('CLOUD_SYNC_TOKEN', 'cloud_sync_enabled')]:
            with patch.dict(os.environ, {missing: ''}):
                with self.assertRaises(ValueError):local_settings.save(self.store, {'telegram_enabled': False, 'cloud_sync_enabled': False} | {key: True})
        self.assertNotIn('test-token', json.dumps(local_settings.read(self.store)))
        self.assertFalse(local_settings.read(self.store)['telegram_enabled'])

    def test_cloud_worker_can_enable_and_disable_without_restarting(self):
        self.save()
        stop = threading.Event()
        transitions = []
        def wait(_):
            if not transitions:
                transitions.append('enabled')
                self.save(cloud=True)
            else:
                transitions.append('disabled')
                stop.set()
            return stop.is_set()
        def apply(*_):
            self.save(cloud=False)
        with patch.object(stop, 'wait', side_effect=wait), patch('cloud_sync.snapshot'), \
             patch('cloud_sync.batch', return_value={'records': []}), \
             patch('cloud_sync.urllib.request.urlopen', return_value=io.StringIO('{}')) as send, \
             patch('cloud_sync.apply', side_effect=apply):
            cloud_sync.worker(self.store, stop)
        self.assertEqual(transitions, ['enabled', 'disabled'])
        send.assert_called_once()

    def test_delivery_changes_live_and_does_not_resend_processed_dry_runs(self):
        self.save()
        self.store.insert(example())
        stop = threading.Event()
        thread = threading.Thread(target=delivery_worker, args=(self.store, 'test-token', '123', True, stop))
        def await_status(expected):
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                with self.store.connect() as db:
                    statuses = [row[0] for row in db.execute('SELECT status FROM signals ORDER BY received')]
                if statuses == expected:return
                time.sleep(.02)
            self.fail(f'Expected {expected}, got {statuses}')
        with patch('platform_app.send_telegram') as send:
            thread.start()
            try:
                await_status(['dry-run'])
                self.save(telegram=True)
                self.store.insert(example() | {'symbol': 'SECOND'})
                await_status(['dry-run', 'sent'])
                self.save()
                self.store.insert(example() | {'symbol': 'THIRD'})
                await_status(['dry-run', 'sent', 'dry-run'])
                send.assert_called_once()
            finally:
                stop.set()
                thread.join(4)
            self.assertFalse(thread.is_alive())

    def test_paper_preference_overrides_env_survives_restart_and_preserves_dev_isolation(self):
        with patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod', 'PAPER_TRADING_ENABLED': 'true',
                                     'ALPACA_API_KEY': 'dummy', 'ALPACA_SECRET_KEY': 'dummy'}):
            self.assertTrue(local_settings.paper_trading_enabled(self.store))
            local_settings.save(self.store, {'paper_trading_enabled': False})
            self.assertFalse(local_settings.paper_trading_enabled(Store(self.path)))
            local_settings.save(self.store, {'paper_trading_enabled': True})
            self.assertTrue(local_settings.paper_trading_enabled(Store(self.path)))
            with patch.dict(os.environ, {'TRADING_BOT_ENV': 'dev'}):
                self.assertFalse(local_settings.paper_trading_enabled(self.store))
                with self.assertRaises(ValueError):local_settings.save(self.store, {'paper_trading_enabled': True})
        with patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod'}):
            with self.assertRaises(ValueError):local_settings.save(self.store, {'paper_trading_enabled': True})
            self.assertTrue(local_settings.paper_trading_enabled(self.store))

    def test_disabled_saved_paper_setting_prevents_broker_calls_and_keeps_existing_trades(self):
        import paper_trading
        from unittest.mock import Mock
        with patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod', 'PAPER_TRADING_ENABLED': 'true'}):
            local_settings.save(self.store, {'paper_trading_enabled': False})
            with self.store.connect() as db:
                paper_trading.initialize(db)
                db.execute('INSERT INTO paper_trades VALUES(?,?)', ('existing', json.dumps({'id': 'existing', 'state': 'open', 'signal': {}})))
            broker = Mock()
            result = paper_trading.tick(self.store, broker)
            self.assertFalse(result['enabled'])
            self.assertEqual(result['trades'][0]['state'], 'open')
            broker.request.assert_not_called()

    def test_paper_worker_can_enable_and_disable_without_restart(self):
        import paper_trading
        with patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod', 'ALPACA_API_KEY': 'dummy', 'ALPACA_SECRET_KEY': 'dummy'}):
            local_settings.save(self.store, {'paper_trading_enabled': False})
            stop = threading.Event()
            waits = []
            def wait(_):
                waits.append(True)
                if len(waits) == 1:local_settings.save(self.store, {'paper_trading_enabled': True})
                if len(waits) == 3:stop.set()
                return stop.is_set()
            with patch.object(stop, 'wait', side_effect=wait), \
                 patch('paper_trading.check_and_log', side_effect=lambda _: local_settings.save(self.store, {'paper_trading_enabled': False})) as check:
                paper_trading.worker(self.store, stop)
            check.assert_called_once_with(self.store)
            self.assertFalse(local_settings.paper_trading_enabled(self.store))

    def test_production_paper_setting_api_is_local_and_accepts_only_booleans(self):
        with patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod', 'ALPACA_API_KEY': 'dummy', 'ALPACA_SECRET_KEY': 'dummy'}):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(self.store, load_rules(), 's' * 32, True, dashboard=True))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            try:
                request = urllib.request.Request(url + '/api/local-settings', data=b'{"paper_trading_enabled":true}',
                    headers={'Content-Type': 'application/json', 'Origin': url})
                with urllib.request.urlopen(request) as response:
                    value = json.load(response)
                    self.assertTrue(value['paper_trading_enabled'])
                    self.assertTrue(value['paper_trading_editable'])
                    self.assertFalse(value['editable'])
                self.assertNotIn('dummy', json.dumps(value))
                with urllib.request.urlopen(url + '/api/local-settings') as response:
                    self.assertTrue(json.load(response)['paper_trading_enabled'])
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_local_api_requires_same_origin_and_is_not_available_on_webhook_port(self):
        for dashboard in (True, False):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(self.store, load_rules(), 's' * 32, True, dashboard=dashboard))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            def call(body=None, origin=None):
                request = urllib.request.Request(url + '/api/local-settings', data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type': 'application/json', 'Origin': origin or url})
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    with error:return error.code, json.load(error)
            try:
                self.assertEqual(call()[0], 200 if dashboard else 404)
                body = dict(telegram_enabled=True, cloud_sync_enabled=True)
                self.assertEqual(call(body, 'https://foreign.example')[0], 403 if dashboard else 404)
                status, result = call(body)
                self.assertEqual(status, 200 if dashboard else 404)
                if dashboard:
                    self.assertTrue(result['telegram_enabled'])
                    self.assertEqual(call({'telegram_enabled': 'true'})[0], 400)
                    with urllib.request.urlopen(url + '/health') as response:
                        self.assertFalse(json.load(response)['dry_run'])
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
