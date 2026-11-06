import copy
from decimal import Decimal
import json
import time
import unittest
from unittest.mock import Mock, patch

import demo_execution as d
from unittest import TestCase as PostgresTestCase
from runtime_store import Store as TestStore
import tempfile
from pathlib import Path
import os

CONFIG=dict(version=1,allocation='.25',exit_distance='.01',max_signal_age_ms=300000,
            strategies=['chase-4h-5m','rsi-pb'],symbols={'NEARUSD':'NEARUSDT'})
ACCOUNT=dict(totalEquity='4000',totalAvailableBalance='4000',
             coin=[dict(coin='USDT',equity='4000',usdValue='4000')])
INSTRUMENT=dict(symbol='NEARUSDT',status='Trading',contractType='LinearPerpetual',
                quoteCoin='USDT',settleCoin='USDT',priceFilter=dict(tickSize='.01'),
                lotSizeFilter=dict(qtyStep='.1',minOrderQty='.1',maxMktOrderQty='10000',minNotionalValue='5'))
TICKER=dict(bid1Price='10',ask1Price='10')


def signal(now, frame='4h', **extra):
    return dict(symbol='NEARUSD',timeframe=frame,direction='bullish',pivot2=now-60000,
                confirmed_at=now,market='crypto',source='kraken:Kraken:NEAR/USD',version='rsi-pb-fixture-v1',**extra)


def trade(now):
    return d.intent('rsi-pb',signal(now),'NEARUSD','pb1',CONFIG,now-1000,now)


class PolicyTests(unittest.TestCase):
    def test_independent_strategies_and_chase_root_filter(self):
        now=1000000; root=signal(now-10000)
        self.assertIsNotNone(trade(now))
        self.assertIsNotNone(d.intent('chase-4h-5m',signal(now,'5m'),'NEARUSD','c',CONFIG,now-1000,now,root))
        for r,s in [(root|dict(timeframe='1h'),signal(now,'5m')),
                    (root,signal(now,'15m')),(root,signal(now,'5m')|dict(direction='bearish'))]:
            self.assertIsNone(d.intent('chase-4h-5m',s,'NEARUSD','c',CONFIG,now-1000,now,r))

    def test_no_old_future_or_pre_activation_entry_and_destinations_dedupe(self):
        now=1000000
        for at in (now+1,now-300001,now-1001):
            self.assertIsNone(d.intent('rsi-pb',signal(at),'NEARUSD','a',CONFIG,now-1000,now))
        a=d.intent('rsi-pb',signal(now),'NEARUSD','a',CONFIG,now-1000,now)
        b=d.intent('rsi-pb',signal(now),'NEARUSD','b',CONFIG,now-1000,now)
        self.assertEqual(a['id'],b['id'])

    def test_notional_sizing_rounds_down_without_leverage_and_exits_both_directions(self):
        p=d.plan(trade(1),ACCOUNT,INSTRUMENT,TICKER,[],[])
        self.assertEqual(p['qty'],'99.9')
        self.assertLessEqual(Decimal(p['qty'])*10,Decimal(1000))
        self.assertEqual(d.protection('10','Buy','.01'),dict(stopLoss='9.90',takeProfit='10.10'))
        self.assertEqual(d.protection('10','Sell','.01'),dict(stopLoss='10.10',takeProfit='9.90'))
        account=copy.deepcopy(ACCOUNT);account['coin'][0]['usdValue']='2000'
        self.assertEqual(d.plan(trade(1),account,INSTRUMENT,TICKER,[],[])['notional_cap'],'2000.0')

    def test_closed_contract_exposure_existing_symbol_and_orders_block(self):
        p=dict(symbol='OTHERUSDT',size='1',positionValue='3100',positionIdx=0)
        cases=[(ACCOUNT,INSTRUMENT|dict(status='Closed'),[],[]),
               (ACCOUNT,INSTRUMENT,[p],[]),
               (ACCOUNT,INSTRUMENT,[p|dict(symbol='NEARUSDT',positionValue='1')],[]),
               (ACCOUNT,INSTRUMENT,[],[dict(reduceOnly=False)]),
               (ACCOUNT|dict(totalAvailableBalance='500'),INSTRUMENT,[],[])]
        for a,i,positions,orders in cases:
            with self.assertRaises(ValueError):d.plan(trade(1),a,i,TICKER,positions,orders)
        d.plan(trade(1),ACCOUNT,INSTRUMENT,TICKER,[],[dict(reduceOnly=True,stopOrderType='StopLoss')])

    def test_pagination_and_repeated_cursor_fail_closed(self):
        b=d.Broker('key','secret');b._request=Mock(side_effect=[dict(list=[1],nextPageCursor='x'),dict(list=[2])])
        self.assertEqual(b.pages('/v5/order/realtime',{}),[1,2])
        self.assertEqual(b._request.call_args.args[1]['limit'],50)
        b._request=Mock(return_value=dict(list=[],nextPageCursor='x'))
        with self.assertRaises(d.ExecutionError):b.pages('/v5/position/list',{})

    def test_entry_attaches_protection_and_emergency_close_is_reduce_only(self):
        t=trade(1);t['plan']=d.plan(t,ACCOUNT,INSTRUMENT,TICKER,[],[])
        b=d.Broker('key','secret');b._request=Mock(return_value={})
        b.entry(t);body=b._request.call_args.kwargs['body']
        self.assertEqual(body['stopLoss'],'9.90');self.assertEqual(body['takeProfit'],'10.10')
        self.assertEqual(body['tpslMode'],'Full');self.assertFalse(body['reduceOnly'])
        b.flatten(t,'1');body=b._request.call_args.kwargs['body']
        self.assertTrue(body['reduceOnly']);self.assertEqual(body['side'],'Sell')
        self.assertLessEqual(len(body['orderLinkId']),36)


