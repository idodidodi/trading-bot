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
from platform_app import Store, handler_factory, load_rules


class ConnectionTests(unittest.TestCase):
    def test_manual_telegram_test_sends_in_dry_run_without_using_the_delivery_queue(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'private-token','TELEGRAM_CHAT_ID':'123','DRY_RUN':'true'},clear=True),patch('platform_app.send_telegram') as send:
            result=tests.run('telegram',load_rules())
            self.assertTrue(result['ok'])
            send.assert_called_once()
            self.assertEqual(send.call_args.args[:2],('private-token','123'))
            self.assertIn('test message',send.call_args.args[2])
            self.assertIn('Family Trading Bot '+tests.VERSION,send.call_args.args[2])

    def test_missing_credentials_and_remote_errors_are_safe(self):
        with patch.dict(os.environ,{},clear=True):
            for action in ('telegram','cloud-demo'):
                with self.subTest(action=action),self.assertRaises(tests.ConnectionTestError):tests.run(action,{})
        env={'TELEGRAM_BOT_TOKEN':'private-token','TELEGRAM_CHAT_ID':'123','CLOUD_SYNC_URL':'https://cloud.test/sync','CLOUD_SYNC_TOKEN':'private-cloud-token'}
        with patch.dict(os.environ,env,clear=True),patch('platform_app.send_telegram',side_effect=RuntimeError('private-token')),patch('connection_tests.urllib.request.urlopen',side_effect=OSError('private-cloud-token')):
            for action in ('telegram','cloud-demo'):
                with self.subTest(action=action),self.assertRaises(tests.ConnectionTestError) as caught:tests.run(action,{})
                self.assertNotIn('private-',str(caught.exception))
        with self.assertRaises(ValueError):tests.run('unknown',{})

    def test_cloud_demo_sends_only_one_labeled_record_and_requires_acknowledgment(self):
        captured=[]
        def respond(request,**kwargs):
            payload=json.loads(request.data);captured.append(payload)
            self.assertEqual(request.get_header('Authorization'),'Bearer private-cloud-token')
            return io.BytesIO(json.dumps({'ack':[payload['records'][0]['event_id']]}).encode())
        with patch.dict(os.environ,{'CLOUD_SYNC_URL':'https://cloud.test/sync','CLOUD_SYNC_TOKEN':'private-cloud-token','DEV_CLOUD_SYNC_ENABLED':'false'},clear=True),patch('connection_tests.urllib.request.urlopen',side_effect=respond):
            result=tests.run('cloud-demo',load_rules())
        record=captured[0]['records'][0]
        self.assertEqual(len(captured[0]['records']),1)
        self.assertEqual(record['kind'],'live')
        self.assertTrue(record['payload']['is_demo'])
        self.assertTrue(record['payload']['signal']['is_demo'])
        self.assertEqual(record['payload']['signal']['symbol'],'DEMO-CLOUD-SYNC')
        self.assertEqual(result['id'],record['key'])
        self.assertNotIn('private-cloud-token',json.dumps(captured))
        with patch.dict(os.environ,{'CLOUD_SYNC_URL':'https://cloud.test/sync','CLOUD_SYNC_TOKEN':'private-cloud-token'},clear=True),patch('connection_tests.urllib.request.urlopen',return_value=io.BytesIO(b'{"ack":[]}')):
            with self.assertRaises(tests.ConnectionTestError):tests.run('cloud-demo',{})


class ConnectionEndpointTests(unittest.TestCase):
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
