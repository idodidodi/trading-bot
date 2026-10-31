import json
from unittest.mock import patch
from test_postgres_support import PostgresTestCase, TestStore
from test_divergence_chasing import signal
from chasing_runtime import enroll, tick, deliver_pending, callback

USER='00000000-0000-0000-0000-000000000001'
INST='00000000-0000-0000-0000-000000000002'


class RuntimeTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()

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
    def test_bound_personal_callback_enters_and_opposite_delivery_is_terminal(self):
        person=dict(user_id=USER,telegram_user_id=72,chasing=True,live_positions=True,
                    routes={'chasing':'-10001','live_positions':'-10002'},stop_pct=.5,revision=1,created_at=0)
        root=signal(at=1000000)|dict(signal_status='provisional',rules={})
        with patch('chasing_runtime.time.time',return_value=1000):enroll(self.store,root,person)
        tick(self.store,1060000,lambda *a:(99,1060000,[signal('15m',1060000)]))
        calls=[]
        def api(token,method,body):
            calls.append((method,body))
            return dict(chat=dict(id=int(body.get('chat_id',-10001))),message_id=70)
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
        sent=[body for method,body in calls if method=='sendMessage']
        self.assertEqual([str(b['chat_id']) for b in sent],['-10001','-10002'])
        self.assertEqual(sent[0]['reply_markup']['inline_keyboard'][1][0]['text'],"I'm in")
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT state FROM chasing_sessions').fetchone()[0]['status'],'opposite_found')

    @patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
    def test_enroll_dedup_first_divergence_and_no_search_after_stop(self):
        root=signal(at=1000000)|dict(signal_status='provisional',rules={})
        person=dict(user_id=USER,chasing=True,routes={'chasing':'-10001'},stop_pct=.7,revision=1,created_at=0)
        with patch('chasing_runtime.time.time',return_value=1000):
            enroll(self.store,root,person);enroll(self.store,root,person)
        fetch=lambda root,now:(99,now,[signal('5m',now)])
        tick(self.store,1060000,fetch)
        with self.store.connect() as db:
            rows=db.execute('SELECT state FROM chasing_sessions').fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0][0]['status'],'complete_first')
            self.assertEqual(rows[0][0]['stop_pct'],.7)
            self.assertEqual(db.execute('SELECT count(*) FROM chasing_outbox').fetchone()[0],2)
        with patch('chasing_runtime.market',side_effect=AssertionError('No further searches')):
            tick(self.store,1120000,lambda *a: self.fail('No further searches'))
