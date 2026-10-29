from test_postgres_support import TestStore as Store, PostgresTestCase
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from managed_assets import ensure, get
from market_feeds import candidates, merge, verify
from scanner import prepare_store
from test_scanner import fixture

spec = importlib.util.spec_from_file_location('activate_markets', Path(__file__).parent/'scripts/activate-market-feeds.py')
activation = importlib.util.module_from_spec(spec); spec.loader.exec_module(activation)


class MarketFeedsTests(PostgresTestCase):
    def config(self):
        return dict(timeframes=['monthly','weekly','daily','4h'], assets=[
            dict(id='SPX',provider='csv',enabled=False,timeframes=['daily','4h','1h'],
                 scan_strategies=['rsi','pin-bars'],unavailable_reason='not configured'),
            dict(id='NVDA',provider='alpaca',symbol='NVDA',enabled=True),
            dict(id='ES',provider='csv',enabled=False,timeframes=['daily'])])

    def test_targeted_merge_preserves_other_assets_and_existing_timeframes(self):
        config = self.config(); before = copy.deepcopy(config)
        assets = candidates(config); updated = merge(config,assets)
        self.assertEqual(config,before)
        self.assertEqual(updated['assets'][1:3],config['assets'][1:3])
        self.assertEqual(assets[0]['timeframes'],['daily','4h','1h'])
        self.assertEqual(assets[0]['scan_strategies'],['rsi','pin-bars'])
        self.assertNotIn('unavailable_reason',assets[0])
        self.assertEqual({a['id'] for a in assets},{'SPX','DAX','DOLLAR_INDEX','XAUUSD','USOIL'})
        self.assertTrue(all(a['enabled'] and a['feed_confirmed'] for a in assets))
        self.assertEqual(len({a['id'] for a in merge(updated,assets)['assets']}),len(updated['assets']))

    def test_activation_checks_all_history_before_mutation_and_backups_saved_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/'db'); prepare_store(store)
            ensure(store,self.config()); before = get(store)
            with patch('tiingo_feed.fetch_tiingo',side_effect=ValueError('Unavailable')),\
                 patch('yahoo_feed.fetch_yahoo',return_value=fixture()),self.assertRaises(ValueError):
                activation.activate(store)
            self.assertEqual(get(store),before)
            with patch('tiingo_feed.fetch_tiingo',return_value=fixture()),\
                 patch('yahoo_feed.fetch_yahoo',return_value=fixture()):
                result = activation.activate(store)
            self.assertTrue(Path(result['backup']).exists())
            saved = get(store)
            self.assertEqual(saved['revision'],before['revision']+1)
            self.assertEqual(saved['config']['assets'][1:3],before['config']['assets'][1:3])
            with store.connect() as db:
                feeds = {r[0] for r in db.execute('select distinct feed from candle_cache')}
            self.assertEqual(feeds,{'yahoo:SNP:^GSPC','yahoo:XETRA:^GDAXI','yahoo:NYB:DX-Y.NYB',
                                    'tiingo:Tiingo-UTC:XAU/USD','tiingo:Tiingo-UTC:USOIL/USD'})

    def test_short_history_still_cannot_generate_indicator_signals(self):
        assets = candidates(self.config())
        with patch('yahoo_feed.fetch_yahoo',return_value=fixture()[:10]),\
             self.assertRaisesRegex(ValueError,'configuration unchanged'):
            verify(assets,fixture()[-1].end)


if __name__ == '__main__': unittest.main()
