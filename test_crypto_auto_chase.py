import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

from crypto_chase_policy import POLICY
from divergence_chasing import create, step
from test_divergence_chasing import signal
from test_postgres_support import PostgresTestCase, TestStore

USER='00000000-0000-0000-0000-000000000001'
INST='00000000-0000-0000-0000-000000000002'
SYMBOL='kraken:Kraken:BTC/USD'
START=1800000000000
MINUTE=60000


def crypto(frame='4h',at=START,direction='bullish'):
    return signal(frame,at,direction)|dict(symbol=SYMBOL,market='crypto',signal_status='provisional',rules={})


@patch('chasing_runtime.CHASING_SUSPENDED',False)
class DescendingTests(unittest.TestCase):
    def watch(self,frame='4h'):
        return create(USER,crypto(frame),START,watch_policy=POLICY)

    def test_every_descent_notifies_and_only_five_minutes_completes(self):
        state=self.watch()
        original=json.loads(json.dumps(state))
        for minutes,frame in [(1,'1h'),(2,'30m'),(3,'15m'),(4,'5m')]:
            at=START+minutes*MINUTE
            state,events=step(state,at,signals=[crypto(frame,at)])
            self.assertEqual([e['signal']['timeframe'] for e in events],[frame])
            self.assertEqual(state['stage'],frame)
            self.assertEqual(state['status'],'complete_first' if frame=='5m' else 'chasing')
        self.assertEqual(len(state['chain']),4)
        self.assertEqual(original['chain'],[])
        self.assertEqual(step(state,START+5*MINUTE,signals=[crypto('5m',START+5*MINUTE)])[1],[])

    def test_skipped_stages_and_same_higher_opposite_or_old_cannot_replace(self):
        state=self.watch()
        at=START+MINUTE
        state,_=step(state,at,signals=[crypto('15m',at)])
        deadline=state['expires_at']
        at+=MINUTE
        state,events=step(state,at,signals=[crypto('1h',at),crypto('30m',at),crypto('15m',at),
                                         crypto('5m',at,'bearish'),crypto('5m',START)])
        self.assertEqual(events,[])
        self.assertEqual((state['stage'],state['expires_at']),('15m',deadline))

    def test_no_price_drawdown_gate_or_favorable_price_cancellation(self):
        state=self.watch()
        at=START+MINUTE
        state,events=step(state,at,price=110,price_at=at,signals=[crypto('1h',at)])
        self.assertEqual(state['status'],'chasing')
        self.assertEqual(events[0]['kind'],'chase')

    def test_one_hour_exact_fifty_five_minute_expiry_without_market_data(self):
        state=self.watch('1h')
        self.assertEqual(state['expires_at'],START+55*MINUTE)
        state,events=step(state,START+55*MINUTE-1)
        self.assertEqual((state['status'],events),('chasing',[]))
        state,events=step(state,START+55*MINUTE,signals=[crypto('5m',START+55*MINUTE)])
        self.assertEqual(state['status'],'expired')
        self.assertEqual([e['kind'] for e in events],['expired'])
        self.assertIsNone(state['next_due'])

    def test_one_hour_descent_sets_fifty_five_minute_limit(self):
        state=self.watch()
        at=START+MINUTE
        state,_=step(state,at,signals=[crypto('1h',at)])
        self.assertEqual(state['expires_at'],at+55*MINUTE)
        state,_=step(state,at+MINUTE,signals=[crypto('15m',at+MINUTE)])
        self.assertEqual(state['expires_at'],at+11*MINUTE)

    def test_provider_publication_delay_does_not_lose_new_lower_divergence(self):
        state=self.watch()
        state,_=step(state,START+2*MINUTE,signals=[])
        state,events=step(state,START+3*MINUTE,signals=[crypto('15m',START+MINUTE)])
        self.assertEqual(state['stage'],'15m')
        self.assertEqual(len(events),1)
        self.assertEqual(step(state,START+4*MINUTE,signals=[crypto('15m',START+MINUTE)])[1],[])

    def test_simultaneous_pairs_deduplicate_and_chronological_stages_are_reported(self):
        state=self.watch()
        at=START+MINUTE
        state,events=step(state,at,signals=[crypto('5m',at),crypto('30m',at),crypto('1h',at),crypto('1h',at)])
        self.assertEqual([e['signal']['timeframe'] for e in events],['1h','30m','5m'])
        self.assertEqual(state['status'],'complete_first')

    def test_higher_originals_start_crypto_only_and_expired_entry_is_rejected(self):
        for frame in ('monthly','weekly','daily'):
            state=self.watch(frame)
            state,events=step(state,START+MINUTE,signals=[crypto('1h',START+MINUTE)])
            self.assertEqual(state['stage'],'1h')
        state=self.watch('1h')
        state,events=step(state,START+55*MINUTE,enter=True)
        self.assertEqual(state['status'],'expired')
        self.assertIsNone(state['entry_at'])

    def test_lookback_reports_intermediate_findings_and_monotonically_adopts_five_minutes(self):
        from chase_lookback import replay,request,finish
        from chasing_runtime import text_for
        from scanner import Candle
        rows=[Candle(START+i*MINUTE,START+(i+1)*MINUTE,100,101,99,100) for i in range(3)]
        state=self.watch()
        one=crypto('1h',START+MINUTE)
        result=replay(state,START+3*MINUTE,rows,[one])
        self.assertEqual(result['status'],'found_intermediate')
        self.assertEqual([s['timeframe'] for s in result['findings']],['1h'])
        pending,_=request(state,START+3*MINUTE,'one')
        progressed,event=finish(pending,result,START+3*MINUTE)
        self.assertEqual((progressed['stage'],progressed['status']),('1h','chasing'))
        self.assertEqual(progressed['expires_at'],START+56*MINUTE)
        self.assertNotIn('No eligible',text_for(progressed,event))
        live,_=step(state,START+2*MINUTE,signals=[crypto('15m',START+2*MINUTE)])
        pending,_=request(live,START+3*MINUTE,'no-rewind')
        unchanged,_=finish(pending,result,START+3*MINUTE)
        self.assertEqual((unchanged['stage'],unchanged['expires_at']),('15m',live['expires_at']))
        five=crypto('5m',START+2*MINUTE)
        result=replay(state,START+3*MINUTE,rows,[one,five])
        self.assertEqual(result['signal']['timeframe'],'5m')
        pending,_=request(progressed,START+3*MINUTE,'five')
        completed,_=finish(pending,result,START+3*MINUTE)
        self.assertEqual((completed['stage'],completed['status'],completed['next_due']),('5m','complete_first',None))


