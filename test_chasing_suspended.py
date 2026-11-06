import json
import unittest
from unittest.mock import Mock,patch
from test_postgres_support import PostgresTestCase,TestStore
from divergence_chasing import create,step
from test_divergence_chasing import signal
import chasing_runtime as c
USER='00000000-0000-0000-0000-000000000001'
INST='00000000-0000-0000-0000-000000000002'

@patch('chasing_runtime.CHASING_SUSPENDED',True)
class SuspensionTests(unittest.TestCase):
    @patch.dict('os.environ',{'DIVERGENCE_CHASING_ENABLED':'true'})
    def test_old_environment_cannot_enable_chasing(self):
        self.assertFalse(c.enabled())
        self.assertIsNone(c.proposal({}, {}, 1))

    def test_old_buttons_do_not_mutate_state(self):
        store=Mock()
        for action in ('start','lookback','enter'):
            with patch('postgres_store.assert_worker'),patch('telegram_checklist.api') as api:
                c.callback(store,'fake',dict(id='q',data='chase:'+action+':old'))
                self.assertIn('suspended',api.call_args.args[2]['text'])
        store.connect.assert_not_called()

@patch('chasing_runtime.CHASING_SUSPENDED',True)
class DurableSuspensionTests(PostgresTestCase):
    def test_cancel_watch_and_pending_messages_preserves_entered_positions(self):
        store=TestStore();watch=create(USER,signal(at=1000000),1000000)
        watch['lookback']=dict(status='pending')
        pos,_=step(create(USER,signal(at=900000),900000),1000000,enter=True)
        with store.connect() as db:
            for s in (watch,pos):db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s)',(s['id'],INST,USER,json.dumps(s)))
            c.enqueue(db,watch,dict(kind='started',at=1000000))
            c.enqueue(db,pos,dict(kind='entered',at=1000000,destination='live_positions'))
        c.cancel_unentered(store);c.cancel_unentered(store)
        with store.connect() as db:
            saved=db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(watch['id'],)).fetchone()[0]
            self.assertEqual(saved['status'],'expired');self.assertIsNone(saved['next_due'])
            self.assertEqual(saved['revision'],watch['revision']+1)
            self.assertEqual(saved['lookback']['status'],'cancelled')
            self.assertEqual(db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(pos['id'],)).fetchone()[0],pos)
            self.assertEqual(dict(db.execute("SELECT event->>'kind',status FROM chasing_outbox")),dict(started='suppressed',entered='pending'))
        with patch('chasing_runtime.people',return_value=[dict(user_id=USER,chasing=True,crypto_auto_chase=True,routes={'chasing':'-1','crypto_auto_chase':'-2'})]),patch('telegram_checklist.api') as api:
            c.deliver_pending(store,'fake');api.assert_not_called()
