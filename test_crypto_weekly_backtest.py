import unittest
import tempfile
from unittest.mock import patch
from datetime import date
from scanner import Candle, detect
from platform_app import load_rules
from research import crypto_weekly_backtest as b

def candle(start,end=None,close=100):return Candle(start,end or start+60000,100,max(101,close),min(99,close),close)
def signal(start,direction='bullish'):return dict(direction=direction,pivot1=1,pivot2=start)
class CryptoTests(unittest.TestCase):
    def test_seed_selection_checks_history_and_excludes_stablecoins(self):
        class Fake:
            def api(self,*args):
                return {'symbols':[dict(symbol=s+'USDT',baseAsset=s,quoteAsset='USDT',status='TRADING',isSpotTradingAllowed=True) for s in ('AAA','BBB','NEW','USDC','BTCUP')]}
            def klines(self,symbol,frame,start,end):
                count=3 if symbol=='NEWUSDT' else 250
                return [candle(end-(count-i)*7*b.DAY,end-(count-i-1)*7*b.DAY) for i in range(count)]
        a=b.universe(Fake(),7,2,1000000000000);z=b.universe(Fake(),7,2,1000000000000)
        self.assertEqual(a,z);self.assertEqual(set(a['selected']),{'AAAUSDT','BBBUSDT'})
        self.assertNotIn('USDCUSDT',a['current_candidate_pool']);self.assertNotIn('BTCUPUSDT',a['current_candidate_pool'])
    def test_archive_checksum_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            history=b.History(tmp)
            with patch.object(history,'raw',side_effect=[b'corrupt zip',b'0000 file.zip']):
                with self.assertRaisesRegex(ValueError,'checksum'):history.month('BTCUSDT','1m','2025-11')
    def case(self):
        spec=b.week_spec(date(2026,3,8));first=spec['period_start'];sunday=spec['close']-b.DAY
        return spec,dict(symbol='TEST',weekly=[candle(first-7*b.DAY,first)],daily=[candle(first+i*b.DAY,first+(i+1)*b.DAY) for i in range(6)],minutes=[candle(t) for t in range(sunday,spec['expires'],60000)])
    def test_utc_boundaries_and_annual_window(self):
        spec=b.week_spec(date(2026,3,8));self.assertEqual(b.stamp(spec['start']),'2026-03-08T21:00+00:00');self.assertEqual(b.stamp(spec['expires']),'2026-03-10T00:00+00:00')
        self.assertEqual(len(b.week_dates(date(2025,11,1),date(2026,11,1))),52)
        with self.assertRaises(ValueError):b.week_spec(date(2026,3,6))
    def test_future_weekly_and_minutes_excluded(self):
        spec,case=self.case();at=spec['start'];before=b.parent_at(case,spec,at)
        case['weekly'].append(candle(spec['period_start'],spec['close'],999));case['minutes'][-1]=candle(case['minutes'][-1].start,close=999)
        self.assertEqual(before,b.parent_at(case,spec,at));self.assertEqual(b.parent_at(case,spec,spec['expires'])[-1].end,spec['close'])
    def test_provisional_entry_and_later_invalid_kept(self):
        spec,case=self.case();at=spec['start']+900000
        rows,_=b.replay(spec,{'TEST':case},{},lambda bars,*a:[signal(spec['period_start'])] if bars[-1].end<at+60000 else [],lambda *a:({at:[('15m',candle(at-900000,at),signal(at-900000))]},{}))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['higher_status'],'PROVISIONAL');self.assertEqual(rows[0]['parent_invalidated_at'],at+60000)
    def test_closed_parent_entry_after_boundary_and_four_per_tf(self):
        spec,case=self.case();schedule={}
        for n in range(1,7):
            at=spec['close']+n*900000;schedule[at]=[(tf,candle(at-900000,at),signal(at-900000)) for tf in ('15m','30m')]
            schedule[at].append(('1h',candle(at-900000,at),signal(at-900000,'bearish')))
        rows,_=b.replay(spec,{'TEST':case},{},lambda *a:[signal(spec['period_start'])],lambda *a:(schedule,{}))
        self.assertEqual(len(rows),8);self.assertTrue(all(r['higher_status']=='CLOSED' for r in rows));self.assertEqual({r['tf'] for r in rows},{'15m','30m'})
    def test_nonempty_causal_detector(self):
        closes=[100+(i%13)*.3 for i in range(270)]+[98,97,99,96,95,98]
        bars=[candle(100000000+i*60000,100000000+(i+1)*60000,p) for i,p in enumerate(closes)];rules=load_rules()
        full=detect(bars,rules,'T','15m',provisional=True,include_hidden=False);self.assertTrue(full)
        for n in (250,270,273):self.assertEqual(detect(bars[:n],rules,'T','15m',provisional=True,include_hidden=False),[s for s in full if s['confirmed_at']<=bars[n-1].end])
    def test_missing_history_fails_parent_and_aggregation(self):
        spec,case=self.case();case['daily'].pop()
        with self.assertRaises(ValueError):b.parent_at(case,spec,spec['start'])
        self.assertEqual(b.aggregate([candle(0),candle(120000)],'15m'),[])
        with self.assertRaises(ValueError):b.current_signals([candle(i*60000) for i in range(249)],load_rules(),'T','weekly')
    def test_directional_metrics_exclude_entry_candle_and_future(self):
        entry=dict(entry_time=60000,entry=100,direction='bullish');bars=[Candle(0,60000,100,999,1,100),Candle(60000,120000,100,104,98,102),Candle(120000,180000,102,999,1,100)]
        o=b.outcome(entry,bars,120000,180000);self.assertEqual((o['return_pct'],o['max_gain_pct'],o['max_dd_pct']),(2,4,2));self.assertEqual(o['max_gain_time'],60000)
        o=b.outcome(entry|{'direction':'bearish'},bars,120000,180000);self.assertEqual((o['return_pct'],o['max_gain_pct'],o['max_dd_pct']),(-2,2,4))
        self.assertEqual(b.outcome(entry,bars[:2],180000,180000)['status'],'incomplete_history')
    def test_microseconds_and_milliseconds(self):
        row=[1700000000000,'100','101','99','100',0,1700000059999]
        micro=[1700000000000000,*row[1:6],1700000059999999]
        self.assertEqual(b.parse_rows([row],1800000000000),b.parse_rows([micro],1800000000000))
    def test_selection_chronological_not_hindsight(self):
        a=dict(week='w',ticker='T',direction='bullish',entry_time=10,tf='1h');z=a|dict(entry_time=20,tf='15m')
        self.assertEqual(b.select_first([z,a]),[a]);self.assertEqual(b.select_first([a,a|{'tf':'15m'}]),[a|{'tf':'15m'}])
if __name__=='__main__':unittest.main()
