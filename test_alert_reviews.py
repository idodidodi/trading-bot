"""Older queued clock reviews must never be delivered again."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from platform_app import Store, delivery_worker
from test_platform import example


class ReviewRetirementTests(unittest.TestCase):
    @patch.dict('os.environ', {'DEV_TELEGRAM_ENABLED': 'true'}, clear=True)
    def test_cancel_clock_review_but_deliver_candle_event(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'test.sqlite3')
            setup = example()
            store.insert(setup | {'review_slot': '2026-10-07 07:00'})
            store.insert(setup)
            stop = Mock()
            stop.is_set.side_effect = [False, False, True]
            def coordinate(payload):
                return {'status': 'completed' if payload['action']=='complete' else 'acquired'}
            with patch('telegram_delivery.coordinator', side_effect=coordinate), patch('platform_app.send_telegram', return_value=42) as send:
                delivery_worker(store, 'test-token', '123', False, stop)
            send.assert_called_once()
            self.assertCountEqual([row[1] for row in store.rows()], ['cancelled', 'sent'])


if __name__ == '__main__':
    unittest.main()
