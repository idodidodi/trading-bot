import json
import unittest
from unittest.mock import Mock, patch

from chase_lookback import request
from chasing_runtime import callback, deliver_pending, enqueue, tick
from divergence_chasing import create, step
from test_chase_lookback import ORIGIN, USER, signal
import test_chase_lookback as fixtures
from test_postgres_support import PostgresTestCase


class StopChasingTests(unittest.TestCase):
    def test_stop_cancels_history_and_is_terminal_and_idempotent(self):
        state = create(USER, signal(), ORIGIN)
        pending, _ = request(state, ORIGIN + 1000, 'history')
        stopped, events = step(pending, ORIGIN + 2000, stop=True)
        self.assertEqual(stopped['status'], 'stopped_manual')
        self.assertIsNone(stopped['next_due'])
        self.assertIsNone(stopped['entry_at'])
        self.assertEqual(stopped['lookback']['status'], 'cancelled')
        self.assertGreater(stopped['revision'], pending['revision'])
        self.assertEqual(events, [])
        self.assertEqual(step(stopped, ORIGIN + 3000, stop=True)[0], stopped)
        self.assertEqual(step(stopped, ORIGIN + 3000, enter=True)[0], stopped)
        self.assertEqual(request(stopped, ORIGIN + 3000, 'new')[0], stopped)
        self.assertEqual(state['status'], 'chasing')

    def test_stop_does_not_change_reported_position(self):
        entered, _ = step(create(USER, signal(), ORIGIN), ORIGIN + 1000, enter=True)
        self.assertEqual(step(entered, ORIGIN + 2000, stop=True), (entered, []))


class StopChasingRuntimeTests(PostgresTestCase):
    setUp = fixtures.LookbackRuntimeTests.setUp
    saved = fixtures.LookbackRuntimeTests.saved

    def stop(self, query=None):
        query = query or self.query
        with patch('telegram_checklist.api') as api, patch('chasing_runtime.time.time', return_value=(ORIGIN+2000)/1000):
            callback(self.store, '123:fake', query | dict(data='chase:stop:'+self.state['id']))
        return api

    def test_stop_persists_cancels_queued_alerts_and_never_fetches_again(self):
        with self.store.connect() as db:
            enqueue(db, self.state, dict(kind='started', at=ORIGIN))
        self.stop()
        self.assertEqual(self.saved()['status'], 'stopped_manual')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM chasing_outbox').fetchone()[0], 'suppressed')
        fetch = Mock(side_effect=AssertionError('Stopped watch must not fetch'))
        tick(self.store, ORIGIN+300000, fetch=fetch, lookback_fetch=fetch)
        fetch.assert_not_called()
        before = self.saved()
        self.stop()
        self.assertEqual(self.saved(), before)
        with patch('telegram_checklist.api'):
            for action in ('start', 'lookback', 'enter'):
                callback(self.store, '123:fake', self.query | dict(data=f'chase:{action}:'+self.state['id']))
        self.assertEqual(self.saved(), before)

    def test_wrong_owner_chat_and_message_cannot_stop(self):
        for query in (self.query | {'from':dict(id=99)},
                      self.query | dict(message=dict(chat=dict(id=-901),message_id=42)),
                      self.query | dict(message=dict(chat=dict(id=-900),message_id=43))):
            self.stop(query)
            self.assertEqual(self.saved()['status'], 'chasing')

    def test_inflight_lookback_cannot_overwrite_stop(self):
        pending, _ = request(self.state, ORIGIN+1000, 'pending')
        with self.store.connect() as db:
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s', (json.dumps(pending), pending['id']))
        def history(*args):
            self.stop()
            return [], []
        tick(self.store, ORIGIN+1000, lookback_fetch=history)
        self.assertEqual(self.saved()['status'], 'stopped_manual')
        self.assertEqual(self.saved()['lookback']['status'], 'cancelled')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM chasing_outbox').fetchone()[0], 0)

    def test_started_card_shows_only_done_entry_and_stop(self):
        with self.store.connect() as db:
            enqueue(db, self.state, dict(kind='started', at=ORIGIN))
        person=dict(user_id=USER,telegram_user_id=72,chasing=True,routes={'chasing':'-900'})
        with patch('chasing_runtime.people',return_value=[person]), patch('telegram_checklist.api', return_value=dict(chat=dict(id=-900),message_id=60)) as api:
            deliver_pending(self.store, '123:fake')
        body=next(c.args[2] for c in api.call_args_list if c.args[1]=='sendMessage')
        self.assertEqual([[b['text'] for b in row] for row in body['reply_markup']['inline_keyboard']],
                         [['✓ Done'], ["I'm in"], ['Stop chasing']])

    def test_shared_automatic_watch_stops_without_affecting_separate_position(self):
        from crypto_chase_policy import POLICY
        self.state.update(watch_policy=POLICY, destinations=['chasing','crypto_auto_chase'])
        position, _ = step(create(USER, signal(at=ORIGIN-1000), ORIGIN), ORIGIN+1000, enter=True)
        with self.store.connect() as db:
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s', (json.dumps(self.state),self.state['id']))
            db.execute('INSERT INTO chasing_sessions SELECT %s,installation,%s,%s FROM runtime_control WHERE id=1', (position['id'],USER,json.dumps(position)))
            enqueue(db,self.state,dict(kind='started',at=ORIGIN))
        self.stop()
        self.assertEqual(self.saved()['status'],'stopped_manual')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM chasing_sessions WHERE id=%s',(position['id'],)).fetchone()[0],position)
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM chasing_outbox')],['suppressed','suppressed'])

    def test_auto_card_outside_position_frames_has_stop(self):
        from crypto_chase_policy import POLICY
        self.state.update(watch_policy=POLICY,destinations=['crypto_auto_chase'])
        self.state['root']['timeframe']='1d'
        with self.store.connect() as db:
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(self.state),self.state['id']))
            enqueue(db,self.state,dict(kind='started',at=ORIGIN))
        person=dict(user_id=USER,telegram_user_id=72,crypto_auto_chase=True,routes={'crypto_auto_chase':'-900'})
        with patch('chasing_runtime.people',return_value=[person]), patch('telegram_checklist.api',return_value=dict(chat=dict(id=-900),message_id=60)) as api:
            deliver_pending(self.store,'123:fake')
        body=next(c.args[2] for c in api.call_args_list if c.args[1]=='sendMessage')
        self.assertIn([dict(text='Stop chasing',callback_data='chase:stop:'+self.state['id'])],body['reply_markup']['inline_keyboard'])

    def test_stop_during_delivery_suppresses_rest_of_loaded_batch(self):
        with self.store.connect() as db:
            enqueue(db,self.state,dict(kind='started',at=ORIGIN))
            enqueue(db,self.state,dict(kind='started',at=ORIGIN+1))
        person=dict(user_id=USER,telegram_user_id=72,chasing=True,routes={'chasing':'-900'})
        sends=[]
        def api(token,method,body):
            if method=='sendMessage':
                sends.append(body)
                self.stop()
                return dict(chat=dict(id=-900),message_id=60)
        with patch('chasing_runtime.people',return_value=[person]),patch('telegram_checklist.api',side_effect=api):
            deliver_pending(self.store,'123:fake')
        self.assertEqual(len(sends),1)
        self.assertEqual(self.saved()['status'],'stopped_manual')
