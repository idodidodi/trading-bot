import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from decimal import Decimal as D
from repair_kraken_minutes import aggregate, repair
from chasing_research import outcome, detect_series
from scanner import Candle
from platform_app import load_rules


def trade(at,price,volume,identity):return [str(price),str(volume),D(at),'b','l','',identity]


def bar(at,price=100,volume=1):
    return dict(time=at,open=D(price),high=D(price),low=D(price),close=D(price),Volume=D(volume),source_status='tradingview')


class RepairTests(unittest.TestCase):
    def test_absent_trade_interval_is_verified_empty_not_recovered_candle(self):
        source={0:bar(0),120:bar(120)}
        rows=[trade(10,100,1,1),trade(130,100,1,2),trade(190,101,1,3)]
        recovered,empty,unresolved,evidence=repair(source,lambda since:(rows,190*10**9,{'cursor':'190000000000'}))
        self.assertEqual(recovered,{})
        self.assertEqual(empty,{60});self.assertEqual(unresolved,set())
        self.assertEqual(evidence['overlap_candles'],2)

    def test_actual_gap_trade_is_reconstructed_and_overlap_mismatch_fails(self):
        source={0:bar(0),120:bar(120)}
        rows=[trade(10,100,1,1),trade(70,99,2,2),trade(80,102,3,3),trade(130,100,1,4),trade(190,101,1,5)]
        recovered,empty,_,_=repair(source,lambda since:(rows,190*10**9,{}))
        self.assertEqual(recovered[60]['Volume'],5)
        self.assertEqual(recovered[60]['open'],99);self.assertEqual(recovered[60]['close'],102)
        self.assertFalse(empty)
        rows[0]=trade(10,101,1,1)
        with self.assertRaisesRegex(ValueError,'mismatch'):repair(source,lambda since:(rows,190*10**9,{}))
        _,_,_,evidence=repair(source,lambda since:(rows,190*10**9,{}),record_differences=True)
        self.assertEqual(len(evidence['overlap_conflicts']),1)
        self.assertEqual(source[0]['open'],100)

    def test_empty_observation_is_not_an_executable_entry(self):
        bars=[Candle(i*60000,(i+1)*60000,100,100,100,100) for i in range(1,9)]
        starts=[b.start for b in bars]
        result=outcome(bars,starts,60000,360000,'bullish',0,empty=[120000,180000,360000,420000])
        self.assertEqual(result['entry_at'],240000)
        self.assertEqual(result['end_at'],480000)
        self.assertEqual(result['exit_delay_ms'],120000)

    def test_partial_minute_pages_deduplicate_trade_ids(self):
        source={0:bar(0),120:bar(120)}
        pages=iter([([trade(10,100,1,1),trade(70,99,2,2)],70*10**9,{}),
                    ([trade(70,99,2,2),trade(130,100,1,3),trade(190,101,1,4)],190*10**9,{})])
        recovered,empty,unresolved,evidence=repair(source,lambda since:next(pages))
        self.assertEqual(recovered[60]['Volume'],2)
        self.assertEqual(evidence['pages'],2)
        self.assertFalse(empty|unresolved)

    def test_empty_csv_requires_matching_provenance(self):
        from chasing_research import read_series
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'1m.csv'
            p.write_text('time,open,high,low,close,Volume,source_status\n2026-10-01T00:00:00Z,100,100,100,100,1,tradingview\n2026-10-01T00:01:00Z,100,100,100,100,0,verified_no_trades\n')
            with self.assertRaisesRegex(ValueError,'manifest'):read_series(p,'1m',1790899320000)
            m=Path(d)/'manifest.json'
            m.write_text(json.dumps(dict(include_empty=True,output_hashes={'1m':hashlib.sha256(p.read_bytes()).hexdigest()})))
            _,_,audit=read_series(p,'1m',1790899320000)
            self.assertEqual(len(audit['verified_empty']),1)
            m.write_text(json.dumps(dict(include_empty=True,output_hashes={'1m':'bad'})))
            with self.assertRaisesRegex(ValueError,'provenance'):read_series(p,'1m',1790899320000)

    def test_empty_rows_do_not_change_indicator_bar_sequence(self):
        from test_scanner import fixture
        original=fixture();expected=detect_series(original,{b.start:100 for b in original},load_rules(),'TEST','4h')
        # A time-continuity observation is not added to RSI or BB windows.
        shifted=[];empty=[]
        for i,b in enumerate(original):
            from dataclasses import replace
            shifted.append(replace(b,start=b.start+i*14400000,end=b.end+i*14400000))
            e=Candle(shifted[-1].end,shifted[-1].end+14400000,b.close,b.close,b.close,b.close)
            shifted.append(e);empty.append(e.start)
        got=detect_series(shifted,{b.start:(0 if b.start in empty else 100) for b in shifted},load_rules(),'TEST','4h',empty)
        self.assertEqual([(s['direction'],s['price1'],s['price2'],s['rsi2']) for s in got],
                         [(s['direction'],s['price1'],s['price2'],s['rsi2']) for s in expected])


if __name__=='__main__':unittest.main()
