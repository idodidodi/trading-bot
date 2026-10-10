import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_app import Store
import paper_trading as p

NOW=1791811800000

def signal(market='stock',direction='buy'):
    return dict(market=market,symbol='PCTY' if market=='stock' else 'BTC/USD',direction=direction,confirmed_at=NOW-86400000,
                version='test',entry_open_at=NOW,expires_at=NOW+86400000,entry_min=95,entry_max=105,stop=90 if direction=='buy' else 110,
                target=125 if direction=='buy' else 75,rules=dict(max_hold=10))

ACCOUNT=dict(id='paper-test',status='ACTIVE',equity='100000',buying_power='400000',non_marginable_buying_power='100000',shorting_enabled=True)
ASSET=dict(status='active',tradable=True,shortable=True,**{'class':'us_equity'},min_order_size='.00001',min_trade_increment='.00000001')
QUOTE=dict(t='2026-10-12T13:30:00Z',bp=99.99,ap=100)

class FakeBroker:
    def __init__(self):self.orders={};self.positions=[];self.post_count=0;self.fail_after_post=False;self.quote_data=QUOTE.copy();self.clock=True
    def lookup(self,key):return self.orders.get(key)
    def quote(self,s):return self.quote_data
    def request(self,path,method='GET',body=None,data=False):
        if path=='/v2/account':return ACCOUNT.copy()
        if path=='/v2/positions':return self.positions.copy()
        if path.startswith('/v2/clock'):return dict(is_open=self.clock)
        if path.startswith('/v2/assets/'):
            return ASSET|{'class':'crypto' if '%' in path else 'us_equity'}
        if path.startswith('/v2/orders?'):return [o for o in self.orders.values() if o['status'] not in p.TERMINAL]
        if method=='POST':
            self.post_count+=1
            o=body|dict(id='order-'+body['client_order_id'],status='new',filled_qty='0',filled_avg_price=None)
            self.orders[body['client_order_id']]=o
            if self.fail_after_post:raise TimeoutError()
            return o
        key=path.split('/')[-1].split('?')[0]
        order=next(o for o in self.orders.values() if o['id']==key)
        if method=='DELETE':order['status']='canceled'
        return order

