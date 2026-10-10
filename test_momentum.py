import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scanner import Candle
from platform_app import Store
import momentum

DAY=86400000

def trend():
    bars=[]
    for i in range(80):
        close=100+i*.2
        if i==78:close+=1
        if i==79:close+=1.2
        bars.append(Candle(i*DAY,(i+1)*DAY,close-.15,close+.75,close-.9,close))
    return bars


class MomentumTests(unittest.TestCase):
    def test_confirmed_buy_sell_and_no_chasing(self):
        bars=trend();s=momentum.candidate(bars,'X','stock')
        self.assertIsNotNone(s);self.assertEqual(s['direction'],'buy')
        mirror=[Candle(b.start,b.end,240-b.open,240-b.low,240-b.high,240-b.close) for b in bars]
        self.assertEqual(momentum.candidate(mirror,'X','stock')['direction'],'sell')
        bars[-1]=Candle(bars[-1].start,bars[-1].end,119,121,118,120)
        self.assertIsNone(momentum.candidate(bars,'X','stock'))

    def test_need_second_close_and_minimum_history(self):
        bars=trend()
        self.assertIsNone(momentum.candidate(bars[:79],'X','crypto'))
        last=bars[-1];bars[-1]=Candle(last.start,last.end,last.open,last.high,115,115.5)
        self.assertIsNone(momentum.candidate(bars,'X','crypto'))

    def test_future_bars_cannot_change_candidate(self):
        bars=trend();s=momentum.candidate(bars,'X','stock')
        future=bars+[Candle(80*DAY,81*DAY,130,140,100,135)]
        self.assertEqual(s,momentum.candidate(future,'X','stock',79))

    def test_next_open_gap_and_stop_before_target(self):
        s=momentum.candidate(trend(),'X','crypto');entry=s['entry']
        both=Candle(80*DAY,81*DAY,entry,s['target']+1,s['stop']-1,entry)
        f=momentum.outcome(s,[both]);self.assertEqual(f['status'],'stop');self.assertLess(f['net_pct'],0)
        gap=Candle(80*DAY,81*DAY,s['entry_max']+1,s['entry_max']+2,s['entry_max'],s['entry_max']+1)
        self.assertEqual(momentum.outcome(s,[gap])['status'],'skipped gap')
        self.assertEqual(momentum.outcome(s|dict(published_at=80*DAY+1),[both])['status'],'awaiting entry')

    def test_one_winner_per_market_close(self):
        bars=trend();r=momentum.replay({('stock','A'):bars,('stock','B'):bars,('crypto','C'):bars})
        self.assertEqual([(s['market'],s['symbol']) for s in r['signals']],[('crypto','C'),('stock','A')])
        self.assertEqual(r['metrics']['closed_trades'],0)

    def test_stock_entry_uses_session_open_not_midnight_bar_timestamp(self):
        from scanner import timestamp
        s=momentum.candidate(trend(),'X','stock')
        s.update(confirmed_at=timestamp('2026-10-09T20:00:00Z'),published_at=timestamp('2026-10-12T05:00:00Z'))
        opening=timestamp('2026-10-12T13:30:00Z')
        s.update(momentum.entry_session('stock',s['published_at']))
        self.assertEqual(s['entry_open_at'],opening)
        b=Candle(timestamp('2026-10-12T04:00:00Z'),timestamp('2026-10-12T20:00:00Z'),s['entry'],s['entry']+.1,s['entry']-.1,s['entry'])
        self.assertEqual(momentum.outcome(s,[b])['entry_at'],opening)

    def test_missing_entry_session_never_substitutes_later_candle(self):
        s=momentum.candidate(trend(),'X','crypto')
        s['entry_open_at']=80*DAY
        b=Candle(81*DAY,82*DAY,s['entry'],s['entry']+.1,s['entry']-.1,s['entry'])
        self.assertEqual(momentum.outcome(s,[b])['status'],'unavailable')

    def test_persist_restart_and_unavailable_not_no_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db.sqlite3')
            with patch('weekly_stock_screen.universe',return_value=(['X'],[])),patch('alpaca_feed.fetch_alpaca_multi',side_effect=ValueError):
                result=momentum.scan(store,dict(assets=[]),now=1780000000000)
            self.assertEqual(result['results'][0]['status'],'unavailable')
            self.assertEqual(result['status'],'partial')
            with patch('alpaca_feed.fetch_alpaca_multi') as fetch:
                same=momentum.scan(store,dict(assets=[]),now=1780000001000)
                fetch.assert_not_called();self.assertEqual(same,result)
            self.assertEqual(momentum.view(store)['latest'],result)
            from dashboard_api import read
            self.assertEqual(read(store,'/api/momentum?source=backtest')['strategy'],'momentum')
            with self.assertRaises(ValueError):read(store,'/api/momentum?source=other')

if __name__=='__main__':unittest.main()
