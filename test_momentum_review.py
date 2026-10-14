import unittest

from momentum_review import group_metrics, paper_metrics


class MomentumReviewTests(unittest.TestCase):
    def test_replay_metrics_are_separated_by_market_and_version(self):
        signals = [
            {'market': 'stock', 'version': 'v1', 'outcome': {'status': 'target', 'net_pct': 2.0}},
            {'market': 'stock', 'version': 'v1', 'outcome': {'status': 'stop', 'net_pct': -1.0}},
            {'market': 'crypto', 'version': 'v2', 'outcome': {'status': 'open'}},
        ]

        by_market = group_metrics(signals, 'market')
        by_stock_version = group_metrics([s for s in signals if s['market'] == 'stock'], 'version')

        self.assertEqual(by_market['stock']['closed_trades'], 2)
        self.assertEqual(by_market['stock']['average_net_pct'], 0.5)
        self.assertEqual(by_market['crypto']['outcome_states'], {'open': 1})
        self.assertEqual(by_stock_version['v1']['outcome_states'], {'stop': 1, 'target': 1})

    def test_paper_summary_keeps_gross_fills_separate_from_skips(self):
        trades = [
            {'signal': {'market': 'stock', 'version': 'v1'}, 'state': 'closed', 'gross_return_pct': 0.25},
            {'signal': {'market': 'stock', 'version': 'v1'}, 'state': 'skipped'},
        ]

        result = paper_metrics(trades)

        self.assertEqual(result['stock/v1']['closed_fills'], 1)
        self.assertEqual(result['stock/v1']['average_gross_pct'], 0.25)
        self.assertEqual(result['stock/v1']['states'], {'closed': 1, 'skipped': 1})


if __name__ == '__main__':
    unittest.main()
