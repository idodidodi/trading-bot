import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from managed_assets import ensure, get
from platform_app import Store
from scanner import prepare_store
from test_scanner import fixture

spec=importlib.util.spec_from_file_location('portfolio_feeds',Path(__file__).parent/'scripts/activate-portfolio-feeds.py')
feeds=importlib.util.module_from_spec(spec);spec.loader.exec_module(feeds)


class PortfolioFeedsTests(unittest.TestCase):
    def test_candidates_preserve_oil_and_other_assets_and_explicit_oil_opt_in(self):
        config=dict(assets=[dict(id='ES',provider='csv',enabled=False,timeframes=['daily']),
                            dict(id='USOIL',provider='tiingo',symbol='USOIL/USD',timeframes=['daily'])])
        result=feeds.candidates(config)
        self.assertEqual([a['id'] for a in result],['ES','XAUUSD','FETUSD'])
        self.assertEqual(result[0]['timeframes'],['daily'])
        self.assertEqual(result[2]['timeframe_providers']['weekly'],'kraken')
        self.assertEqual(result[2]['provider'],'twelvedata')
        oil=feeds.candidates(config,True)[-1]
        self.assertEqual((oil['id'],oil['symbol'],oil['market']),('USOIL','CL=F','futures'))
        self.assertEqual(config['assets'][1]['symbol'],'USOIL/USD')

    def test_activation_verifies_before_mutating_and_uses_each_selected_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            ensure(store,dict(assets=[dict(id='ES',provider='csv',enabled=False,timeframes=['daily']),
                                     dict(id='USOIL',provider='csv',enabled=False,timeframes=['daily'])]))
            before=get(store)
            with patch.object(feeds,'fetch_yahoo',side_effect=ValueError('Unavailable')),self.assertRaises(ValueError):
                feeds.activate(store)
            self.assertEqual(get(store),before)
            with patch.object(feeds,'time') as clock,patch.object(feeds,'fetch_yahoo',return_value=fixture()),\
                 patch.object(feeds,'fetch_tiingo',return_value=fixture()),\
                 patch.object(feeds,'fetch_kraken',return_value=fixture()) as kraken,\
                 patch.object(feeds,'fetch_twelve_data',return_value=fixture()) as twelve:
                clock.time.return_value=1792750000;clock.time_ns.return_value=1792750000000000000
                result=feeds.activate(store)
            self.assertTrue(Path(result['backup']).exists())
            assets={a['id']:a for a in get(store)['config']['assets']}
            self.assertEqual(assets['USOIL'],before['config']['assets'][1])
            self.assertEqual(assets['XAUUSD']['symbol'],'XAU/USD')
            self.assertEqual(kraken.call_count,3);self.assertEqual(twelve.call_count,1)
            self.assertEqual(twelve.call_args.args[1],'monthly')
            with store.connect() as db:
                self.assertEqual({r[0] for r in db.execute('select distinct feed from candle_cache')},
                                 {'yahoo:CME:ES=F','tiingo:Tiingo-UTC:XAU/USD','kraken:Kraken:FET/USD',
                                  'twelvedata:Binance:FET/USD'})


if __name__=='__main__':unittest.main()
