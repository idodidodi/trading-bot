from test_postgres_support import TestStore as Store, PostgresTestCase
import json
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import market_summary as summary
from platform_app import delivery_worker


def clock(value):
    return datetime.fromisoformat(value).timestamp()


class MarketSummaryTests(PostgresTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'signals.sqlite3'
        self.store = Store(self.path)
        self.now = clock('2026-10-18T10:30:00+03:00')

    def queue(self, now=None):
        now = self.now if now is None else now
        with patch('market_summary.time.time', return_value=now):
            return summary.enqueue_due(self.store, now)

    def test_1030_local_time_across_dst_and_weekends(self):
        for day, offset in [('2026-07-18', '+03:00'), ('2026-12-18', '+02:00'),
                            ('2026-10-25', '+02:00'), ('2026-03-27', '+03:00')]:
            with self.subTest(day=day):
                before = clock(f'{day}T10:29:59{offset}')
                target = clock(f'{day}T10:30:00{offset}')
                self.assertEqual(summary.session(before)[1].timestamp(), target)
                with patch('market_summary.snapshot', return_value='Market prices') as fetch:
                    self.assertFalse(self.queue(before))
                    fetch.assert_not_called()
                    self.assertTrue(self.queue(target))

    def test_same_day_restart_deduplicates_and_next_day_gets_new_summary(self):
        with patch('market_summary.snapshot', return_value='Market prices') as fetch:
            self.assertTrue(self.queue())
            self.store = Store(self.path)
            self.assertFalse(self.queue(self.now + 3600))
            self.assertTrue(self.queue(self.now + 86400))
            self.assertEqual(fetch.call_count, 2)

    def test_afternoon_start_sends_today_only(self):
        with patch('market_summary.snapshot', return_value='Latest prices'):
            self.assertTrue(self.queue(self.now + 7200))
        with self.store.connect() as db:
            rows = db.execute('SELECT id FROM notification_queue').fetchall()
        self.assertEqual(rows, [('market-summary:2026-10-18',)])

    def test_all_failed_quotes_retry_without_consuming_day_and_partial_summary_works(self):
        with patch('market_summary.quote', side_effect=RuntimeError('private-provider-error')):
            with self.assertRaisesRegex(ValueError, 'All market-summary quotes unavailable'):
                self.queue()
        def fetch(instrument, now):
            if instrument[0] == '^GDAXI':
                raise RuntimeError('private-provider-error')
            return instrument[1] + ': 100'
        with patch('market_summary.quote', side_effect=fetch):
            self.assertTrue(self.queue())
        with self.store.connect() as db:
            text = db.execute('SELECT text FROM notification_queue').fetchone()[0]
        self.assertIn('DAX: unavailable', text)
        self.assertIn('WTI oil futures: 100', text)
        self.assertNotIn('private-provider-error', text)

    def test_midnight_during_fetch_does_not_queue_yesterdays_snapshot(self):
        with patch('market_summary.snapshot', return_value='prices'), \
             patch('market_summary.time.time', return_value=self.now + 86400):
            self.assertFalse(summary.enqueue_due(self.store, self.now))

    def test_quote_validation_units_precision_and_staleness(self):
        def response(meta):
            result = Mock()
            result.__enter__ = Mock(return_value=result)
            result.__exit__ = Mock(return_value=False)
            result.read.return_value = json.dumps({'chart': {'result': [{'meta': meta}]}}).encode()
            return result
        for instrument in summary.INSTRUMENTS:
            symbol, label, unit, currency = instrument
            meta = dict(symbol=symbol, currency=currency, regularMarketPrice=1.12345,
                        regularMarketTime=self.now - 86400)
            with patch('market_summary.urllib.request.urlopen', return_value=response(meta)) as request:
                text = summary.quote(instrument, self.now)
            self.assertIn(label + ':', text)
            self.assertIn(unit, text)
            self.assertIn('older quote', text)
            self.assertIn('17 Oct 10:30', text)
            if symbol == 'EURUSD=X':
                self.assertIn('1.12345', text)
            self.assertNotIn('apikey', request.call_args.args[0].full_url)
            for change in [dict(symbol='WRONG'), dict(currency='WRONG'),
                           dict(regularMarketPrice=float('nan')), dict(regularMarketPrice=0),
                           dict(regularMarketTime=None), dict(regularMarketTime=self.now+3600)]:
                with self.subTest(symbol=symbol, change=change):
                    with patch('market_summary.urllib.request.urlopen', return_value=response(meta | change)):
                        with self.assertRaises(ValueError):
                            summary.quote(instrument, self.now)

    @patch.dict(os.environ, {'TRADING_BOT_ENV': 'prod'})
    def test_existing_delivery_queue_retries_and_respects_dry_run(self):
        with patch('market_summary.snapshot', return_value='Morning prices'):
            self.queue()
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch('platform_app.send_telegram', side_effect=RuntimeError('Telegram connection failed')) as send:
            delivery_worker(self.store, 'test', '123', False, stop)
            send.assert_called_once()
        with self.store.connect() as db:
            row = db.execute('SELECT status,attempts FROM notification_queue').fetchone()
            db.execute('UPDATE notification_queue SET next_attempt=0')
        self.assertEqual(row, ('pending', 1))
        stop.is_set.side_effect = [False, True]
        with patch('platform_app.send_telegram') as send:
            delivery_worker(self.store, 'test', '123', True, stop)
        send.assert_not_called()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM notification_queue').fetchone()[0], 'dry-run')
        self.assertFalse(self.queue())

    def test_worker_reaches_boundary_without_waiting_for_candle_scan(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        with patch('market_summary.time.time', side_effect=[self.now-1, self.now]), \
             patch('market_summary.enqueue_due', return_value=True) as enqueue:
            summary.worker(self.store, stop)
        enqueue.assert_called_once_with(self.store, self.now)
        stop.wait.assert_any_call(1)
        stop.wait.assert_any_call(60)


if __name__ == '__main__':
    unittest.main()
