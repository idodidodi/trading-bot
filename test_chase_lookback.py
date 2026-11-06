from unittest.mock import patch
import unittest
from scanner import Candle
from divergence_chasing import create,step
from chase_lookback import replay,request,finish

ORIGIN=3600000


def signal(frame='4h',at=ORIGIN,direction='bullish'):
    return dict(symbol='TEST',timeframe=frame,direction=direction,price2=100,pivot1=at-600000,pivot2=at-300000,confirmed_at=at)


def minutes(prices):return [Candle(ORIGIN+i*60000,ORIGIN+(i+1)*60000,p,p,p,p) for i,p in enumerate(prices)]


@patch('chasing_runtime.CHASING_SUSPENDED',False)
class LookbackTests(unittest.TestCase):
    def setUp(self):self.session=create('owner',signal(),ORIGIN)

    def test_missed_second_divergence_found_at_original_time_without_entry(self):
        at=ORIGIN+300000
        result=replay(self.session,ORIGIN+600000,minutes([99]*10),[signal('5m',at)])
        self.assertEqual(result['status'],'found')
        self.assertEqual(result['signal']['confirmed_at'],at)
        pending,_=request(self.session,ORIGIN+600000,'request')
        state,event=finish(pending,result,ORIGIN+601000)
        self.assertEqual(state['status'],'complete_first')
        self.assertIsNone(state['entry_at'])
        self.assertEqual(state['started_at'],ORIGIN)
        self.assertEqual(event['kind'],'lookback_result')
        self.assertEqual(event['at'],ORIGIN+601000)

    def test_favorable_stop_precedes_later_divergence(self):
        result=replay(self.session,ORIGIN+600000,minutes([101]*5+[99]*5),[signal('5m',ORIGIN+600000)])
        self.assertEqual(result['status'],'stopped_favorable')
        self.assertEqual(result['through_at'],ORIGIN+300000)

    def test_one_minute_opposite_preexisting_and_future_signals_do_not_qualify(self):
        signals=[signal('1m',ORIGIN+300000),signal('5m',ORIGIN+300000,'bearish'),signal('5m'),signal('5m',ORIGIN+900000)]
        result=replay(self.session,ORIGIN+600000,minutes([99]*10),signals)
        self.assertEqual(result['status'],'none')

    def test_gap_before_finding_is_incomplete_but_gap_after_terminal_is_irrelevant(self):
        bars=minutes([99]*10)
        bad=replay(self.session,ORIGIN+600000,bars[1:],[signal('5m',ORIGIN+300000)])
        self.assertEqual(bad['status'],'incomplete')
        good=replay(self.session,ORIGIN+600000,bars[:5],[signal('5m',ORIGIN+300000)])
        self.assertEqual(good['status'],'found')

    def test_no_finding_resumes_without_backdating_new_signals(self):
        pending,_=request(self.session,ORIGIN+600000,'request')
        result=replay(pending,ORIGIN+600000,minutes([99]*10),[])
        state,_=finish(pending,result,ORIGIN+601000)
        self.assertEqual(state['checked_at'],ORIGIN+600000)
        self.assertGreater(state['next_due'],ORIGIN+601000)
        state,events=step(state,ORIGIN+900000,price=99,price_at=ORIGIN+900000,signals=[signal('5m',ORIGIN+300000)])
        self.assertEqual(state['status'],'chasing')
        self.assertFalse(any(e['kind']=='chase' for e in events))

    def test_duplicate_request_no_revival_and_entry_cancels_pending(self):
        stopped,_=step(self.session,ORIGIN+300000,price=101,price_at=ORIGIN+300000)
        pending,_=request(stopped,ORIGIN+600000,'r')
        self.assertEqual(request(pending,ORIGIN+600001,'r')[0],pending)
        result=replay(pending,ORIGIN+600000,minutes([99]*10),[signal('5m',ORIGIN+300000)])
        done,_=finish(pending,result,ORIGIN+601000)
        self.assertEqual(done['status'],'stopped_favorable')
        self.assertIsNone(done['next_due'])
        pending,_=request(self.session,ORIGIN+600000,'new')
        entered,_=step(pending,ORIGIN+600001,enter=True)
        self.assertEqual(entered['lookback']['status'],'cancelled')
        self.assertEqual(request(entered,ORIGIN+600002,'another')[0],entered)

    def test_history_bound_is_explicit(self):
        result=replay(self.session,ORIGIN+13*3600000,[],[])
        self.assertEqual(result['status'],'incomplete')

    def test_existing_expiry_precedes_a_later_finding(self):
        self.session['expires_at']=ORIGIN+300000
        result=replay(self.session,ORIGIN+600000,minutes([99]*10),[signal('5m',ORIGIN+600000)])
        self.assertEqual(result['status'],'expired')

    def test_missing_indicator_warmup_does_not_claim_no_divergence(self):
        from chasing_runtime import history
        from platform_app import load_rules
        with patch('chasing_runtime.market_bars',return_value={'1m':minutes([99]*10),'5m':minutes([99])}):
            with self.assertRaisesRegex(ValueError,'warm-up'):history(self.session['root']|dict(rules=load_rules()),ORIGIN+600000)

