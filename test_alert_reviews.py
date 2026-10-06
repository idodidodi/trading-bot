import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import replace
from alert_reviews import review
from platform_app import Store,load_rules
from scanner import detect
from test_scanner import fixture

class ReviewTests(unittest.TestCase):
    def test_clock_reviews_dedup_confirmed_stage_and_breach(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite3')
            now=datetime(2026,10,6,7,1,tzinfo=ZoneInfo('Asia/Jerusalem')).timestamp()
            bars=fixture();shift=int(now*1000)-bars[-1].end
            bars=[replace(c,start=c.start+shift,end=c.end+shift) for c in bars]
            warmup=detect(bars,load_rules(),'TEST','4h',provisional=True)[0]
            confirmed=detect(bars,load_rules(),'TEST','4h')[0]
            store.insert(warmup);store.insert(confirmed)
            with store.connect() as db:
                for c in bars:db.execute('INSERT INTO candle_cache(feed,timeframe,start,end,high,low) VALUES(?,?,?,?,?,?)',('TEST','4h',c.start,c.end,c.high,c.low))
                db.execute('UPDATE signals SET received=?',(now-60,))
            self.assertEqual(review(store,now),1)
            payloads=[json.loads(r[0]) for r in store.rows()]
            scheduled=[s for s in payloads if s.get('review_slot')]
            self.assertEqual(len(scheduled),1)
            self.assertNotIn('signal_status',scheduled[0])
            self.assertIn('Confirmed divergence',scheduled[0]['stage_description'])
            self.assertEqual(review(store,now+30),0)
            self.assertEqual(review(store,now+4*3600),1)
            with store.connect() as db:
                db.execute('INSERT INTO candle_cache(feed,timeframe,start,end,high,low) VALUES(?,?,?,?,?,?)',('TEST','4h',int(now*1000)+1,int(now*1000)+2,100,confirmed['price2']-1))
            self.assertEqual(review(store,now+86400),0)
            self.assertEqual(review(store,now+86400+4*3600),0)
            self.assertEqual(review(store,now+600),0)

if __name__=='__main__':unittest.main()
