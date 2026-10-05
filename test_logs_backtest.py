from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from backtest import START, END, replay, run_backtest
from platform_app import Store, handler_factory, load_rules
from scanner import prepare_store, scan_once
from test_scanner import fixture


class TabsTests(unittest.TestCase):
    def test_replay_year_boundaries_and_future_independence(self):
        bars = fixture()
        delta = START + 14400000 - bars[258].end
        bars = [replace(c, start=c.start+delta, end=c.end+delta) for c in bars]
        found = replay(bars, load_rules(), 'TEST', '4h')['signals']
        self.assertEqual(len(found), 1)
        self.assertTrue(START <= found[0]['confirmed_at'] < END)
        future = replace(bars[-1], start=END+1, end=END+14400001)
        self.assertEqual(replay(bars+[future], load_rules(), 'TEST', '4h')['signals'], found)
        before = [replace(c, start=c.start-28800000, end=c.end-28800000) for c in bars]
        self.assertEqual(replay(before, load_rules(), 'TEST', '4h')['signals'], [])

    def test_tabs_http_logs_and_backtest_queue_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'test.sqlite3')
            prepare_store(store)
            config = {'assets':[{'id':'TEST','provider':'csv','path':str(Path(folder)/'missing.csv')}], 'timeframes':['4h']}
            scan_once(store, config, load_rules())
            run_backtest(store, config, load_rules())
            self.assertEqual(store.rows(), [])
            server = ThreadingHTTPServer(('127.0.0.1',0), handler_factory(store,load_rules(),'test',True,dashboard=True))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for path, expected in [('/', 'Backtest 2020'),('/logs','Scan cycle completed'),('/backtest','unavailable')]:
                    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}') as response:
                        self.assertEqual(response.status,200)
                        self.assertIn(expected,response.read().decode())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
