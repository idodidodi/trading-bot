import unittest
from unittest.mock import Mock, patch
from signal_notice_delivery import worker


class NoticeWorkerTests(unittest.TestCase):
    def test_worker_only_delivers_external_facts(self):
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch('chasing_runtime.deliver_pending') as deliver, patch('postgres_store.assert_worker'), patch('chasing_runtime.tick') as tick:
            worker('store', 'token', False, stop)
            deliver.assert_called_once_with('store', 'token', execution_only=True)
            tick.assert_not_called()

    def test_dry_run_and_missing_token_do_nothing(self):
        with patch('chasing_runtime.deliver_pending') as deliver:
            worker('store', 'token', True, Mock())
            worker('store', '', False, Mock())
            deliver.assert_not_called()
