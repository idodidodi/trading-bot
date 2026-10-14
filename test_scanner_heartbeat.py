import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from platform_app import Store
from scanner import next_heartbeat_at, prepare_store, scanner_heartbeat, scanner_worker


class ScannerHeartbeatTests(unittest.TestCase):
    def test_wall_clock_schedule_before_at_and_after_quarter_hour(self):
        self.assertEqual(next_heartbeat_at(899), 900)
        self.assertEqual(next_heartbeat_at(900), 4500)
        self.assertEqual(next_heartbeat_at(901), 4500)
        self.assertEqual(next_heartbeat_at(86399), 87300)

    def test_one_log_at_boundary_even_during_scan_and_no_missed_hour_flood(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'signals.sqlite3')
            prepare_store(store)
            with store.connect() as db:
                db.execute('INSERT INTO scanner_status(id,payload) VALUES(1,?)',
                           (json.dumps({'phase': 'scanning'}),))
            finished = Mock()
            finished.is_set.side_effect = [False, False, False, True]
            with patch('scanner.time.time', side_effect=[899, 899, 900, 900, 8100, 8100]):
                scanner_heartbeat(store, finished)
            with store.connect() as db:
                logs = db.execute('SELECT event,details FROM activity_log').fetchall()
            self.assertEqual(len(logs), 2)
            self.assertTrue(all(event == 'Scanner heartbeat' for event, _ in logs))
            self.assertTrue(all('scanning' in details and 'Israel time' in details for _, details in logs))

    def test_heartbeat_ends_when_scanner_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'signals.sqlite3')
            finished_events = []
            def heartbeat(store, finished):
                finished_events.append(finished)
                finished.wait(2)
            with patch('scanner.scanner_heartbeat', side_effect=heartbeat), \
                 patch('scanner._scanner_worker', side_effect=RuntimeError('scan failed')):
                with self.assertRaisesRegex(RuntimeError, 'scan failed'):
                    scanner_worker(store, {}, {}, threading.Event())
            self.assertEqual(len(finished_events), 1)
            self.assertTrue(finished_events[0].is_set())


if __name__ == '__main__':
    unittest.main()
