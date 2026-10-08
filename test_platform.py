import copy
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

from generate_pine import render
from platform_app import Store, delivery_worker, handler_factory, load_env, load_rules, validate_signal


def example():
    return dict(secret='s' * 32, symbol='OANDA:EURUSD', timeframe='240', direction='bullish',
                price1=1.10, price2=1.09, rsi1=15.0, rsi2=25.0, band1=1.11, band2=1.095,
                band_slope_pct=0.25,
                pivot1=1700000000000, pivot2=1700100000000, confirmed_at=1700200000000,
                spacing=7, rules=load_rules())


class EnvTests(unittest.TestCase):
    def test_notes_quotes_export_and_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, '.env').write_text(
                '# comment\nPasted setup instructions\ninvalid key=ignored\n'
                'export TEST_KEY="secret=with-equals"\nTEST_OTHER=\'value\'\n'
                'TEST_EXISTING=file-value\n')
            with patch('platform_app.ROOT', Path(directory)), patch.dict(
                    os.environ, {'TEST_EXISTING': 'environment-value'}, clear=True):
                load_env()
                self.assertEqual(os.environ['TEST_KEY'], 'secret=with-equals')
                self.assertEqual(os.environ['TEST_OTHER'], 'value')
                self.assertEqual(os.environ['TEST_EXISTING'], 'environment-value')
                self.assertEqual(len(os.environ), 3)


class RulesTests(unittest.TestCase):
    def test_bullish_and_bearish(self):
        p = example()
        clean = validate_signal(p, load_rules())
        self.assertNotIn('secret', clean)
        p.update(direction='bearish', price1=1.1, price2=1.12, rsi1=90, rsi2=80, band2=1.115)
        self.assertEqual(validate_signal(p, load_rules())['direction'], 'bearish')

    def test_reject_invalid_evidence(self):
        changes = [dict(price2=1.11), dict(rsi2=10), dict(band2=1.08), dict(spacing=2),
                   dict(rsi1=float('nan')), dict(pivot2=1700000000000), dict(rsi2=101), dict(rules={})]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_signal(example() | change, load_rules())

    def test_pine_uses_skill(self):
        pine = render()
        self.assertIn('int rsiPeriod = 3', pine)
        self.assertIn('float bbMultiplier = 2.0', pine)
        self.assertIn(load_rules()['skill_hash'], pine)
        self.assertNotIn('__RULES_JSON__', pine)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(self.store, load_rules(), 's' * 32, True))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, body):
        req = urllib.request.Request(self.url + '/webhook', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)

    def test_accept_deduplicate_and_no_secret_storage(self):
        status, result = self.post(example())
        self.assertEqual(status, 202)
        self.assertFalse(result['duplicate'])
        self.assertTrue(self.post(example())[1]['duplicate'])
        rows = self.store.rows()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('secret', json.loads(rows[0][0]))

    def test_unauthorized_and_invalid_signals(self):
        for change, expected in [(dict(secret='wrong'), 401), (dict(band2=1), 400)]:
            with self.subTest(change=change), self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(example() | change)
            self.assertEqual(caught.exception.code, expected)
        self.assertEqual(self.store.rows(), [])

    def test_public_port_has_no_dashboard(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.url + '/')
        self.assertEqual(caught.exception.code, 404)

    def test_delivery_retries_and_recovers(self):
        self.store.insert(validate_signal(example(), load_rules()))
        stop = threading.Event()
        with patch('platform_app.send_telegram', side_effect=[RuntimeError('Telegram connection failed'), None]) as send:
            worker = threading.Thread(target=delivery_worker, args=(self.store, 'test-token', '123', False, stop))
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and self.store.rows()[0][1] != 'sent':
                    time.sleep(0.05)
                self.assertEqual(self.store.rows()[0][1], 'sent')
                self.assertEqual(send.call_count, 2)
            finally:
                stop.set()
                worker.join(3)


if __name__ == '__main__':
    unittest.main()
