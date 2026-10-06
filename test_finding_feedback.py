import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from dashboard_pages import render_page
from finding_feedback import export, finding_id, save
from platform_app import Store, handler_factory, load_rules, validate_signal
from test_platform import example


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.signal = validate_signal(example(), load_rules())
        self.store.insert(self.signal)
        self.body = dict(source='live', finding_id=finding_id('live', self.signal),
                         rating=4, comment='Good setup <script>alert(1)</script>', revision=0)
        self.root = Path(self.temp.name)
        self.report = dict(results=[dict(asset='EURUSD', timeframe='daily', status='replayed', signals=[self.signal])])
        saved = self.root / 'data/historical-2020'
        saved.mkdir(parents=True)
        (saved / 'report.json').write_text(json.dumps(self.report))

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_history_and_version_identity(self):
        self.assertEqual(save(self.store, self.root, self.body)['revision'], 1)
        reopened = Store(self.store.path)
        self.assertEqual(export(reopened)['feedback_history'][0]['rating'], 4)
        save(reopened, self.root, self.body | dict(revision=1, rating=None, comment='New opinion'))
        history = export(reopened)['feedback_history']
        self.assertEqual([h['revision'] for h in history], [1, 2])
        self.assertEqual(history[0]['evidence'], self.signal)
        self.assertNotEqual(finding_id('live', self.signal), finding_id('backtest', self.signal))
        changed = self.signal | {'rules': self.signal['rules'] | {'rsi_period': 5}}
        self.assertNotEqual(finding_id('live', changed), self.body['finding_id'])
        with self.assertRaises(LookupError):
            save(reopened, self.root, self.body)

    def test_saved_report_and_database_report_match_without_live_inserts(self):
        body = self.body | dict(source='backtest', finding_id=finding_id('backtest', self.signal))
        save(self.store, self.root, body)
        with self.store.connect() as db:
            db.execute('CREATE TABLE backtest_runs(id INTEGER PRIMARY KEY, report TEXT)')
            db.execute('INSERT INTO backtest_runs(report) VALUES(?)', (json.dumps(self.report),))
        save(self.store, self.root, body | dict(revision=1, rating=5))
        page = render_page(self.store, '/backtest')
        self.assertIn('Feedback</th>', page)
        self.assertIn('data-rating="5" aria-label="5 stars" aria-pressed="true"', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertEqual(len(self.store.rows()), 1)
        self.assertEqual(self.store.rows()[0][1], 'pending')

    def test_validation_rolls_back_and_rejects_unknown_findings(self):
        for change in [dict(rating=0), dict(rating=6), dict(rating=True), dict(rating=1.5),
                       dict(comment='x'*4001), dict(comment=None), dict(revision=True)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                save(self.store, self.root, self.body | change)
        with self.assertRaises(LookupError):
            save(self.store, self.root, self.body | dict(finding_id='0'*64))
        self.assertEqual(export(self.store)['feedback_history'], [])

    def test_http_origin_validation_save_and_render(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(self.store, load_rules(), '', True, dashboard=True))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            for origin in ['https://evil.example', None, url]:
                headers = {'Content-Type':'application/json'}
                if origin: headers['Origin'] = origin
                request = urllib.request.Request(url+'/api/feedback', data=json.dumps(self.body).encode(), headers=headers)
                if origin == url:
                    with urllib.request.urlopen(request) as response:
                        self.assertTrue(json.load(response)['saved'])
                else:
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(request)
                    self.assertEqual(caught.exception.code, 403)
            with urllib.request.urlopen(url) as response:
                page = response.read().decode()
                self.assertIn('data-rating="4" aria-label="4 stars" aria-pressed="true"', page)
                self.assertIn('&lt;script&gt;', page)
                self.assertNotIn('http-equiv="refresh"', page)
            with urllib.request.urlopen(url+'/api/feedback/export') as response:
                self.assertEqual(len(json.load(response)['feedback_history']), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
