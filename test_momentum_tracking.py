import json
import tempfile
import unittest
from pathlib import Path
from platform_app import Store
from paper_trading import initialize as initialize_paper
from momentum_tracking import acknowledge,refresh

DAY=86400000


class TrackingTests(unittest.TestCase):
    def test_failed_entry_gets_separate_daily_simulation_and_one_time_close_event(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'tracking.sqlite3')
            signal=dict(market='crypto',symbol='BTC/USD',direction='buy',confirmed_at=10*DAY-1,published_at=10*DAY-1,
                        entry_open_at=10*DAY,expires_at=11*DAY,entry=100,entry_min=97,entry_max=103,stop=90,target=120,
                        rules=dict(max_hold=2,cost_bps=10))
            bars=[dict(start=10*DAY,end=11*DAY,open=100,high=121,low=95,close=115)]
            with store.connect() as db:
                initialize_paper(db)
                db.execute('CREATE TABLE momentum_history(market TEXT,symbol TEXT,payload TEXT,PRIMARY KEY(market,symbol))')
                db.execute('INSERT INTO momentum_history VALUES(?,?,?)',('crypto','BTC/USD',json.dumps(bars)))
                record=dict(id='mom-test',signal=signal,state='skipped',reason='Temporary paper connection failure after 3 attempts',
                            entry_quote=dict(price=101,source='Alpaca crypto quote',timestamp=10*DAY),account='paper')
                db.execute('INSERT INTO paper_trades VALUES(?,?)',(record['id'],json.dumps(record)))
            result=refresh(store,now=12*DAY)
            track=result['tracking'][0]
            self.assertEqual(track['kind'],'simulated_tracking')
            self.assertEqual(track['name'],'BTC/USD')
            self.assertEqual(track['simulated_entry_price'],100)
            self.assertEqual(track['outcome_status'],'target')
            self.assertEqual(track['exit']['reference_price'],120)
            self.assertAlmostEqual(track['tracking_return_pct'],19.8)
            self.assertEqual({e['kind'] for e in result['events']},{'entry_failure','tracking_close'})
            acknowledge(store,'mom-test','entry_failure',now=12*DAY)
            acknowledge(store,'mom-test','tracking_close',now=12*DAY)
            self.assertEqual(refresh(store,now=12*DAY)['events'],[])


if __name__=='__main__':unittest.main()
