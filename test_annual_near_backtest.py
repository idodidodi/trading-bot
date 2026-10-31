import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from annual_near_backtest import dataset, excursion, recent_minutes, require_archive_coverage, detail_report, ranks
from kraken_archive import RemoteZip
from repair_kraken_minutes import Client
from decimal import Decimal


class AnnualTests(unittest.TestCase):
    def test_correlation_ranks_average_tied_volume_values(self):
        self.assertEqual(ranks([30,10,10,20]),[4,1.5,1.5,3])

    def test_missing_archive_coverage_cannot_be_called_empty(self):
        proof={'manifest':{'columns':['timestamp','open','high','low','close','volume','trades'],
                           'coverage':{'start':'2025-01-01','end':'2025-01-31'}}}
        from repair_kraken_minutes import timestamp
        require_archive_coverage([proof],timestamp('2025-01-02T00:00:00Z'),timestamp('2025-02-01T00:00:00Z'))
        with self.assertRaisesRegex(ValueError,'coverage'):
            require_archive_coverage([proof],timestamp('2025-01-02T00:00:00Z'),timestamp('2025-02-02T00:00:00Z'))

    def test_observation_followup_requires_same_direction(self):
        original=dict(confirmed_at=60000,direction='bullish',price2=100)
        opposite=dict(confirmed_at=120000,direction='bearish',timeframe='1m')
        matching=dict(confirmed_at=180000,direction='bullish',timeframe='1m')
        report=dict(roots=[dict(id='root',signal=original,status='stopped_favorable')],events=[],report_hash='test')
        rows=[(i*60,99,99,99,99,1,1) for i in range(10)]
        with tempfile.TemporaryDirectory() as d, \
             patch('chasing_research.read_series',return_value=([],{},dict(verified_empty=[]))), \
             patch('chasing_research.detect_series',side_effect=[[opposite,matching],[],[],[],[]]):
            details=detail_report(Path(d),report,{f:Path(d) for f in ('1h','30m','15m','5m','1m')},rows,600)
        self.assertEqual(details[0]['followup_at'],180)
        self.assertEqual(details[0]['followup_source'],'observation_only_after_stop')

    def test_extremes_recovery_and_failure_use_directional_baseline(self):
        rows=[(0,100,101,97,98,1,1),(60,98,102,98,101,1,1),
              (120,101,105,100,104,1,1)]
        r=excursion(rows,[0,60,120],0,180,100,'bullish')
        self.assertAlmostEqual(r['mae_pct'],3)
        self.assertEqual(r['failure_threshold_at'],60)
        self.assertEqual(r['recovery_at'],120)
        self.assertAlmostEqual(r['mfe_after_recovery_pct'],5)
        self.assertEqual(r['mfe_after_recovery_at'],180)
        # Mirror prices for a bearish signal: rising prices are adverse.
        mirror=[(t,200-o,200-l,200-h,200-c,v,n) for t,o,h,l,c,v,n in rows]
        b=excursion(mirror,[0,60,120],0,240,100,'bearish')
        self.assertAlmostEqual(b['mae_pct'],3)
        self.assertEqual(b['status'],'censored')
        self.assertEqual(b['recovery_at'],120)
        self.assertAlmostEqual(b['mfe_after_recovery_pct'],5)

    def test_recovery_does_not_assume_wick_order_within_one_minute(self):
        r=excursion([(0,100,110,97,105,1,1)],[0],0,60,100,'bullish')
        self.assertIsNone(r['recovery_at'])
        self.assertIsNone(r['mfe_after_recovery_pct'])
        self.assertEqual(r['first_favorable_close_at'],60)

    def test_dataset_labels_empty_minutes_and_aggregates_volume(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)
            rows=dataset(out,{86460:(99,101,98,100,2,3)},86400,100800,(86340,99),{})
            self.assertEqual(rows[0],(86400,99,99,99,99,0,0))
            self.assertEqual(rows[2],(86520,100,100,100,100,0,0))
            import csv
            with (out/'5m.csv').open() as f:bars=list(csv.DictReader(f))
            self.assertEqual(float(bars[0]['Volume']),2)
            self.assertEqual(bars[0]['source_status'],'kraken_native')
            self.assertEqual(bars[1]['source_status'],'verified_no_trades')
            from chasing_research import read_series
            _,_,audit=read_series(out/'1m.csv','1m',100800000)
            self.assertEqual(len(audit['verified_empty']),239)

    def test_recent_pages_deduplicate_boundary_trade(self):
        def t(at,price,identity):return [str(price),'1',Decimal(at),'b','l','',identity]
        class Fake:
            pages=iter([([t(1,100,1),t(61,99,2)],62000000000,{}),
                        ([t(61,99,2),t(121,101,3)],122000000000,{})])
            def page(self,since):return next(self.pages)
        minutes={}
        proof=recent_minutes(Fake(),0,120,minutes)
        self.assertEqual(minutes[60][-2:],(1.,1))
        self.assertNotIn(120,minutes)
        self.assertEqual(proof['pages'],2)

    def test_remote_zip_reads_across_parts_and_blocks(self):
        parts={'first':b'abcdefgh','second':b'ijklmn'}
        with tempfile.TemporaryDirectory() as d:
            with patch.object(RemoteZip,'range',lambda self,url,start,end:(parts[url][start:end+1],len(parts[url]))):
                stream=RemoteZip(list(parts),d,block=3)
                stream.seek(6)
                self.assertEqual(stream.read(6),b'ghijkl')
                stream.seek(-3,2)
                self.assertEqual(stream.read(),b'lmn')
                self.assertEqual(stream.read(),b'')

    def test_trade_cursor_tolerates_only_submicrosecond_rounding(self):
        with tempfile.TemporaryDirectory() as d:
            client=Client(d,'NEAR/USD');since=100000000060
            path=Path(d)/f'NEAR_USD-{since}.json'
            def write(at):
                path.write_text(json.dumps({'error':[],'result':{'NEAR/USD':[['1','1',at,'b','l','',1]],'last':str(since+1000000000)}}))
            write('100.0000000')
            self.assertEqual(len(client.page(since)[0]),1)
            write('99.9999980')
            with self.assertRaisesRegex(ValueError,'boundary'):client.page(since)


if __name__=='__main__':unittest.main()
