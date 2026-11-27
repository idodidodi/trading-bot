import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock
from colmex_feed import fetch_colmex
from scanner import Candle
from rsi_pb import next_start
from scan_schedule import next_due, identity

ASSET=dict(id='DAX',provider='colmex',symbol='GER40#',exchange='Colmex-MT4',feed_confirmed=True,market='index')
def stamp(s):return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())

class DaxTimingTests(unittest.TestCase):
    def test_adapter_and_scheduler_share_broker_close(self):
        t=stamp('2026-11-26T09:10:00');now=t+14400
        reader=SimpleNamespace(FRAMES={'4h':14400},read_json=lambda *a:{},candles=lambda *a,**kw:[(t,now,10,12,9,11)])
        with patch.dict('sys.modules', {'colmex_mt4.history':SimpleNamespace(dax_next_close=lambda n,p:now+14400,DAX_TIMING_VERSION='test')}),patch('colmex_feed.bridge',return_value=reader),patch('colmex_feed.time.time',return_value=now),patch.dict('os.environ',COLMEX_TIME_POLICY='/test',COLMEX_FILES_DIR='/test'):
            raw=fetch_colmex(ASSET,'4h')
        self.assertEqual(raw[0].end,now*1000)
        self.assertEqual(raw.next_close_at,(now+14400)*1000)
        self.assertEqual(next_due(ASSET,'4h',raw,raw,now*1000,dict(status='scanned')),raw.next_close_at)

    def test_rsi_followup_skips_closed_session_and_weekend(self):
        a=stamp('2026-11-27T17:10:00')*1000;b=stamp('2026-11-27T21:00:00')*1000
        expected=stamp('2026-11-30T01:10:00')
        helper=Mock(return_value=expected)
        with patch.dict('sys.modules', {'colmex_mt4.history':SimpleNamespace(dax_next_start=helper)}):
            for tf,period in (('1h',3600),('4h',14400),('daily',86400)):
                self.assertEqual(next_start(Candle(a,b,10,12,9,11),tf,ASSET),expected*1000)
                helper.assert_called_with(b//1000,period)


from test_postgres_support import PostgresTestCase, TestStore
class DaxCutoverTests(PostgresTestCase):
    def test_cutover_scoped_idempotent_and_retains_history(self):
        import json
        from pathlib import Path
        store=TestStore()
        source='colmex:Colmex-MT4:GER40#'
        other='colmex:Colmex-MT4:SPX500#'
        with store.connect() as db:
            for key,feed in [('dax',source),('spx',other)]:
                payload=json.dumps(dict(source=feed))
                db.execute('INSERT INTO pin_bar_signals VALUES(%s,1,%s)',(key,payload))
                db.execute('INSERT INTO notification_queue(id,text) VALUES(%s,%s)',('pin-bars:'+key,'test'))
                db.execute('INSERT INTO pin_bar_state VALUES(%s,%s,1)',(key+'@'+feed+'@v1','4h'))
                db.execute('INSERT INTO rsi_pb_pending VALUES(%s,%s,%s,%s)',(key,key+'@'+feed+'@v1','4h',payload))
                db.execute('INSERT INTO candle_cache VALUES(%s,%s,%s,0,1,1,2,1,2)',(feed,key,'4h'))
        sql=(Path(__file__).parent/'cloud/dax-colmex-timing-cutover.sql').read_text()
        for _ in range(2):
            with store.connect() as db:
                db.execute('SET LOCAL ROLE trading_worker')
                db.execute(sql,prepare=False)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT id,status FROM notification_queue ORDER BY id').fetchall(),[('pin-bars:dax','cancelled'),('pin-bars:spx','pending')])
            self.assertEqual(db.execute('SELECT feed FROM candle_cache').fetchall(),[(other,)])
            self.assertEqual(db.execute('SELECT id FROM rsi_pb_pending').fetchall(),[('spx',)])
            self.assertEqual(db.execute('SELECT count(*) FROM pin_bar_signals').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM pin_bar_state').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT payload::jsonb->>'timing_superseded' FROM pin_bar_signals WHERE id='dax'").fetchone()[0],'colmex-dax-session-2026-11-27-v1')
