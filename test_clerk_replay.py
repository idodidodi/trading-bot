import sys
import unittest
from unittest.mock import patch
from clerk_dashboard_api import Gateway


class ReplayProcessTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway('host=127.0.0.1 dbname=unused user=dashboard_gateway',
            'https://clerk.example.com', ['https://dashboard.example.com'],
            'dddddddd-dddd-4ddd-8ddd-dddddddddddd')

    @unittest.skipIf(sys.platform == 'win32', 'Production replay process uses POSIX resource limits')
    def test_real_subprocess_computes_csv_without_database(self):
        csv = 'timestamp,open,high,low,close\n' + ''.join(
            f'{1609459200+i*86400},100,102,98,101\n' for i in range(80))
        report = self.gateway.replay_csv({'csv':csv,'symbol':'FIXTURE','timeframe':'daily','strategy':'confirmed'})
        self.assertEqual(report['symbol'],'FIXTURE')
        self.assertEqual(report['strategy'],'confirmed')
        self.assertIn('signals',report)

    def test_busy_slot_and_timeout_do_not_leave_replay_locked(self):
        import subprocess
        self.gateway.csv_slot.acquire()
        with self.assertRaises(ValueError): self.gateway.replay_csv({})
        self.gateway.csv_slot.release()
        with patch('clerk_dashboard_api.subprocess.run',side_effect=subprocess.TimeoutExpired('replay',12)):
            with self.assertRaises(ValueError): self.gateway.replay_csv({})
        self.assertTrue(self.gateway.csv_slot.acquire(blocking=False))
        self.gateway.csv_slot.release()
