"""Independent NEAR copies with real local ledger and mocked Telegram transport."""
import json
from unittest.mock import Mock, patch
from test_postgres_support import PostgresTestCase, TestStore
from test_telegram_personal import INST, OWNER
from telegram_routing import deliver_routed
from telegram_delivery import DeliveryPending
from near_alerts import needed, plan

FRAMES=['5m','15m','30m','1h','4h','daily','weekly']

class RedundantRoutingTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        self.send=Mock(return_value=123)
        self.person=dict(user_id=OWNER,telegram_user_id=123,routes={'crypto.rsi':'-3','crypto.pin-bars':'-3'},enabled={},created_at=0)
        self.people=patch('telegram_personal.recipients',return_value=[self.person]);self.people.start();self.addCleanup(self.people.stop)
        with self.store.connect() as db:
            db.execute("INSERT INTO telegram_asset_subscriptions VALUES('-2','NEARUSD',%s,true,to_timestamp(1))",(FRAMES,))
            db.execute("INSERT INTO near_group_preferences(chat,subscriptions) VALUES('-2',%s::jsonb)",(json.dumps({'rsi':FRAMES,'trend-pin-bars':FRAMES}),))
            db.execute('INSERT INTO public.telegram_routes VALUES(%s,1,%s::jsonb)',(INST,json.dumps({'crypto.rsi':'-1','crypto.pin-bars':'-1','general':'-4'})))
    def rsi(self,frame='15m',event=None):
        p=dict(asset='NEARUSD',market='crypto',timeframe=frame,signal_status='provisional',alert_timing='pivot_close',confirmed_at=2000000,divergence_type='hidden')
        return deliver_routed(self.store,'token','-4','test',event or 'divergence:'+frame,self.send,payload=p)
    def pin(self,strategy='trend-pin-bars',setup='trend_pullback'):
        table={'trend-pin-bars':'trend_pin_bar_signals','pin-bars':'pin_bar_signals','bb-pin-bars':'bb_pin_bar_signals'}[strategy]
        p=dict(asset='NEARUSD',symbol='NEARUSD',market='crypto',strategy=strategy,timeframe='15m',confirmed_at=2000000,setup=setup)
        with self.store.connect() as db:
            db.execute(f'INSERT INTO {table} VALUES(%s,2000000,%s) ON CONFLICT DO NOTHING',('sample',json.dumps(p)))
        return deliver_routed(self.store,'token','-4','test','notification:'+strategy+':sample',self.send,notification_key=strategy+':sample')
    def chats(self):return [c.args[1] for c in self.send.call_args_list]
    def test_rsi_all_frames_and_retries_preserve_each_destination(self):
        for frame in FRAMES:
            self.send.reset_mock()
            self.rsi(frame);self.rsi(frame)
            self.assertCountEqual(self.chats(),['-2'] if frame=='5m' else ['-1','-2','-3'])
    def test_pin_strategies_and_no_duplicate_per_destination(self):
        for strategy in ('trend-pin-bars','pin-bars','bb-pin-bars'):
            self.send.reset_mock();self.pin(strategy);self.pin(strategy)
            self.assertCountEqual(self.chats(),['-1','-2','-3'] if strategy=='trend-pin-bars' else ['-1','-3'])
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT strategy FROM telegram_event_routes WHERE event=%s',('notification:'+strategy+':sample',)).fetchone()[0],strategy)
    def test_dedicated_opt_out_does_not_remove_other_copies(self):
        with self.store.connect() as db:
            db.execute("UPDATE near_group_preferences SET subscriptions='{}'")
            db.execute('UPDATE telegram_asset_subscriptions SET enabled=false')
        self.rsi();self.pin()
        self.assertCountEqual(self.chats(),['-1','-3','-1','-3'])
    def test_personal_opt_out_and_same_destination_dedup(self):
        self.person['routes']['crypto.rsi']='-2'
        self.rsi();self.assertCountEqual(self.chats(),['-1','-2'])
        self.person['routes']['crypto.rsi']='-3';self.person['enabled']['crypto.rsi']=False
        self.send.reset_mock();self.rsi(event='divergence:next')
        self.assertCountEqual(self.chats(),['-1','-2'])
    def test_dedicated_failure_does_not_block_personal_and_retry_dedups(self):
        def send(token,chat,*a,**kw):
            if chat=='-2':raise RuntimeError('transport failure')
            return 123
        self.send.side_effect=send
        with self.assertRaises(DeliveryPending):self.rsi()
        self.assertCountEqual(self.chats(),['-1','-2','-3'])
        self.send.reset_mock();self.send.side_effect=None
        with self.store.connect() as db:db.execute('UPDATE telegram_delivery SET locked_until=0')
        self.rsi();self.assertEqual(self.chats(),['-2'])
    def test_no_shared_destination_still_delivers_subscription_and_personal(self):
        with self.store.connect() as db:db.execute('DELETE FROM public.telegram_routes')
        p=dict(asset='NEARUSD',market='crypto',timeframe='15m',signal_status='provisional',alert_timing='pivot_close',confirmed_at=2000000,divergence_type='hidden')
        deliver_routed(self.store,'token','','test','divergence:missing',self.send,payload=p)
        self.assertCountEqual(self.chats(),['-2','-3'])
    def test_near_preferences_cannot_be_bypassed_by_personal_route(self):
        self.person['routes']['crypto.pin-bars']='-2'
        with self.store.connect() as db:db.execute("UPDATE near_group_preferences SET subscriptions='{}'")
        self.pin();self.assertEqual(self.chats(),['-1'])
    def test_retired_reversal_not_replayed_to_any_group(self):
        self.pin(setup='reversal');self.send.assert_not_called()
    def test_other_group_demand_survives_near_opt_out(self):
        state=dict(preferences=[('-2',{}, {})],subscriptions=[],services=[],routes={'crypto.rsi':'-1','crypto.pin-bars':'-1'},people=[])
        with patch.dict('os.environ',{},clear=True):
            for strategy in ('rsi','pin-bars','bb-pin-bars','trend-pin-bars'):
                self.assertTrue(needed(state,strategy,'15m'))
                self.assertFalse(needed(state,strategy,'5m'))
            wanted,resumes=plan(self.store,state,dict(id='NEARUSD',symbol='NEAR/USD'),'15m',['rsi','pin-bars','bb-pin-bars'],2000)
            self.assertEqual(set(wanted),{'rsi','pin-bars','bb-pin-bars','trend-pin-bars'})
            self.assertEqual(set(resumes.values()),{2000})
            self.assertFalse(needed(state|{'routes':{'crypto.rsi':'-2'}},'rsi','15m'))
