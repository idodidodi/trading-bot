import json
import unittest
from unittest.mock import patch
from test_postgres_support import PostgresTestCase, TestStore
from test_divergence_chasing import signal
from chasing_runtime import enroll, tick, deliver_pending, callback, market


@patch('chasing_runtime.CHASING_SUSPENDED',False)
class MarketTests(unittest.TestCase):
    def setUp(self):
        from chasing_runtime import _market_cache
        from shared_kraken import divergences
        _market_cache.clear()
        divergences.clear()
    def test_minute_prices_are_fetched_without_minute_divergence_detection(self):
        from scanner import Candle
        from divergence_chasing import CHASE_FRAMES, DURATIONS
        now=20000000;detected=[];fetched=[]
        def fetch(asset,frame):
            fetched.append(frame)
            return [Candle(now-DURATIONS[frame],now,100,100,100,100)]
        def detect(rows,rules,symbol,frame,**kw):
            detected.append(frame)
            return []
        with patch('kraken_feed.fetch_kraken',side_effect=fetch),patch('scanner.detect',side_effect=detect):
            price,at,signals=market(dict(symbol='kraken:Kraken:NEAR/USD',rules={}),now)
        self.assertEqual((price,at,signals),(100,now,[]))
        self.assertIn('1m',fetched)
        self.assertEqual(set(detected),set(CHASE_FRAMES))
        self.assertNotIn('1m',detected)

    def test_higher_frame_candles_are_shared_until_next_close(self):
        from scanner import Candle
        from divergence_chasing import DURATIONS
        now=20000000;fetched=[]
        def fetch(asset,frame):
            fetched.append(frame)
            return [Candle(now-DURATIONS[frame],now,100,100,100,100)]
        root=dict(symbol='kraken:Kraken:NEAR/USD',rules={})
        with patch('kraken_feed.fetch_kraken',side_effect=fetch),patch('scanner.detect',return_value=[]):
            market(root,now);market(root,now)
            self.assertEqual(len(fetched),6)
            market(root,now+60000)
        self.assertEqual(fetched.count('1m'),2)
        self.assertEqual(len(fetched),7)

USER='00000000-0000-0000-0000-000000000001'
INST='00000000-0000-0000-0000-000000000002'