@patch('chasing_runtime.CHASING_SUSPENDED',False)
class SharedDataTests(unittest.TestCase):
    def setUp(self):
        from shared_kraken import candles,divergences
        candles.clear();divergences.clear()

    def test_simultaneous_fetches_and_scanner_chase_consume_one_snapshot(self):
        from shared_kraken import fetch
        from chasing_runtime import market
        from scanner import Candle
        asset=dict(symbol='BTC/USD',exchange='Kraken',feed_confirmed=True)
        entered=Event();release=Event();calls=[]
        def provider(asset,frame):
            calls.append(frame)
            if frame=='1h':entered.set();self.assertTrue(release.wait(5))
            return [Candle(START-3600000,START,100,101,99,100)]
        with patch('kraken_feed.fetch_kraken',side_effect=provider),patch('scanner.detect',return_value=[]):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(fetch,asset,'1h',START)
                self.assertTrue(entered.wait(5))
                second=pool.submit(fetch,asset,'1h',START)
                release.set()
                self.assertIs(first.result(),second.result())
            market(crypto()|dict(_search_stage='4h'),START)
        self.assertEqual(calls.count('1h'),1)
        self.assertEqual(set(calls),{'1h','30m','15m','5m'})

    def test_divergence_results_reused_and_revised_candles_recalculated(self):
        from shared_kraken import detect
        from scanner import Candle
        rows=[Candle(START-MINUTE,START,100,101,99,100)]
        with patch('scanner.detect',return_value=[]) as engine:
            detect(rows,{},SYMBOL,'5m',START);detect(rows,{},SYMBOL,'5m',START)
            self.assertEqual(engine.call_count,1)
            detect([Candle(START-MINUTE,START,100,102,99,101)],{},SYMBOL,'5m',START)
            detect(rows,{'rsi_period':3},SYMBOL,'5m',START)
            self.assertEqual(engine.call_count,3)

    def test_delayed_empty_and_failed_responses_retry_without_duplicate_immediate_requests(self):
        from shared_kraken import fetch
        asset=dict(symbol='BTC/USD',exchange='Kraken',feed_confirmed=True)
        with patch('kraken_feed.fetch_kraken',return_value=[]) as provider:
            fetch(asset,'1h',START);fetch(asset,'1h',START+1000)
            self.assertEqual(provider.call_count,1)
            fetch(asset,'1h',START+15000)
            self.assertEqual(provider.call_count,2)
        from shared_kraken import candles
        candles.clear()
        with patch('kraken_feed.fetch_kraken',side_effect=ValueError('unavailable')) as provider:
            for at in (START,START+1000):
                with self.assertRaises(ValueError):fetch(asset,'1h',at)
            self.assertEqual(provider.call_count,1)


@patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
@patch('chasing_runtime.CHASING_SUSPENDED',False)
class RuntimeTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        quote=patch('chasing_runtime.current_price',return_value=dict(price=123.45,at=1200000))
        quote.start();self.addCleanup(quote.stop)
        self.person=dict(user_id=USER,telegram_user_id=72,chasing=True,live_positions=True,crypto_auto_chase=True,
                         routes={'chasing':'-10001','live_positions':'-10002','crypto_auto_chase':'-10003'},
                         stop_pct=.5,revision=1,created_at=0,auto_created_at=0)

    def enroll(self,frame='4h',at=START,auto=True):
        from chasing_runtime import enroll
        with patch('chasing_runtime.time.time',return_value=at/1000):
            return enroll(self.store,crypto(frame,at),self.person,automatic=auto)

    def active(self):
        with self.store.connect() as db:
            return [r[0] for r in db.execute("SELECT state FROM chasing_sessions WHERE state->>'status'='chasing'")]

    def test_automatic_and_manual_share_watch_lower_priority_and_timer(self):
        original=self.enroll()
        manual=self.enroll(auto=False)
        self.assertEqual(original['id'],manual['id'])
        lower=self.enroll('1h',START+MINUTE,auto=False)
        self.assertEqual(lower['id'],original['id'])
        self.assertEqual(lower['stage'],'1h')
        self.assertEqual(lower['expires_at'],START+56*MINUTE)
        higher=self.enroll('4h',START+2*MINUTE)
        self.assertEqual((higher['id'],higher['stage'],higher['expires_at']),
                         (lower['id'],'1h',lower['expires_at']))
        self.assertEqual(len(self.active()),1)
        self.assertEqual(set(self.active()[0]['destinations']),{'chasing','crypto_auto_chase'})

    def test_new_higher_original_starts_fresh_after_expiry(self):
        from chasing_runtime import tick
        previous=self.enroll('1h')
        tick(self.store,START+55*MINUTE,lambda *a:self.fail('Expiry must not fetch data'))
        current=self.enroll('4h',START+60*MINUTE)
        self.assertNotEqual(previous['id'],current['id'])
        self.assertEqual(self.active()[0]['stage'],'4h')

    def test_delayed_lower_original_still_takes_priority_without_replaying_old_signals(self):
        from chasing_runtime import enroll,tick
        self.enroll()
        tick(self.store,START+2*MINUTE,lambda *a:(None,None,[]))
        with patch('chasing_runtime.time.time',return_value=(START+3*MINUTE)/1000):
            result=enroll(self.store,crypto('1h',START+MINUTE),self.person,automatic=True)
        self.assertEqual(result['stage'],'1h')
        self.assertEqual(result['expires_at'],START+58*MINUTE)

    def test_concurrent_enrollment_is_one_watch_with_one_started_per_destination(self):
        from chasing_runtime import enroll
        with patch('chasing_runtime.time.time',return_value=START/1000),ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda automatic:enroll(self.store,crypto(),self.person,automatic=automatic),[True,False,True,False]))
        self.assertEqual(len({s['id'] for s in results}),1)
        self.assertEqual(len(self.active()),1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='started'").fetchone()[0],2)

    def test_each_descent_delivers_once_to_both_destinations_and_stops_at_five(self):
        from chasing_runtime import tick,deliver_pending
        self.enroll();self.enroll(auto=False)
        calls=[]
        def api(token,method,body):
            if method=='sendMessage':calls.append(body)
            return dict(chat=dict(id=int(body.get('chat_id',-10003))),message_id=len(calls))
        with patch('chasing_runtime.people',return_value=[self.person]),patch('telegram_checklist.api',side_effect=api):
            deliver_pending(self.store,'fake')
            for n,frame in enumerate(('1h','30m','15m','5m'),1):
                at=START+n*MINUTE
                tick(self.store,at,lambda *a:(None,None,[crypto(frame,at)]))
                deliver_pending(self.store,'fake');deliver_pending(self.store,'fake')
            tick(self.store,START+10*MINUTE,lambda *a:self.fail('Completed watch must not fetch'))
        self.assertTrue(all('Current price: 123.45' in c['text'] for c in calls))
        self.assertEqual(len(calls),10)
        self.assertEqual(sum(str(c['chat_id'])=='-10003' for c in calls),5)
        self.assertEqual(self.active(),[])

    def test_simultaneous_matches_have_distinct_durable_delivery_keys(self):
        from chasing_runtime import tick
        self.enroll();self.enroll(auto=False)
        at=START+MINUTE
        tick(self.store,at,lambda *a:(None,None,[crypto(f,at) for f in ('5m','15m','30m','1h')]))
        with self.store.connect() as db:
            events=db.execute("SELECT event FROM chasing_outbox WHERE event->>'kind'='chase'").fetchall()
        self.assertEqual(len(events),8)
        self.assertEqual({e[0]['signal']['timeframe'] for e in events},{'5m','15m','30m','1h'})

    def test_old_bound_button_joins_lower_watch_and_entry_does_not_cancel_automatic(self):
        from chasing_runtime import callback,chase_buttons
        from telegram_personal import prepare_message,bind_message
        original=self.enroll()
        at=START+MINUTE
        lower=self.enroll('1h',at)
        alias=create(USER,crypto('1h',at),at,watch_policy=POLICY)['id']
        item=prepare_message(self.store,'fake','-10001','original',[],72)
        item.extra_keyboard=[[dict(text="I'm in",callback_data='chase:enter:'+alias)]]+chase_buttons(alias)
        bind_message(self.store,item,dict(chat=dict(id=-10001),message_id=42))
        query=dict(id='start',data='chase:start:'+alias,message=dict(chat=dict(id=-10001),message_id=42),**{'from':dict(id=72)})
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(at+MINUTE)/1000):
            callback(self.store,'fake',query)
            self.assertEqual(self.active()[0]['expires_at'],lower['expires_at'])
            query['data']='chase:enter:'+alias
            query['from']['id']=999
            callback(self.store,'fake',query)
            with self.store.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM chasing_sessions WHERE state->>'status'='opposite'").fetchone()[0],0)
            query['from']['id']=72
            callback(self.store,'fake',query);callback(self.store,'fake',query)
        self.assertEqual(len(self.active()),1)
        self.assertEqual(self.active()[0]['id'],original['id'])
        self.assertEqual(self.active()[0]['destinations'],['crypto_auto_chase'])
        with self.store.connect() as db:
            positions=db.execute("SELECT state FROM chasing_sessions WHERE state->>'status'='opposite'").fetchall()
            self.assertEqual(len(positions),1)
            self.assertEqual(positions[0][0]['position']['frame'],'4h')
            from source_update import blockers
            self.assertTrue({'chase','live position','pending chase delivery'}<=set(blockers(db)))
            position=positions[0][0];position['position']['status']='exit_alerted'
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(position),position['id']))
            self.assertIn('live position',blockers(db))
        # The old card must resolve the same fork even after automatic completion.
        from chasing_runtime import tick
        at=START+4*MINUTE
        tick(self.store,at,lambda *a:(None,None,[crypto('5m',at)]))
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(at+MINUTE)/1000):
            callback(self.store,'fake',query)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_sessions WHERE state->>'entry_at' IS NOT NULL").fetchone()[0],1)
            canonical=db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(original['id'],)).fetchone()[0]
            self.assertEqual(canonical['status'],'complete_first')
            position,_=step(position,at+MINUTE,leave=True)
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(position),position['id']))
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(at+2*MINUTE)/1000):
            callback(self.store,'fake',query)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_sessions WHERE state->>'entry_at' IS NOT NULL").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(position['id'],)).fetchone()[0]['position']['status'],'closed')

    def test_configured_asset_shares_higher_original_and_exact_kraken_lower_feed(self):
        with self.store.connect() as db:
            asset=dict(id='BTCUSD',provider='twelvedata',symbol='BTC/USD',exchange='Binance',feed_confirmed=True,
                       timeframe_providers={'1h':'kraken','4h':'kraken'},market='crypto')
            db.execute('INSERT INTO managed_config VALUES(1,1,1,%s)',(json.dumps(dict(assets=[asset])),))
        from chasing_runtime import enroll,tick
        root=crypto('monthly')|dict(symbol='twelvedata:Binance:BTC/USD')
        with patch('chasing_runtime.time.time',return_value=START/1000):original=enroll(self.store,root,self.person,automatic=True)
        self.assertEqual(original['asset_key'],'BTCUSD')
        self.assertEqual(original['chase_symbol'],SYMBOL)
        at=START+MINUTE
        tick(self.store,at,lambda root,now:(None,None,[crypto('1h',at)]))
        self.assertEqual(self.active()[0]['stage'],'1h')
        joined=self.enroll('4h',START+2*MINUTE)
        self.assertEqual(joined['id'],original['id'])
        self.assertEqual(len(self.active()),1)
        from chase_lookback import request
        from scanner import Candle
        pending,_=request(self.active()[0],START+3*MINUTE,'mixed-feed-history')
        with self.store.connect() as db:db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(pending),pending['id']))
        rows=[Candle(START+i*MINUTE,START+(i+1)*MINUTE,100,101,99,100) for i in range(3)]
        fetched=[]
        def history(root,now):
            fetched.append(root['symbol'])
            return rows,[crypto('1h',START+MINUTE),crypto('5m',START+2*MINUTE)]
        tick(self.store,START+3*MINUTE,lookback_fetch=history)
        self.assertEqual(fetched,[SYMBOL])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(original['id'],)).fetchone()[0]['status'],'complete_first')

    def test_entry_at_exact_deadline_expires_canonical_without_nonexistent_position(self):
        from chasing_runtime import callback
        from telegram_personal import prepare_message,bind_message
        state=self.enroll('1h')
        item=prepare_message(self.store,'fake','-10001','original',[],72)
        item.extra_keyboard=[[dict(text="I'm in",callback_data='chase:enter:'+state['id'])]]
        bind_message(self.store,item,dict(chat=dict(id=-10001),message_id=42))
        query=dict(id='boundary',data='chase:enter:'+state['id'],message=dict(chat=dict(id=-10001),message_id=42),**{'from':dict(id=72)})
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(START+55*MINUTE)/1000):callback(self.store,'fake',query)
        with self.store.connect() as db:
            rows=db.execute('SELECT state FROM chasing_sessions').fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][0]['status'],'expired')
            self.assertIsNone(rows[0][0]['entry_at'])
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='expired'").fetchone()[0],1)

    def test_lookback_after_intermediate_descent_finds_five_and_notifies_both_routes(self):
        from chasing_runtime import tick
        from chase_lookback import request
        from scanner import Candle
        self.enroll();self.enroll(auto=False)
        tick(self.store,START+MINUTE,lambda *a:(None,None,[crypto('1h',START+MINUTE)]))
        state=self.active()[0]
        pending,_=request(state,START+3*MINUTE,'history')
        with self.store.connect() as db:db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(pending),pending['id']))
        rows=[Candle(START+i*MINUTE,START+(i+1)*MINUTE,100,101,99,100) for i in range(3)]
        calls=[]
        def history(root,now):
            calls.append(root['symbol'])
            return rows,[crypto('1h',START+MINUTE),crypto('5m',START+2*MINUTE)]
        tick(self.store,START+3*MINUTE,lambda *a:self.fail('Lookback uses historical fetch'),lookback_fetch=history)
        self.assertEqual(calls,[SYMBOL])
        self.assertEqual(self.active(),[])
        with self.store.connect() as db:
            events=db.execute("SELECT event FROM chasing_outbox WHERE event->>'retrospective'='true'").fetchall()
        self.assertEqual(len(events),2)
        self.assertEqual({r[0]['destination'] for r in events},{'chasing','crypto_auto_chase'})
        self.assertTrue(all(r[0]['signal']['timeframe']=='5m' for r in events))

    def test_legacy_duplicates_adopt_lower_watch_without_touching_positions(self):
        from chasing_runtime import upgrade_watches
        one=create(USER,crypto('1h'),START)
        four=create(USER,crypto(),START)
        entered=create(USER,crypto(at=START-MINUTE),START)
        entered,_=step(entered,START,enter=True)
        with self.store.connect() as db:
            for state in (one,four,entered):db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s)',(state['id'],INST,USER,json.dumps(state)))
        upgrade_watches(self.store,START+MINUTE)
        self.assertEqual(len(self.active()),1)
        self.assertEqual(self.active()[0]['stage'],'1h')
        self.assertEqual(self.active()[0]['expires_at'],START+55*MINUTE)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(entered['id'],)).fetchone()[0],entered)

    def api(self,body=None,user=USER):
        with self.store.connect() as db:
            db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_api(%s,%s,%s)',(INST,'/api/crypto-auto-chase-settings',json.dumps(body or {}))).fetchone()[0]

    def test_owner_registration_preserves_other_routes_and_rejects_stale_or_conflicting_saves(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES(%s,%s,72,%s)',(INST,USER,json.dumps({'chasing':'-10001','crypto.rsi':'-10004'})))
        self.assertFalse(self.api()['enabled'])
        result=self.api(dict(action='save',revision=0,enabled=True,chat='-10003'))
        self.assertTrue(result['enabled'])
        with self.store.connect() as db:
            person=db.execute('SELECT bot_runtime.chasing_recipients()').fetchone()[0][0]
            self.assertTrue(person['crypto_auto_chase'])
            self.assertEqual(person['routes']['crypto.rsi'],'-10004')
        for body in (dict(action='save',revision=0,enabled=True,chat='-10003'),
                     dict(action='save',revision=1,enabled=True,chat='-10001'),
                     dict(action='save',revision=1,enabled=True,chat=''),
                     dict(action='save',revision=1,enabled=True,chat='-10003',user_id=USER)):
            with self.assertRaises(Exception):self.api(body)
        with self.assertRaises(Exception):self.api(user='00000000-0000-0000-0000-000000000099')
        with self.assertRaises(Exception),self.store.connect() as db:
            db.execute('SET LOCAL ROLE authenticated');db.execute('SELECT * FROM public.crypto_auto_chase_preferences')
        # Rerunning this additive migration must not reset preferences or wrap itself.
        with self.store.connect() as db:
            # An old wrapper migration replaces the current dispatcher. Keep the
            # rerun isolated so later tests retain newer installed API handlers.
            with db.transaction(force_rollback=True):
                script=(Path(__file__).parent/'cloud/crypto-auto-chase.sql').read_text().replace('BEGIN;','').replace('COMMIT;','')
                db.execute(script,prepare=False)
                db.execute("SELECT set_config('test.auth_uid',%s,true)",(USER,))
                db.execute('SET LOCAL ROLE authenticated')
                result=db.execute('SELECT public.dashboard_api(%s,%s,%s)',(INST,'/api/crypto-auto-chase-settings','{}')).fetchone()[0]
                self.assertTrue(result['enabled'])
        self.assertTrue(self.api()['enabled'])