from unittest.mock import patch,Mock
from test_postgres_support import PostgresTestCase,TestStore
from chasing_runtime import callback,tick,deliver_pending,chase_buttons
from telegram_personal import prepare_message,bind_message,deliver_personal
import json

USER='00000000-0000-0000-0000-000000000001'
INST='00000000-0000-0000-0000-000000000002'


@patch('chasing_runtime.CHASING_SUSPENDED',False)
class LookbackRuntimeTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        self.root=signal()|dict(rules={},signal_status='provisional')
        self.state=create(USER,self.root,ORIGIN)
        with self.store.connect() as db:db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s)',(self.state['id'],INST,USER,json.dumps(self.state)))
        self.item=prepare_message(self.store,'123:fake','-900','Original RSI alert',[],72)
        self.item.extra_keyboard=chase_buttons(self.state['id'])
        bind_message(self.store,self.item,dict(chat=dict(id=-900),message_id=42))
        self.query=dict(id='query',data='chase:lookback:'+self.state['id'],message=dict(chat=dict(id=-900),message_id=42),**{'from':dict(id=72)})

    def saved(self):
        with self.store.connect() as db:return db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(self.state['id'],)).fetchone()[0]

    def test_real_message_serializer_persists_adjacent_actions_for_undo(self):
        import io
        from platform_app import send_telegram
        from telegram_dismissal import DismissibleMessage
        actions=chase_buttons(self.state['id'])
        response=io.StringIO(json.dumps(dict(ok=True,result=dict(chat=dict(id=-900),message_id=43))))
        with patch('platform_app.urllib.request.urlopen',return_value=response) as http:
            send_telegram('123:fake',-900,'Fresh original',dismissal_user_id=72,dismissal_actions=actions)
        payload=json.loads(http.call_args.args[0].data)
        with self.store.connect() as db:
            raw=db.execute("SELECT state FROM telegram_personal_messages WHERE (state::jsonb->>'message_id')::bigint=43").fetchone()[0]
        stored=json.loads(raw)
        self.assertEqual(stored['extra_keyboard'],actions)
        self.assertEqual(DismissibleMessage.restore(stored).keyboard(),payload['reply_markup'])

    def test_original_alert_owner_binding_history_result_and_repeated_callback(self):
        stranger=self.query|{'from':dict(id=999)}
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(ORIGIN+600000)/1000):
            callback(self.store,'123:fake',stranger)
            self.assertNotIn('lookback',self.saved())
            callback(self.store,'123:fake',self.query)
            callback(self.store,'123:fake',self.query)
        self.assertEqual(self.saved()['lookback']['status'],'pending')
        historical=Mock(return_value=(minutes([99]*10),[signal('5m',ORIGIN+300000)]))
        tick(self.store,ORIGIN+601000,lookback_fetch=historical)
        self.assertEqual(historical.call_count,1)
        self.assertEqual(self.saved()['status'],'complete_first')
        self.assertIsNone(self.saved()['entry_at'])
        person=dict(user_id=USER,telegram_user_id=72,chasing=True,live_positions=False,routes={'chasing':'-10001'},stop_pct=.5,revision=1)
        with patch('chasing_runtime.people',return_value=[person]),patch('telegram_checklist.api',return_value=dict(chat=dict(id=-10001),message_id=60)) as api:
            deliver_pending(self.store,'123:fake')
        body=next(c.args[2] for c in api.call_args_list if c.args[1]=='sendMessage')
        self.assertIn('LOOKBACK',body['text'])
        self.assertIn('historical finding',body['text'])
        self.assertEqual([b['text'] for b in body['reply_markup']['inline_keyboard'][2]],['Chase','Chase + look back'])
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(ORIGIN+602000)/1000):callback(self.store,'123:fake',self.query)
        self.assertEqual(self.saved()['lookback']['status'],'complete')
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='lookback_result'").fetchone()[0],1)

    def test_dismissed_or_wrong_message_cannot_start_lookup(self):
        with self.store.connect() as db:
            db.execute("UPDATE telegram_personal_messages SET state=jsonb_set(state::jsonb,'{phase}','\"pending\"')::text WHERE id=%s",(self.item.key,))
        with patch('telegram_checklist.api'):callback(self.store,'123:fake',self.query)
        self.assertNotIn('lookback',self.saved())

    def test_provider_failure_reports_incomplete_and_keeps_live_session(self):
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=(ORIGIN+600000)/1000):callback(self.store,'123:fake',self.query)
        tick(self.store,ORIGIN+601000,lookback_fetch=Mock(side_effect=ValueError('history unavailable')))
        state=self.saved()
        self.assertEqual(state['lookback']['result']['status'],'incomplete')
        self.assertEqual(state['status'],'chasing')
        self.assertGreater(state['next_due'],ORIGIN+601000)

    def test_old_window_is_rejected_without_unnecessary_provider_calls(self):
        at=ORIGIN+13*3600000
        with patch('telegram_checklist.api'),patch('chasing_runtime.time.time',return_value=at/1000):callback(self.store,'123:fake',self.query)
        history=Mock(side_effect=AssertionError('No provider request for unsupported window'))
        tick(self.store,at+1000,lookback_fetch=history)
        self.assertEqual(history.call_count,0)
        self.assertEqual(self.saved()['lookback']['result']['status'],'incomplete')

    @patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
    def test_original_delivery_gets_adjacent_buttons_before_post_delivery_enrollment(self):
        with self.store.connect() as db:db.execute("INSERT INTO telegram_event_routes VALUES('offer','crypto','rsi','-900','-900')")
        person=dict(user_id=USER,telegram_user_id=72,routes={'crypto.rsi':'-900','chasing':'-10001'},enabled={},created_at=0,
                    chasing=True,live_positions=False,stop_pct=.5,revision=1)
        sent=Mock(return_value=42);order=[]
        def delivered(store,token,chat,text,event,send,**kw):
            send(token,chat,text);order.append('delivered')
        with patch('telegram_personal.recipients',return_value=[person]),patch('chasing_runtime.people',return_value=[person]), \
             patch('telegram_personal.time.time',return_value=(ORIGIN+600000)/1000), \
             patch('telegram_delivery.deliver',side_effect=delivered),patch('chasing_runtime.enroll',side_effect=lambda *a:order.append('enrolled')):
            deliver_personal(self.store,'123:fake','Original','offer',sent,payload=self.root)
        self.assertEqual(order,['delivered','enrolled'])
        self.assertEqual([b['text'] for b in sent.call_args.kwargs['dismissal_actions'][0]],['Chase','Chase + look back'])