@patch('chasing_runtime.CHASING_SUSPENDED',False)
class RuntimeTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        quote=patch('chasing_runtime.current_price',return_value=dict(price=123.45,at=1200000))
        quote.start();self.addCleanup(quote.stop)

    def test_slow_fetch_is_checked_against_completion_time(self):
        from divergence_chasing import create,step
        s=create(USER,signal()|dict(rules={}),1000000)
        s,_=step(s,2000000,enter=True)
        with self.store.connect() as db:db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s)',(s['id'],INST,USER,json.dumps(s)))
        clock=iter([2000000,2320000])
        tick(self.store,2000000,lambda *a:(100,2060000,[]),clock=lambda:next(clock))
        with self.store.connect() as db:
            state=db.execute('SELECT state FROM chasing_sessions').fetchone()[0]
        self.assertIsNone(state['position']['entry_price'])
        self.assertTrue(state['data_delayed'])

    def test_data_failure_warning_is_once_per_episode_and_fresh_data_resumes(self):
        from divergence_chasing import create,step
        s=create(USER,signal()|dict(rules={}),1000000)
        s,_=step(s,2000000,enter=True)
        with self.store.connect() as db:
            db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s)',(s['id'],INST,USER,json.dumps(s)))
        def broken(*args):raise ValueError('provider unavailable')
        tick(self.store,2000000,broken);tick(self.store,2060000,broken)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='position_data_delayed'").fetchone()[0],1)
        tick(self.store,2120000,lambda *a:(100,2120000,[]))
        tick(self.store,2180000,lambda *a:(90,2000000,[]))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='position_data_delayed'").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='position_data_resumed'").fetchone()[0],1)
            state=db.execute('SELECT state FROM chasing_sessions').fetchone()[0]
            self.assertEqual(state['position']['entry_price'],100)
            self.assertEqual(state['position']['status'],'monitoring')

    def api(self,body,user=USER):
        with self.store.connect() as db:
            db.execute("SELECT set_config('test.auth_uid',%s,true)",(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_api(%s,%s,%s)',(INST,'/api/chasing-settings',json.dumps(body))).fetchone()[0]

    def test_personal_default_save_and_revision(self):
        value=self.api({});self.assertEqual(float(value['stop_pct']),.5)
        value=self.api(dict(action='save',revision=0,stop_pct=1,chasing=True,live_positions=False))
        self.assertEqual(value['revision'],1);self.assertEqual(float(value['stop_pct']),1)
        with self.assertRaises(Exception):self.api(dict(action='save',revision=0,stop_pct=2,chasing=True,live_positions=False))
        with self.assertRaises(Exception):self.api(dict(action='save',revision=1,stop_pct=0,chasing=True,live_positions=False))
        with self.assertRaises(Exception):self.api({},user='00000000-0000-0000-0000-000000000099')

    def test_preferences_and_worker_state_not_directly_readable(self):
        with self.assertRaises(Exception),self.store.connect() as db:
            db.execute('SET LOCAL ROLE authenticated')
            db.execute('SELECT * FROM public.chasing_preferences')
        with self.assertRaises(Exception),self.store.connect() as db:
            db.execute('SET LOCAL ROLE authenticated')
            db.execute('SELECT * FROM bot_runtime.chasing_sessions')

    @patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
    def test_non_owner_cannot_enroll_even_with_enabled_person_snapshot(self):
        other='00000000-0000-0000-0000-000000000099'
        person=dict(user_id=other,chasing=True,routes={'chasing':'-10003'},stop_pct=.5,revision=1,created_at=0)
        root=signal(at=1000000)|dict(signal_status='provisional',rules={})
        with patch('chasing_runtime.time.time',return_value=1000):enroll(self.store,root,person)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM chasing_sessions').fetchone()[0],0)
            db.execute('INSERT INTO auth.users VALUES(%s) ON CONFLICT DO NOTHING',(other,))
            db.execute('INSERT INTO public.dashboard_members(installation,user_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(INST,other))
            db.execute('INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES(%s,%s,99,%s)',(INST,other,'{}'))
            db.execute('INSERT INTO public.chasing_preferences(installation,user_id,chasing,live_positions) VALUES(%s,%s,true,true)',(INST,other))
            self.assertEqual(db.execute('SELECT bot_runtime.chasing_recipients()').fetchone()[0],[])
        with self.assertRaises(Exception):self.api(dict(action='save',revision=1,stop_pct=.5,chasing=True,live_positions=True),user=other)

    @patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
    def test_bound_personal_callback_enters_and_opposite_delivery_is_terminal(self):
        person=dict(user_id=USER,telegram_user_id=72,chasing=True,live_positions=True,
                    routes={'chasing':'-10001','live_positions':'-10002'},stop_pct=.5,revision=1,created_at=0)
        root=signal(at=1000000)|dict(signal_status='provisional',rules={})
        with patch('chasing_runtime.time.time',return_value=1000):enroll(self.store,root,person)
        tick(self.store,1300000,lambda *a:(99,1300000,[signal('15m',1300000)]))
        calls=[]
        counter=[69]
        def api(token,method,body):
            calls.append((method,body))
            if method=='sendMessage':counter[0]+=1
            return dict(chat=dict(id=int(body.get('chat_id',-10001))),message_id=counter[0])
        with patch('chasing_runtime.people',return_value=[person]),patch('telegram_checklist.api',side_effect=api):
            deliver_pending(self.store,'123:fake')
            with self.store.connect() as db:
                s=db.execute('SELECT state FROM chasing_sessions').fetchone()[0]
            query=dict(id='q',data='chase:enter:'+s['id'],message=dict(chat=dict(id=-10001),message_id=70),**{'from':dict(id=999)})
            callback(self.store,'123:fake',query)
            with self.store.connect() as db:self.assertEqual(db.execute('SELECT state FROM chasing_sessions').fetchone()[0]['status'],'complete_first')
            query['from']['id']=72
            with patch('chasing_runtime.time.time',return_value=2000):callback(self.store,'123:fake',query)
            tick(self.store,2300000,lambda *a:(99,2300000,[signal(at=2300000,direction='bearish')]))
            deliver_pending(self.store,'123:fake')
            # Opposite-divergence notification does not stop minute risk checks.
            tick(self.store,2360000,lambda *a:(98,2360000,[]))
            deliver_pending(self.store,'123:fake')
            tick(self.store,2420000,lambda *a:(97,2420000,[]))
            deliver_pending(self.store,'123:fake')
            with self.store.connect() as db:
                state=db.execute('SELECT state FROM chasing_sessions').fetchone()[0]
                exit_id=db.execute("SELECT message_id FROM chasing_outbox WHERE event->>'kind'='exit_signal'").fetchone()[0]
                self.assertEqual(state['position']['status'],'exit_alerted')
                self.assertEqual(state['next_due'],2480000)
                self.assertEqual(db.execute("SELECT count(*) FROM chasing_outbox WHERE event->>'kind'='exit_signal'").fetchone()[0],1)
            leave=dict(id='leave',data='chase:exit:'+s['id'],message=dict(chat=dict(id=-10002),message_id=exit_id),**{'from':dict(id=999)})
            callback(self.store,'123:fake',leave)
            with self.store.connect() as db:self.assertEqual(db.execute('SELECT state FROM chasing_sessions').fetchone()[0]['position']['status'],'exit_alerted')
            leave['from']['id']=72
            with patch('chasing_runtime.time.time',return_value=2500):callback(self.store,'123:fake',leave)
            deliver_pending(self.store,'123:fake')
            tick(self.store,2600000,lambda *a:self.fail('No checks after reported exit'))
        sent=[body for method,body in calls if method=='sendMessage']
        self.assertEqual([str(b['chat_id']) for b in sent],['-10001']+['-10002']*4)
        self.assertIn('Current price: 123.45',sent[0]['text'])
        self.assertEqual(sent[0]['reply_markup']['inline_keyboard'][1][0]['text'],"I'm in")
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT state FROM chasing_sessions').fetchone()[0]['status'],'reported_exited')

    @patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
    def test_enroll_dedup_first_divergence_and_no_search_after_stop(self):
        root=signal(at=1000000)|dict(signal_status='provisional',rules={})
        person=dict(user_id=USER,chasing=True,routes={'chasing':'-10001'},stop_pct=.7,revision=1,created_at=0)
        with patch('chasing_runtime.time.time',return_value=1000):
            enroll(self.store,root,person);enroll(self.store,root,person)
        fetch=lambda root,now:(99,now,[signal('5m',now)])
        tick(self.store,1060000,lambda *a:self.fail('No pre-entry check before five minutes'))
        tick(self.store,1300000,fetch)
        with self.store.connect() as db:
            rows=db.execute('SELECT state FROM chasing_sessions').fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0][0]['status'],'complete_first')
            self.assertEqual(rows[0][0]['stop_pct'],.7)
            self.assertEqual(db.execute('SELECT count(*) FROM chasing_outbox').fetchone()[0],2)
        with patch('chasing_runtime.market',side_effect=AssertionError('No further searches')):
            tick(self.store,1600000,lambda *a: self.fail('No further searches'))
