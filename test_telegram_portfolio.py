"""Portfolio boundaries and routing without network or a production database."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import telegram_portfolio as portfolio
import telegram_routing as routing
from telegram_delivery import DeliveryPending
from postgres_store import WorkerOwned


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(enabled=True, chat_id='-5276778238',
                           assets=['CRWV', 'ALK', 'NVDA', 'ANT', 'AMAT', 'AUR'], enrolled_at=100)
        self.signal = dict(asset='NVDA', symbol='NVDA', timeframe='4h', confirmed_at=100000)

    def test_symbols_frames_and_enrollment(self):
        for asset in self.config['assets']:
            for frame in portfolio.FRAMES:
                self.assertTrue(portfolio.eligible(self.config, self.signal | dict(asset=asset, timeframe=frame), 'rsi'))
        for change in (dict(asset='NEARUSD'), dict(asset='NVDAX'), dict(timeframe='1h'),
                       dict(timeframe='5m'), dict(confirmed_at=99999), dict(confirmed_at=None)):
            self.assertFalse(portfolio.eligible(self.config, self.signal | change, 'rsi'))
        self.assertFalse(portfolio.eligible(self.config | dict(enabled=False), self.signal, 'rsi'))
        self.assertFalse(portfolio.eligible(self.config | dict(enrolled_at=0), self.signal, 'rsi'))

    def test_replacement_removes_old_and_preserves_retained_enrollment(self):
        config = self.config | dict(assets=['NVDA', 'MSFT'], asset_enrolled_at={'MSFT': 200})
        self.assertTrue(portfolio.eligible(config, self.signal, 'rsi'))
        self.assertFalse(portfolio.eligible(config, self.signal | dict(asset='AUR'), 'rsi'))
        self.assertFalse(portfolio.eligible(config, self.signal | dict(asset='MSFT'), 'rsi'))
        self.assertTrue(portfolio.eligible(config, self.signal | dict(asset='MSFT', confirmed_at=200000), 'rsi'))

    def test_all_notification_strategies_use_stored_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch('platform_app.ROOT', Path(directory)), \
                patch('telegram_delivery.deliver', return_value='sent') as deliver, \
                patch('telegram_checklist.group_dismissal_enabled', return_value=False):
            Path(directory, 'telegram-portfolio.json').write_text(json.dumps(self.config))
            store = Mock()
            db = store.connect.return_value.__enter__ = Mock(return_value=Mock())
            store.connect.return_value.__exit__ = Mock(return_value=False)
            db.return_value.execute.return_value.fetchone.return_value = (json.dumps(self.signal | dict(setup='trend_pullback')),)
            for strategy in portfolio.TABLES:
                event = 'notification:' + strategy + ':test'
                portfolio.deliver_portfolio(store, 'token', 'text', event, Mock(), notification_key=strategy + ':test')
                self.assertEqual(deliver.call_args.args[2], self.config['chat_id'])
                self.assertEqual(deliver.call_args.args[4], event)
            self.assertEqual(deliver.call_count, len(portfolio.TABLES))
            for key in ('telegram-demo:test', 'stock-report:test', 'unknown:test'):
                portfolio.deliver_portfolio(store, 'token', 'text', 'notification:' + key, Mock(), notification_key=key)
            self.assertEqual(deliver.call_count, len(portfolio.TABLES))

    def test_portfolio_failure_still_attempts_existing_groups(self):
        with patch('telegram_portfolio.deliver_portfolio', side_effect=RuntimeError('offline')), \
                patch('telegram_routing.select', return_value=('-1', '-1')), \
                patch('telegram_delivery.deliver', return_value='sent') as shared, \
                patch('telegram_personal.deliver_personal') as personal, \
                patch('telegram_checklist.group_dismissal_enabled', return_value=False):
            with self.assertRaises(DeliveryPending):
                routing.deliver_routed(Mock(), 'token', '-1', 'text', 'divergence:test', Mock())
            shared.assert_called_once()
            personal.assert_called_once()

    def test_worker_ownership_failure_propagates(self):
        with patch('telegram_portfolio.deliver_portfolio', side_effect=WorkerOwned('owned')), \
                patch('telegram_routing.select') as select:
            with self.assertRaises(WorkerOwned):
                routing.deliver_routed(Mock(), 'token', '-1', 'text', 'divergence:test', Mock())
            select.assert_not_called()


if __name__ == '__main__':
    unittest.main()
