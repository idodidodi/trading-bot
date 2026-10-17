from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import fmp_feed
from managed_assets import ensure, get, validate_asset
from platform_app import Store, load_rules
from scanner import prepare_store, scan_once, timestamp
from test_scanner import fixture


class FMPTests(unittest.TestCase):
    asset = dict(id='SPX',provider='fmp',symbol='^GSPC',exchange='INDEX',market='index',
                 enabled=True,feed_confirmed=True,timeframes=['daily'])

    def row(self, day, symbol='^GSPC'):
        return dict(symbol=symbol,date=day,open=10,high=13,low=9,close=12)

    def test_current_day_excluded_and_no_intraday_interpolation(self):
        rows=[self.row('2026-10-16'), self.row('2026-10-15')]
        bars=fmp_feed.candles_from_rows(rows,'^GSPC','daily',timestamp('2026-10-16T21:00:00Z'))
        self.assertEqual(len(bars),1)
        self.assertEqual(bars[0].end,timestamp('2026-10-16T00:00:00Z'))
        with patch('fmp_feed.request_json') as request, self.assertRaisesRegex(ValueError,'intraday'):
            fmp_feed.fetch_fmp(self.asset,'1h')
        request.assert_not_called()

    def test_full_calendar_periods_and_truncated_leading_period(self):
        rows=[self.row(f'2026-09-{n:02}') for n in range(1,31)]
        monthly=fmp_feed.candles_from_rows(rows,'^GSPC','monthly',timestamp('2026-10-01T00:00:00Z'))
        self.assertEqual(len(monthly),1)
        self.assertEqual(monthly[0].end,timestamp('2026-10-01T00:00:00Z'))
        self.assertEqual(fmp_feed.candles_from_rows(rows,'^GSPC','monthly',timestamp('2026-09-30T23:59:00Z')),[])
        self.assertEqual(fmp_feed.candles_from_rows(rows[1:],'^GSPC','monthly',timestamp('2026-10-01T00:00:00Z')),[])
        self.assertEqual(fmp_feed.candles_from_rows([],'^GSPC','weekly',timestamp('2026-10-01T00:00:00Z')),[])

    def test_wrong_symbol_duplicate_and_invalid_prices_rejected(self):
        cutoff=timestamp('2026-10-17T00:00:00Z')
        for rows in ([self.row('2026-10-16','^GDAXI')],
                     [self.row('2026-10-16')]*2,
                     [self.row('2026-10-16')|dict(high=5)],
                     [self.row('2026-10-16')|dict(close=float('nan'))]):
            with self.assertRaises(ValueError):fmp_feed.candles_from_rows(rows,'^GSPC','daily',cutoff)

    def test_pagination_preserves_history_beyond_5000_rows(self):
        page=[self.row('2010-01-01')]*5000
        with patch('fmp_feed.request_json',side_effect=[page,[self.row('2000-01-03')]]) as request:
            rows=fmp_feed.daily_history('^GSPC','2000-01-01','2026-10-17')
        self.assertEqual(len(rows),5001)
        self.assertEqual(request.call_args.kwargs['to'],'2009-12-31')

    def test_subscription_error_safe_and_cache_scoped_to_credential(self):
        fmp_feed._cache.clear()
        with patch.dict(os.environ,FMP_API_KEY='private-key'), patch('fmp_feed.urllib.request.urlopen',side_effect=urllib.error.HTTPError('secret-url',402,'secret',{},None)):
            with self.assertRaisesRegex(ValueError,'subscription') as caught:fmp_feed.request_json('quote',symbol='^GDAXI')
        self.assertNotIn('secret',str(caught.exception))
        with patch.dict(os.environ,FMP_API_KEY='key-one'),patch('fmp_feed.urllib.request.urlopen',return_value=io.StringIO('[]')) as request:
            fmp_feed.request_json('quote',symbol='^GSPC');fmp_feed.request_json('quote',symbol='^GSPC')
            self.assertEqual(request.call_count,1)
        with patch.dict(os.environ,FMP_API_KEY='key-two'),patch('fmp_feed.urllib.request.urlopen',return_value=io.StringIO('[]')) as request:
            fmp_feed.request_json('quote',symbol='^GSPC');self.assertEqual(request.call_count,1)

    def test_validation_and_scanner_baseline_cache_identity(self):
        validate_asset(self.asset)
        with self.assertRaisesRegex(ValueError,'intraday'):validate_asset(self.asset|dict(timeframes=['4h']))
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('fmp_feed.fetch_fmp',return_value=fixture()):
                rows=scan_once(store,dict(assets=[self.asset],timeframes=['daily']),load_rules(),fixture()[-1].end,run_momentum=False)
            self.assertEqual(rows[0]['status'],'baseline set')
            self.assertEqual(rows[0]['provider'],'fmp')
            self.assertEqual(store.rows(),[])
            with store.connect() as db:self.assertEqual(db.execute('select distinct feed from candle_cache').fetchone()[0],'fmp:INDEX:^GSPC')

    def test_failed_activation_does_not_change_config(self):
        spec=importlib.util.spec_from_file_location('activate_fmp',Path(__file__).parent/'scripts/activate-fmp.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            ensure(store,dict(assets=[self.asset|dict(provider='csv',enabled=False)],timeframes=['daily']))
            before=get(store)
            with patch.object(module,'fetch_fmp',side_effect=ValueError('FMP subscription denied')),self.assertRaises(ValueError):module.activate(store)
            self.assertEqual(get(store),before)


    def test_successful_activation_preserves_dax_and_exports_history(self):
        from scanner import ProviderCandles
        spec=importlib.util.spec_from_file_location('activate_fmp',Path(__file__).parent/'scripts/activate-fmp.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            dax=dict(id='DAX',provider='csv',enabled=False,timeframes=['daily'])
            ensure(store,dict(assets=[self.asset|dict(provider='csv',enabled=False),dax],timeframes=['daily']))
            with patch.object(module,'ROOT',Path(folder)),patch.object(module,'fetch_fmp',return_value=ProviderCandles(fixture(),['test history'])):
                result=module.activate(store)
            config=get(store)['config']
            self.assertEqual(config['assets'][1]['enabled'],False)
            self.assertEqual(config['assets'][0]['provider'],'fmp')
            self.assertEqual(config['assets'][0]['timeframes'],['monthly','weekly','daily'])
            self.assertTrue(Path(result['backup']).exists())
            self.assertTrue((Path(folder)/'data/historical-fmp/SPX-monthly.csv').exists())
            from backtest_jobs import options
            self.assertIn('SPX',options(store)['fmp_assets'])

    def test_fmp_backtest_uses_separate_feed_without_live_alerts(self):
        from backtest import run_backtest
        from scanner import ProviderCandles
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');prepare_store(store)
            with patch('fmp_feed.fetch_fmp',return_value=ProviderCandles(fixture(),['FMP daily'])) as fetch:
                result=run_backtest(store,dict(year=2020,assets=[self.asset],timeframes=['daily']),load_rules())
            self.assertEqual(result['results'][0]['source']['provider'],'FMP')
            self.assertTrue(fetch.call_args.kwargs['end'].startswith('2021-01-01'))
            self.assertEqual(store.rows(),[])


if __name__=='__main__':unittest.main()
