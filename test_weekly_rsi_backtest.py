import unittest
import tempfile
from datetime import date,datetime
from unittest.mock import patch
from scanner import Candle,detect
from platform_app import load_rules
from research import weekly_rsi_backtest as b


def ms(text):return b.millis(datetime.fromisoformat(text).replace(tzinfo=b.NY))
def candle(start,end=None,close=100):return Candle(start,end or start+60000,100,max(101,close),min(99,close),close)
def signal(start,direction='bullish',first=1):return dict(direction=direction,pivot1=first,pivot2=start)


class WeeklyResearchTests(unittest.TestCase):
    def test_history_uses_full_rfc3339_and_explicit_sip(self):
        with tempfile.TemporaryDirectory() as tmp, patch('alpaca_feed.request_page',return_value={'bars':{'AAPL':[]}}) as fetch, patch.object(b.timer,'sleep'):
            b.History(tmp).get('AAPL','1Min',ms('2025-11-28T04:00'),ms('2025-11-28T20:00'))
            params=fetch.call_args.args[0]
            self.assertEqual(params['start'],'2025-11-28T09:00:00+00:00')
            self.assertEqual(params['feed'],'sip')

    def test_weekly_warmup_maps_renamed_symbols(self):
        with tempfile.TemporaryDirectory() as tmp, patch('alpaca_feed.request_page',return_value={'bars':{'META':[]}}) as fetch, patch.object(b.timer,'sleep'):
            b.History(tmp).get('META','1Week',ms('2020-08-01T00:00'),ms('2025-11-23T23:59'))
            self.assertEqual(fetch.call_args.args[0]['asof'],'2025-11-23')
    def test_calendar_early_close_holiday_and_dst(self):
        early=b.week_spec(date(2025,11,28))
        self.assertEqual(b.stamp(early['start']),'2025-11-28T10:00-05:00')
        self.assertEqual(b.stamp(early['close']),'2025-11-28T13:00-05:00')
        before=b.week_spec(date(2026,3,6))
        self.assertEqual(b.stamp(before['expires']),'2026-03-09T09:30-04:00')
        holiday=b.week_spec(date(2026,4,2))
        self.assertEqual(b.stamp(holiday['expires']),'2026-04-06T09:30-04:00')

    def test_completed_year_selection(self):
        dates=b.week_dates(date(2025,11,1),date(2026,11,1))
        self.assertEqual(len(dates),52)
        self.assertIn(date(2026,4,2),dates)

    def case(self):
        spec=b.week_spec(date(2026,3,13));day=ms('2026-03-13T09:30')
        case=dict(symbol='TEST',weekly=[candle(ms('2026-03-02T00:00'),ms('2026-03-06T16:00'))],
            daily=[candle(ms(f'2026-03-{d}T00:00'),ms(f'2026-03-{d}T16:00')) for d in ('09','10','11','12')],
            minutes=[candle(t) for t in range(day,spec['close'],60000)])
        return spec,case

    def test_weekly_snapshot_excludes_future_minutes_and_final_weekly_bar(self):
        spec,case=self.case();at=spec['start']+60000
        first=b.parent_at(case,spec,at)
        case['minutes'][-1]=candle(case['minutes'][-1].start,close=9999)
        case['weekly'].append(candle(spec['period_start'],spec['close'],9999))
        again=b.parent_at(case,spec,at)
        self.assertEqual(first,again)
        self.assertEqual(again[-1].end,at)
        self.assertEqual(again[-1].close,100)

    def test_entry_with_provisional_weekly_and_later_invalidation_kept(self):
        spec,case=self.case();entry=spec['start']+15*60000
        parent=signal(spec['period_start'])
        def parent_detector(bars,*args):return [parent] if bars[-1].end<entry+60000 else []
        def lower_provider(*args):return {entry:[('15m',candle(entry-900000,entry),signal(entry-900000))]}, {'15m':250}
        rows,review=b.replay(spec,{'TEST':case},{},parent_detector,lower_provider)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['entry_time'],entry)
        self.assertEqual(rows[0]['higher_status'],'PROVISIONAL')
        self.assertEqual(rows[0]['parent_invalidated_at'],entry+60000)
        self.assertEqual(rows[0]['entry'],100)

    def test_wrong_direction_and_four_per_tf_limit(self):
        spec,case=self.case();parent=signal(spec['period_start'])
        schedule={}
        for n in range(1,7):
            at=spec['start']+n*15*60000
            schedule[at]=[(tf,candle(at-900000,at),signal(at-900000)) for tf in ('15m','30m')]
            schedule[at].append(('1h',candle(at-900000,at),signal(at-900000,'bearish')))
        rows,_=b.replay(spec,{'TEST':case},{},lambda *a:[parent],lambda *a:(schedule,{}))
        self.assertEqual(len(rows),8)
        self.assertEqual({r['tf'] for r in rows},{'15m','30m'})

    def test_no_future_right_pivot_in_closed_precomputation(self):
        rules=load_rules()
        closes=[100+(i%13)*.3 for i in range(270)]+[98,97,99,96,95,98]
        bars=[candle(100000000+i*60000,100000000+(i+1)*60000,p) for i,p in enumerate(closes)]
        full=detect(bars,rules,'T','15m',provisional=True)
        self.assertTrue(full)  # The prefix comparison must exercise real divergences.
        for length in (250,270,273):
            prefix=detect(bars[:length],rules,'T','15m',provisional=True)
            self.assertEqual(prefix,[s for s in full if s['confirmed_at']<=bars[length-1].end])

    def test_horizon_uses_scheduled_minutes_not_observed_bars(self):
        start=ms('2026-03-06T19:45')
        self.assertEqual(b.stamp(b.horizon_end(start,'1h')),'2026-03-09T04:45-04:00')
        self.assertEqual(b.stamp(b.horizon_end(ms('2025-11-28T12:00'),'next_close')),'2025-11-28T13:00-05:00')

    def test_outcome_excludes_entry_candle_and_future_and_handles_short(self):
        start=ms('2026-03-13T14:00');entry=dict(entry_time=start,entry=100,direction='bullish')
        bars=[Candle(start-60000,start,100,999,1,100),Candle(start,start+60000,100,104,98,102),
              Candle(start+60000,start+120000,102,999,1,100)]
        result=b.outcome(entry,bars,start+60000,start+60000)
        self.assertEqual((result['max_gain_pct'],result['max_dd_pct'],result['return_pct']),(4,2,2))
        short=b.outcome(entry|{'direction':'bearish'},bars,start+60000,start+60000)
        self.assertEqual((short['max_gain_pct'],short['max_dd_pct'],short['return_pct']),(2,4,-2))
        self.assertEqual(b.outcome(entry,[],start+60000,start+60000)['status'],'incomplete')
        self.assertIsNone(b.outcome(entry,bars[:2],start+3600000,start+3600000)['return_pct'])

    def test_selection_cannot_replace_entry_using_future_lower_tf(self):
        first=dict(week='w',ticker='A',direction='bullish',entry_time=10,tf='1h')
        later=first|dict(entry_time=20,tf='15m')
        self.assertEqual(b.select_first([later,first]),[first])
        simultaneous=first|dict(tf='15m')
        self.assertEqual(b.select_first([first,simultaneous]),[simultaneous])

    def test_full_year_window_rejects_non_last_session(self):
        with self.assertRaises(ValueError):b.week_spec(date(2026,3,12))

    def test_missing_parent_data_never_creates_entry(self):
        spec,case=self.case();case['daily']=[]
        at=spec['start']+15*60000
        schedule={at:[('15m',candle(at-900000,at),signal(at-900000))]}
        rows,review=b.replay(spec,{'TEST':case},{},lambda *a:[signal(spec['period_start'])],lambda *a:(schedule,{}))
        self.assertEqual(rows,[])
        self.assertTrue(all(e['kind']=='data_unavailable' for e in review['audit']))


if __name__=='__main__':unittest.main()
