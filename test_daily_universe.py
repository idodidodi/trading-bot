import copy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from platform_app import Store
from daily_universe import POLICY, configure, rank, resolve, session, seconds_to_refresh


def instant(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Jerusalem')).timestamp()*1000)


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'test.db')
        self.config = configure(dict(assets=[], timeframes=['4h']))
        self.selected = {'forex':[dict(symbol=s,volume_usd=10) for s in ['AUD/USD','USD/CAD','EUR/GBP','EUR/CHF','EUR/AUD']],
                         'crypto':[dict(symbol=s,volume_usd=10) for s in ['XRP/USD','ADA/USD','AVAX/USD','LINK/USD','LTC/USD']]}

    def test_core_and_existing_preserved(self):
        original = dict(assets=[dict(id='NVDA',provider='csv',enabled=False)],timeframes=['daily'])
        configured = configure(original)
        self.assertEqual(configured['assets'][0],original['assets'][0])
        self.assertEqual(len(configured['assets']),11)
        self.assertEqual(len(configure(configured)['assets']),11)
        self.assertTrue({'NEARUSD','DOGEUSD'} <= {a['id'] for a in configured['assets']})
        self.assertEqual(len(original['assets']),1)

    def test_boundary_and_dst(self):
        self.assertEqual(session(POLICY,instant('2026-10-06T07:59:59'))[0],'2026-10-05')
        self.assertEqual(session(POLICY,instant('2026-10-06T08:00:00'))[0],'2026-10-06')
        for date in ['2026-01-06','2026-07-06']:
            self.assertEqual(seconds_to_refresh(POLICY,instant(date+'T07:00:00')/1000),3600)

    @patch('daily_universe.rank')
    @patch('daily_universe.ranking_inputs', return_value=({}, {}, {}))
    def test_frozen_restart_rollover(self, inputs, ranked):
        ranked.return_value=self.selected
        first,state=resolve(self.store,self.config,instant('2026-10-06T08:00:00'))
        self.assertEqual(len(first['assets']),20)
        restarted=Store(self.store.path)
        same,again=resolve(restarted,self.config,instant('2026-10-07T07:59:00'))
        self.assertEqual(state,again)
        inputs.assert_called_once()
        resolve(restarted,self.config,instant('2026-10-07T08:00:00'))
        self.assertEqual(inputs.call_count,2)

    @patch('daily_universe.rank')
    @patch('daily_universe.ranking_inputs', return_value=({}, {}, {}))
    def test_failure_does_not_reuse_yesterday_and_recovers(self, inputs, ranked):
        ranked.return_value=self.selected
        resolve(self.store,self.config,instant('2026-10-06T08:00:00'))
        inputs.side_effect=OSError('private provider failure')
        config,state=resolve(self.store,self.config,instant('2026-10-07T08:00:00'))
        self.assertEqual(len(config['assets']),10)
        self.assertEqual(state['status'],'unavailable')
        self.assertNotIn('private',str(state))
        resolve(self.store,self.config,instant('2026-10-07T08:10:00'))
        self.assertEqual(inputs.call_count,2)
        inputs.side_effect=None
        _,state=resolve(self.store,self.config,instant('2026-10-07T12:00:00'))
        self.assertEqual(state['status'],'selected')

    def test_usd_turnover_not_coin_units_and_filters(self):
        pairs={};tickers={}
        for s,vol,price in [('BTC/USD',1,100),('XRP/USD',200,0.1),('ADA/USD',10,3),('USDT/USD',99999,1),('BAD/USD',1,float('nan')),('EUR/USD',100,2),('EUR/GBP',100,0.5),('GBP/USD',1,4)]:
            pairs[s]=dict(wsname=s,status='online')
            tickers[s]=dict(v=[0,str(vol)],p=[0,str(price)],c=[str(price)])
        supported={'forex':set(pairs),'crypto':set(pairs)}
        ranked=rank(pairs,tickers,supported,{'BTCUSD','GBPUSD'},5)
        self.assertEqual([r['symbol'] for r in ranked['crypto']],['ADA/USD','XRP/USD'])
        self.assertEqual([r['volume_usd'] for r in ranked['forex']],[200,200])
        self.assertEqual([r['symbol'] for r in ranked['forex']],['EUR/GBP','EUR/USD'])

    @patch('daily_universe.rank',return_value={'forex':[], 'crypto':[]})
    @patch('daily_universe.ranking_inputs',return_value=({}, {}, {}))
    def test_insufficient_ranking_is_visible(self, *_):
        config,state=resolve(self.store,self.config,instant('2026-10-06T08:00:00'))
        self.assertEqual(state['status'],'unavailable')
        self.assertEqual(len(config['assets']),10)

if __name__=='__main__':unittest.main()
