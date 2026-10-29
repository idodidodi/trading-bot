from test_postgres_support import TestStore as Store, PostgresTestCase
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import connection_tests as tests
from platform_app import handler_factory, load_rules


class ConnectionTests(PostgresTestCase):
    def test_manual_telegram_test_sends_in_dry_run_without_using_the_delivery_queue(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'private-token','TELEGRAM_CHAT_ID':'123','DRY_RUN':'true'},clear=True),patch('platform_app.send_telegram') as send:
            result=tests.run('telegram',load_rules())
            self.assertTrue(result['ok'])
            send.assert_called_once()
            self.assertEqual(send.call_args.args[:2],('private-token','123'))
            self.assertIn('test message',send.call_args.args[2])
            self.assertIn('Family Trading Bot '+tests.VERSION,send.call_args.args[2])

    def test_database_check_is_read_only_and_does_not_contact_network(self):
        store=Store()
        with patch('urllib.request.urlopen') as network:
            self.assertTrue(tests.run('database',{},store)['ok'])
        network.assert_not_called()
        with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM signals').fetchone()[0],0)

    def test_database_errors_are_sanitized(self):
        import psycopg
        with patch('postgres_store.connect',side_effect=psycopg.OperationalError('private-token')):
            with self.assertRaises(tests.ConnectionTestError) as error:tests.run('database',{})
        self.assertNotIn('private-token',str(error.exception))


class ConnectionEndpointTests(PostgresTestCase):
    def test_tests_are_local_post_only_and_require_same_origin_json(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite3')
            for dashboard in (True,False):
                server=ThreadingHTTPServer(('127.0.0.1',0),handler_factory(store,load_rules(),'s'*32,True,dashboard=dashboard))
                thread=threading.Thread(target=server.serve_forever);thread.start()
                origin=f'http://127.0.0.1:{server.server_port}'
                try:
                    for supplied_origin,expected in ((origin,200),('https://other.example',403),('',403)):
                        expected=expected if dashboard else 404
                        headers={'Content-Type':'application/json','Origin':supplied_origin}
                        request=urllib.request.Request(origin+'/api/connection-test',data=b'{"action":"telegram"}',headers=headers)
                        with patch('connection_tests.run',return_value={'ok':True,'message':'sent'}) as run:
                            try:
                                with urllib.request.urlopen(request) as response:status=response.status
                            except urllib.error.HTTPError as error:
                                status=error.code;error.close()
                            self.assertEqual(status,expected)
                            self.assertEqual(run.call_count,1 if expected==200 else 0)
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(origin+'/api/connection-test')
                    self.assertEqual(caught.exception.code,404)
                    caught.exception.close()
                    if dashboard:
                        request=urllib.request.Request(origin+'/api/connection-test',data=b'{"action":"telegram"}',headers={'Content-Type':'application/json','Origin':origin})
                        with patch('connection_tests.run',side_effect=tests.ConnectionTestError('Safe failure')),self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
                        self.assertEqual(caught.exception.code,502)
                        self.assertEqual(json.load(caught.exception),{'error':'Safe failure'})
                        caught.exception.close()
                finally:
                    server.shutdown();server.server_close();thread.join()
            with store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM signals').fetchone()[0],0)