class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.env=patch.dict(os.environ,PAPER_TRADING_ENABLED='true',PAPER_RISK_PCT='1',PAPER_MAX_NOTIONAL='10000');self.env.start()
        self.broker=FakeBroker();self.signals=[signal()]
        self.source=patch('momentum.view',side_effect=lambda *a,**kw:dict(signals=self.signals));self.source.start()
    def tearDown(self):self.source.stop();self.env.stop();self.tmp.cleanup()
    def run_tick(self,now=NOW):return p.tick(self.store,self.broker,now)['trades']
    def test_sizing_and_stock_bracket(self):
        order,size=p.plan(signal(),ACCOUNT,ASSET,QUOTE,NOW)
        self.assertEqual(order['order_class'],'bracket');self.assertLessEqual(size['risk_dollars'],1000)
        self.assertLessEqual(size['notional'],10000);self.assertEqual(float(order['qty']),int(float(order['qty'])))
        self.assertEqual(order['stop_loss'],dict(stop_price='90.00'))
    def test_crypto_short_and_quote_gaps_rejected(self):
        for s,q in [(signal('crypto','sell'),QUOTE),(signal(),QUOTE|dict(ap=110,bp=109.99)),(signal(),QUOTE|dict(t='2026-10-12T13:28:00Z'))]:
            with self.assertRaises(ValueError):p.plan(s,ACCOUNT,ASSET,q,NOW)
    def test_scheduled_no_closed_market_order_and_missed_window(self):
        rows=self.run_tick(NOW-1000);self.assertEqual(rows[0]['state'],'scheduled');self.assertEqual(self.broker.post_count,0)
        rows=self.run_tick(NOW+p.ENTRY_WINDOW_MS);self.assertEqual(rows[0]['state'],'skipped');self.assertEqual(self.broker.post_count,0)
    def test_ambiguous_post_reconciles_without_duplicate_after_restart(self):
        self.broker.fail_after_post=True
        self.run_tick();self.assertEqual(self.broker.post_count,1)
        self.broker.fail_after_post=False
        rows=self.run_tick(NOW+15000);self.assertEqual(self.broker.post_count,1);self.assertEqual(rows[0]['state'],'entry pending')
    def test_unconfirmed_submission_blocks_other_candidate(self):
        self.run_tick();self.broker.orders.clear();self.signals=[signal()|dict(symbol='OTHER')]
        rows=self.run_tick(NOW+15000);self.assertEqual(self.broker.post_count,1)
        self.assertTrue(any(r['state']=='submission unknown' for r in rows))
    def test_cancel_unfilled_entry_and_protect_partial_fill(self):
        self.run_tick();o=next(iter(self.broker.orders.values()))
        rows=self.run_tick(NOW+p.ENTRY_WINDOW_MS);self.assertEqual(rows[0]['state'],'closed unfilled')
        self.assertEqual(o['status'],'canceled')
    def test_partial_stock_fill_without_bracket_closes_safely(self):
        self.run_tick();o=next(iter(self.broker.orders.values()));o.update(status='partially_filled',filled_qty='2',filled_avg_price='100')
        self.broker.positions=[dict(symbol='PCTY',qty='2',asset_class='us_equity')]
        rows=self.run_tick(NOW+15000);self.assertEqual(o['status'],'canceled');self.assertEqual(rows[0]['state'],'exit pending')
        self.assertEqual(self.broker.post_count,2);self.assertEqual(rows[0]['exit_order']['qty'],'2.0')
    def test_existing_manual_position_blocks_entry(self):
        self.broker.positions=[dict(symbol='OTHER',qty='2',asset_class='us_equity')]
        self.assertEqual(self.run_tick()[0]['state'],'skipped');self.assertEqual(self.broker.post_count,0)
    def test_crypto_buy_stop_and_target(self):
        self.signals=[signal('crypto')];self.run_tick();o=next(iter(self.broker.orders.values()))
        o.update(status='filled',filled_qty='50',filled_avg_price='100')
        self.broker.positions=[dict(symbol='BTCUSD',qty='50',asset_class='crypto')]
        rows=self.run_tick(NOW+15000);self.assertEqual(rows[0]['state'],'open')
        stop=self.broker.orders[rows[0]['id']+'-stop'];self.assertEqual(stop['type'],'stop_limit')
        self.broker.quote_data=QUOTE|dict(bp=126,ap=126.01,t='2026-10-12T13:30:30Z')
        rows=self.run_tick(NOW+30000);self.assertEqual(stop['status'],'canceled');self.assertEqual(rows[0]['state'],'exit pending')
        self.assertEqual(self.broker.post_count,3)
        exit=self.broker.orders[rows[0]['id']+'-exit'];exit.update(status='filled',filled_qty='50',filled_avg_price='126')
        self.assertEqual(self.run_tick(NOW+45000)[0]['state'],'closed');self.assertEqual(self.broker.post_count,3)
    def test_crypto_stop_installed_even_if_exit_quote_service_fails(self):
        self.signals=[signal('crypto')];self.run_tick();o=next(iter(self.broker.orders.values()))
        o.update(status='filled',filled_qty='50',filled_avg_price='100')
        self.broker.positions=[dict(symbol='BTCUSD',qty='50',asset_class='crypto')]
        with patch.object(self.broker,'quote',side_effect=TimeoutError()):
            rows=self.run_tick(NOW+15000)
        self.assertEqual(self.broker.post_count,2);self.assertEqual(rows[0]['protective_orders'][0]['side'],'sell')
    def test_position_reversal_does_not_place_wrong_direction_exit(self):
        self.run_tick();o=next(iter(self.broker.orders.values()))
        o.update(status='filled',filled_qty='2',filled_avg_price='100')
        self.broker.positions=[dict(symbol='PCTY',qty='-2',asset_class='us_equity')]
        rows=self.run_tick(NOW+15000);self.assertEqual(self.broker.post_count,1)
        self.assertIn('direction changed',rows[0]['reason'])
    def test_paper_host_cannot_be_overridden(self):
        import io
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return b'{}'
        with patch.dict(os.environ,ALPACA_API_KEY='dummy',ALPACA_SECRET_KEY='dummy',ALPACA_BASE_URL='https://api.alpaca.markets'),patch('urllib.request.urlopen',return_value=Response()) as send:
            p.AlpacaPaper().request('/v2/orders','POST',{})
            self.assertEqual(send.call_args[0][0].full_url,'https://paper-api.alpaca.markets/v2/orders')
    def test_changed_account_cannot_execute_scheduled_entry(self):
        self.run_tick(NOW-1000)
        with patch.dict(ACCOUNT,id='other-paper'):
            rows=self.run_tick();self.assertEqual(rows[0]['state'],'skipped');self.assertEqual(self.broker.post_count,0)

if __name__=='__main__':unittest.main()
