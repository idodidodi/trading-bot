import tempfile
import unittest
from pathlib import Path
from platform_app import Store
from test_platform import example
from finding_feedback import finding_id
from finding_state import change
from dashboard_api import read
from cloud_sync import apply, batch


class FindingStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'signals.sqlite3')
        self.signal = example()
        self.store.insert(self.signal)
        self.key = finding_id('live', self.signal)

    def change(self, action, revision=0):
        return change(self.store,self.root,dict(source='live',finding_id=self.key,action=action,revision=revision))

    def test_archive_restore_delete_and_deduplication(self):
        self.change('archive')
        self.assertEqual(read(self.store,'/api/findings')['findings'],[])
        archived=read(self.store,'/api/findings?archived=true')['findings']
        self.assertTrue(archived[0]['archived'])
        with self.assertRaises(LookupError): self.change('restore')
        self.change('restore',1)
        self.assertFalse(read(self.store,'/api/findings')['findings'][0]['archived'])
        self.change('delete',2)
        self.store.insert(self.signal)
        self.assertEqual(read(self.store,'/api/findings?archived=true')['findings'],[])
        with self.assertRaises(LookupError): self.change('restore',3)
        self.assertEqual(len(self.store.rows()),1)

    def test_filtering_before_pagination(self):
        for i in range(55):
            signal=self.signal|dict(pivot1=self.signal['pivot1']+i+1)
            self.store.insert(signal)
            change(self.store,self.root,dict(source='live',finding_id=finding_id('live',signal),action='archive',revision=0))
        visible=read(self.store,'/api/findings')
        self.assertEqual([r['id'] for r in visible['findings']],[self.key])
        self.assertFalse(visible['has_more'])
        self.assertTrue(read(self.store,'/api/findings?archived=true')['has_more'])

    def test_sync_cloud_wins_equal_revision_and_persists(self):
        self.change('archive')
        event=batch(self.store)['records'][0]['event_id']
        remote=dict(finding_id=self.key,status='deleted',revision=1,updated_at=1)
        response=dict(finding_states=[dict(payload=remote,change_sequence=7)])
        apply(self.store,response)
        self.assertEqual(batch(self.store)['state_cursor'],0)
        apply(self.store,response|dict(ack=[event]))
        self.assertEqual(batch(self.store)['state_cursor'],7)
        self.assertEqual(batch(self.store)['records'],[])
        self.assertEqual(read(Store(self.root/'signals.sqlite3'),'/api/findings?archived=true')['findings'],[])

    def test_invalid_actions(self):
        for action in ('invalid',None):
            with self.assertRaises(ValueError): self.change(action)
        with self.assertRaises(ValueError): self.change('archive',True)

    def test_http_action_origin_and_route(self):
        import json
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from platform_app import handler_factory, load_rules
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_factory(self.store,load_rules(),'',True,dashboard=True))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        body=json.dumps(dict(source='live',finding_id=self.key,action='archive',revision=0)).encode()
        try:
            for origin in ('https://example.invalid',url):
                req=urllib.request.Request(url+'/api/finding-state',data=body,headers={'Content-Type':'application/json','Origin':origin})
                if origin==url:
                    with urllib.request.urlopen(req) as response:self.assertEqual(json.load(response)['status'],'archived')
                else:
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
                    self.assertEqual(caught.exception.code,403)
            with urllib.request.urlopen(url+'/api/findings') as response:self.assertEqual(json.load(response)['findings'],[])
        finally:
            server.shutdown();server.server_close();thread.join()
