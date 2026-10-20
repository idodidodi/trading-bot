"""Telegram groups divergence comparisons without merging dashboard findings."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from platform_app import Store, delivery_worker, signal_alert_event
from test_platform import example


@patch.dict('os.environ', {'DEV_TELEGRAM_ENABLED': 'true'}, clear=True)
class SignalAlertGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'signals.sqlite3')

    def run_worker(self, count, coordinator=None, send=None, dry_run=False):
        stop = Mock()
        stop.is_set.side_effect = [False] * count + [True]
        def coordinate(payload):
            return {'status': {'acquire': 'acquired', 'renew': 'acquired',
                               'complete': 'completed', 'release': 'released'}[payload['action']]}
        with patch('local_settings.delivery_dry_run', return_value=dry_run), \
             patch('telegram_delivery.coordinator', side_effect=coordinator or coordinate) as cloud, \
             patch('platform_app.send_telegram', send or Mock(return_value=42)) as sender:
            delivery_worker(self.store, '123:secret', '123', dry_run, stop)
        return sender, cloud

    def test_three_reference_pivots_send_once_and_keep_all_findings(self):
        for offset in range(3):
            self.store.insert(example() | {'pivot1': example()['pivot1'] + offset})
        send, _ = self.run_worker(3)
        send.assert_called_once()
        self.assertIn('see Signals for all reference-pivot comparisons', send.call_args.args[2])
        self.assertCountEqual([row[1] for row in self.store.rows()], ['sent', 'suppressed', 'suppressed'])
        self.assertEqual(len(self.store.rows()), 3)

    def test_identity_ignores_first_pivot_and_format_but_separates_each_group_dimension(self):
        base = example()
        event = signal_alert_event(base)
        self.assertEqual(event, signal_alert_event(base | {'pivot1': 1, 'price1': 2, 'rsi1': 3}))
        for changes in ({'symbol': 'OTHER'}, {'timeframe': 'weekly'}, {'direction': 'bearish'},
                        {'pivot2': base['pivot2'] + 1}, {'signal_status': 'provisional'}):
            with self.subTest(changes=changes):
                self.assertNotEqual(event, signal_alert_event(base | changes))

    def test_pasted_eleven_divergences_become_five_alerts(self):
        for symbol, direction, count in [('GHC', 'bearish', 3), ('ALK', 'bullish', 1),
                                         ('AEIS', 'bullish', 3), ('POR', 'bullish', 3),
                                         ('C', 'bullish', 1)]:
            for offset in range(count):
                self.store.insert(example() | {'symbol': 'alpaca:Alpaca-sip-split:' + symbol,
                    'direction': direction, 'timeframe': 'weekly',
                    'signal_status': 'provisional', 'pivot_closed_at': example()['confirmed_at'],
                    'pivot1': example()['pivot1'] + offset})
        send, _ = self.run_worker(11)
        self.assertEqual(send.call_count, 5)
        self.assertEqual(len(self.store.rows()), 11)
        self.assertEqual(sum(row[1] == 'suppressed' for row in self.store.rows()), 6)

    def test_provisional_and_confirmed_each_send_once(self):
        for stage in ('provisional', 'confirmed'):
            for offset in range(2):
                self.store.insert(example() | {'pivot1': example()['pivot1'] + offset,
                    'signal_status': stage, 'pivot_closed_at': example()['confirmed_at']})
        send, _ = self.run_worker(4)
        self.assertEqual(send.call_count, 2)
        self.assertIn('(provisional)', send.call_args_list[0].args[2])
        self.assertNotIn('(provisional)', send.call_args_list[1].args[2])

    def test_upgrade_and_restart_suppress_late_comparisons_for_sent_candle(self):
        self.store.insert(example(), delivery_status='sent')
        self.store = Store(self.store.path)
        self.store.insert(example() | {'pivot1': example()['pivot1'] + 1})
        send, cloud = self.run_worker(1)
        send.assert_not_called()
        cloud.assert_not_called()
        self.assertCountEqual([row[1] for row in self.store.rows()], ['sent', 'suppressed'])

    def test_two_devices_with_different_reference_pivots_share_one_cloud_event(self):
        completed = set()
        def coordinate(payload):
            key = payload['destination_hash'], payload['event_hash']
            if key in completed:
                return {'status': 'already_sent'}
            if payload['action'] == 'complete':
                completed.add(key)
                return {'status': 'completed'}
            return {'status': 'acquired', 'silent': False}
        self.store.insert(example())
        first, _ = self.run_worker(1, coordinator=coordinate)
        self.store = Store(Path(self.temp.name) / 'other.sqlite3')
        self.store.insert(example() | {'pivot1': example()['pivot1'] + 1})
        second, _ = self.run_worker(1, coordinator=coordinate)
        first.assert_called_once()
        second.assert_not_called()
        self.assertEqual(self.store.rows()[0][1], 'suppressed')

    def test_failed_group_stays_retryable_and_dry_run_does_not_contact_telegram(self):
        self.store.insert(example())
        self.run_worker(1, send=Mock(side_effect=RuntimeError('Delivery failed')))
        self.assertEqual(self.store.rows()[0][1], 'pending')
        with self.store.connect() as db:
            db.execute('UPDATE signals SET next_attempt=0')
        send, _ = self.run_worker(1)
        send.assert_called_once()
        self.assertEqual(self.store.rows()[0][1], 'sent')
        self.store.insert(example() | {'symbol': 'DRY'})
        send, cloud = self.run_worker(1, dry_run=True)
        send.assert_not_called()
        cloud.assert_not_called()
        self.assertIn('dry-run', [row[1] for row in self.store.rows()])


if __name__ == '__main__':
    unittest.main()