class DurableTests(PostgresTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=TestStore(Path(self.tmp.name)/'execution.sqlite3');self.store.acquire_worker();self.addCleanup(self.store.close)
        self.env=patch.dict(os.environ,EXECUTION_ENABLED='true');self.env.start();self.addCleanup(self.env.stop)
        self.now=int(time.time()*1000)
        with self.store.connect() as db:db.execute('UPDATE control SET enabled=1,reconciled=1,activated_at=?',(self.now-1000,))
        self.broker=Mock()
        self.broker.instrument.return_value=INSTRUMENT
        self.broker.account.return_value=ACCOUNT
        self.broker.positions.return_value=[]
        self.broker.orders.return_value=[]
        self.broker.ticker.return_value=TICKER
        self.broker.entry.return_value={'orderId':'order1'}
        with self.store.connect() as db:
            db.execute('INSERT INTO demo_execution_policy(id,revision,enabled,activated_at,config) VALUES(1,1,true,%s,%s)',(self.now-1000,json.dumps(CONFIG)))
        self.add_pb('pb1',self.now)

    def add_event(self, key, value, strategy='rsi-pb', live=True, instrument='NEARUSD'):
        with self.store.connect() as db:
            seq=db.execute('SELECT coalesce(max(cursor),0)+1 FROM inbox').fetchone()[0]
            event=dict(contract_version=1,event_id=key,signal_id=key,cursor=seq,strategy=strategy,
                strategy_version='fixture-v1',kind='detected',origin='live' if live else 'replay',
                delivery_mode='live' if live else 'replay',published_at=value['confirmed_at'],
                occurred_at=value['confirmed_at'],timeframe=value['timeframe'],direction=value['direction'],
                instrument=dict(symbol=instrument,source=value.get('source',value['symbol']),market='crypto'),evidence=value)
            db.execute('INSERT INTO inbox VALUES(?,?,?,?)',(key,seq,key,json.dumps(event)))
    def add_pb(self,key,at,notify=True):self.add_event(key,signal(at),live=notify)
    def records(self):
        with self.store.connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM demo_execution_trades ORDER BY id')]

    def test_intent_committed_before_write_and_timeout_never_resubmits(self):
        def timeout(t):
            self.assertEqual(self.records()[0]['state'],'submitting')
            raise d.SubmissionUnknown('timeout')
        self.broker.entry.side_effect=timeout
        with self.assertRaises(d.SubmissionUnknown):d.cycle(self.store,self.broker,self.now)
        self.broker.order.return_value=None
        self.add_pb('pb2',self.now+1000)
        d.cycle(self.store,self.broker,self.now+1000)
        self.assertEqual(self.broker.entry.call_count,1)
        self.assertEqual(self.records()[0]['state'],'unknown')

    def test_fill_verified_then_protection_and_no_duplicate_after_restart(self):
        d.cycle(self.store,self.broker,self.now)
        t=self.records()[0]
        self.broker.order.return_value=dict(orderStatus='Filled',cumExecQty=t['plan']['qty'],avgPrice='10')
        self.broker.positions.return_value=[dict(symbol='NEARUSDT',size=t['plan']['qty'],side='Buy',positionIdx=0,avgPrice='10',stopLoss='9.9',takeProfit='10.1')]
        d.cycle(self.store,self.broker,self.now+1000)
        self.assertEqual(self.records()[0]['state'],'protected')
        self.assertEqual(self.broker.entry.call_count,1)
        self.broker.protect.assert_not_called()

    def test_partial_fill_protection_failure_closes_once_even_after_timeout(self):
        d.cycle(self.store,self.broker,self.now)
        self.broker.order.return_value=dict(orderStatus='PartiallyFilledCanceled',cumExecQty='50',avgPrice='10.02')
        self.broker.positions.return_value=[dict(symbol='NEARUSDT',size='50',side='Buy',positionIdx=0,avgPrice='10.02',stopLoss='9.9',takeProfit='10.1')]
        self.broker.protect.side_effect=d.SubmissionUnknown('timeout')
        with self.assertRaises(d.SubmissionUnknown):d.cycle(self.store,self.broker,self.now+1000)
        self.broker.flatten.side_effect=d.SubmissionUnknown('timeout')
        with self.assertRaises(d.SubmissionUnknown):d.cycle(self.store,self.broker,self.now+2000)
        d.cycle(self.store,self.broker,self.now+3000)
        self.assertEqual(self.records()[0]['state'],'closing')
        self.assertEqual(self.broker.flatten.call_count,1)
        self.assertEqual(self.broker.protect.call_count,1)
        self.assertEqual(self.broker.entry.call_count,1)

    def test_policy_disabled_catchup_old_and_unmapped_never_enter(self):
        with self.store.connect() as db:db.execute('DELETE FROM inbox')
        d.cycle(self.store,self.broker,self.now)
        self.broker.entry.assert_not_called()
        self.add_pb('pb2',self.now+1)
        with self.store.connect() as db:db.execute('UPDATE demo_execution_policy SET enabled=false,revision=2')
        d.cycle(self.store,self.broker,self.now+1)
        self.broker.entry.assert_not_called()

    def test_revision_change_during_broker_reads_prevents_entry(self):
        def quote(s):
            with self.store.connect() as db:db.execute('UPDATE demo_execution_policy SET revision=2,enabled=false')
            return TICKER
        self.broker.ticker.side_effect=quote
        d.cycle(self.store,self.broker,self.now)
        self.broker.entry.assert_not_called()
        self.assertEqual(self.records(),[])

    def test_flat_position_requires_two_spaced_observations(self):
        d.cycle(self.store,self.broker,self.now)
        self.broker.order.return_value=dict(orderStatus='Filled',cumExecQty='99.9',avgPrice='10')
        d.cycle(self.store,self.broker,self.now+1000)
        self.assertEqual(self.records()[0]['state'],'pending')
        d.cycle(self.store,self.broker,self.now+31000)
        self.assertEqual(self.records()[0]['state'],'closed')
        self.assertEqual(self.broker.entry.call_count,1)

    def test_effect_gate_blocks_all_broker_requests(self):
        with self.store.connect() as db:db.execute('UPDATE control SET enabled=0')
        with self.assertRaises(RuntimeError):d.cycle(self.store,self.broker,self.now)
        self.broker.entry.assert_not_called();self.broker.account.assert_not_called()

    def test_invalidation_prevents_entry(self):
        with self.store.connect() as db:
            raw=db.execute('SELECT payload FROM inbox').fetchone()[0]
            event=json.loads(raw);event.update(kind='invalidated',event_id='invalid',cursor=2)
            db.execute('INSERT INTO inbox VALUES(?,?,?,?)',('invalid',2,event['signal_id'],json.dumps(event)))
        d.cycle(self.store,self.broker,self.now)
        self.broker.entry.assert_not_called()

    def test_stale_after_broker_read_does_not_submit(self):
        with patch('demo_execution.time.time',return_value=(self.now+300001)/1000):
            d.cycle(self.store,self.broker,self.now)
        self.broker.entry.assert_not_called()

    def test_actual_chase_only_five_minute_four_hour_root_is_candidate(self):
        with self.store.connect() as db:db.execute('DELETE FROM inbox')
        value=signal(self.now,'5m');value['root']=signal(self.now-10000)|dict(symbol='kraken:Kraken:NEAR/USD')
        self.add_event('chase',value,'chase-4h-5m')
        d.cycle(self.store,self.broker,self.now)
        self.assertEqual(self.records()[0]['strategy'],'chase-4h-5m')
        self.assertEqual(self.broker.entry.call_count,1)


class DivergenceExitTests(PostgresTestCase):
    setUp = DurableTests.setUp
    add_pb = DurableTests.add_pb
    add_event = DurableTests.add_event
    records = DurableTests.records

    def add_divergence(self, at, **changes):
        value=dict(symbol='kraken:Kraken:NEAR/USD',timeframe='4h',direction='bearish',
                   confirmed_at=at,pivot2=at-60000,pivot1=at-120000,signal_status='provisional')|changes
        self.add_event(str(at)+json.dumps(changes,sort_keys=True),value,'rsi')
        return value

    def filled(self, strategy='rsi-pb', side='Buy'):
        d.cycle(self.store,self.broker,self.now)
        t=self.records()[0]
        if strategy=='chase-4h-5m':
            t.update(strategy=strategy,root=t['signal']|dict(symbol='kraken:Kraken:NEAR/USD'),
                     signal=t['signal']|dict(timeframe='5m'))
        t['side']=side
        d.save(self.store,t,'submitted')
        self.broker.order.return_value=dict(orderStatus='Filled',cumExecQty=t['plan']['qty'],avgPrice='10',updatedTime=str(self.now+1000))
        levels=d.protection('10',side,'.01')
        self.broker.positions.return_value=[dict(symbol='NEARUSDT',size=t['plan']['qty'],side=side,positionIdx=0,avgPrice='10',**levels)]
        d.cycle(self.store,self.broker,self.now+1000)
        return self.records()[0]

    def test_wrong_frame_asset_direction_preentry_and_future_do_not_close(self):
        self.filled('chase-4h-5m')
        self.add_divergence(self.now+2000,timeframe='5m')
        self.add_divergence(self.now+2000,symbol='kraken:Kraken:SOL/USD')
        self.add_divergence(self.now+2000,direction='bullish')
        self.add_divergence(self.now+1000)
        self.add_divergence(self.now+4000)
        d.cycle(self.store,self.broker,self.now+3000)
        self.broker.flatten.assert_not_called()
        self.assertEqual(self.records()[0]['state'],'protected')
        self.add_divergence(self.now+3000)
        d.cycle(self.store,self.broker,self.now+3000)
        self.broker.flatten.assert_called_once()
        self.assertEqual(self.records()[0]['exit_divergence']['timeframe'],'4h')

    def test_short_pb_closes_remaining_owned_size_and_disabled_entries_still_exit(self):
        self.filled(side='Sell')
        self.broker.positions.return_value[0]['size']='50'
        self.add_divergence(self.now+2000,direction='bullish')
        with self.store.connect() as db:db.execute('UPDATE demo_execution_policy SET enabled=false')
        d.cycle(self.store,self.broker,self.now+3000)
        self.assertEqual(self.broker.flatten.call_args.args[1],Decimal('50'))
        self.assertEqual(self.records()[0]['exit_reason'],'opposite_divergence')

    def test_close_intent_and_alert_commit_before_timeout_and_never_resubmit(self):
        self.filled()
        self.add_divergence(self.now+2000)
        def timeout(t,size):
            self.assertEqual(self.records()[0]['state'],'closing')
            with self.store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM notifications").fetchone()[0],1)
            raise d.SubmissionUnknown('timeout')
        self.broker.flatten.side_effect=timeout
        with self.assertRaises(d.SubmissionUnknown):d.cycle(self.store,self.broker,self.now+3000)
        self.broker.order.side_effect=lambda t,close=False: None if close else dict(orderStatus='Filled',cumExecQty='99.9',avgPrice='10',updatedTime=str(self.now+1000))
        d.cycle(self.store,self.broker,self.now+4000)
        self.assertEqual(self.broker.flatten.call_count,1)
        self.assertEqual(self.records()[0]['state'],'closing')
        # A transient empty position / missing entry lookup must not clear intent.
        position=self.broker.positions.return_value
        self.broker.positions.return_value=[]
        d.cycle(self.store,self.broker,self.now+4500)
        self.broker.positions.return_value=position
        d.cycle(self.store,self.broker,self.now+4600)
        self.assertEqual(self.broker.flatten.call_count,1)
        self.broker.positions.return_value=[]
        d.cycle(self.store,self.broker,self.now+5000)
        d.cycle(self.store,self.broker,self.now+35000)
        self.assertEqual(self.records()[0]['state'],'closed')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM notifications').fetchone()[0],1)

    def test_modified_or_reversed_position_is_not_closed(self):
        self.filled()
        self.add_divergence(self.now+2000)
        for changes in (dict(size='1000'),dict(avgPrice='11'),dict(side='Sell'),dict(positionIdx=1)):
            original=self.broker.positions.return_value[0].copy()
            self.broker.positions.return_value[0].update(changes)
            d.cycle(self.store,self.broker,self.now+3000)
            self.broker.flatten.assert_not_called()
            self.broker.positions.return_value=[original]

    def test_pb_only_consumes_exported_divergence(self):
        trade=self.filled()
        found=self.add_divergence(self.now+2000)
        self.assertEqual(d.opposing_divergence(self.store,trade,self.now+3000),found)
