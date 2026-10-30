"""Offline UI/controller and subprocess event-contract checks."""
import contextlib
from dataclasses import asdict
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

from adhoc.rsi_pb_scanner import ScanJob, filtered_patterns, scan_command, stock_rows, PREFIX
from test_adhoc_rsi_pb import scan
from test_rsi_pb import bars
import rsi_pb


class AdHocUITests(unittest.TestCase):
    def test_command_uses_active_interpreter_and_absolute_paths(self):
        command=scan_command(['daily','weekly'],1,100,feed='SIP',membership='Repository snapshot',output=Path('/tmp/space in output'))
        self.assertEqual(command[0],sys.executable)
        self.assertIn('--events',command)
        self.assertIn('/tmp/space in output',command)
        self.assertEqual(command[command.index('--feed')+1],'sip')
        self.assertTrue(Path(command[2]).is_absolute())
        for frames,recent,history in [([],10,250),(['daily'],'bad',250),(['daily'],20,100)]:
            with self.assertRaises(ValueError):scan_command(frames,recent,history)

    def test_combined_filters_and_unique_stocks(self):
        first=dict(symbol='AAA',timeframe='daily',direction='bullish',structural_breach=False,age_closed_candles=0,confirmation_ny='2026-10-28')
        second=first|dict(timeframe='weekly',direction='bearish',structural_breach=True,age_closed_candles=2)
        patterns=[first,second,first|dict(symbol='BBB')]
        self.assertEqual(filtered_patterns(patterns,'aa','Bullish','daily',True,True),[first])
        rows=stock_rows(patterns)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['timeframes'],'weekly, daily')
        self.assertEqual(rows[0]['unbreached'],'Yes')
        self.assertEqual(rows[0]['latest'],'Yes')

    def test_event_stream_has_manifest_live_rows_and_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);universe=root/'symbols.json';universe.write_text('["TEST"]')
            buffer=io.StringIO()
            with patch.dict(os.environ,ALPACA_API_KEY='offline',ALPACA_SECRET_KEY='offline'), patch.object(scan,'fetch_batch',return_value={'TEST':bars()}), contextlib.redirect_stdout(buffer):
                self.assertEqual(scan.main(['--timeframes','4h','--universe-file',str(universe),'--output',str(root/'run'),'--events']),0)
            events=[json.loads(line[len(PREFIX):]) for line in buffer.getvalue().splitlines() if line.startswith(PREFIX)]
            self.assertEqual([e['event'] for e in events],['started','batch','finished'])
            self.assertEqual(events[1]['completed'],events[1]['total'])
            self.assertEqual(events[1]['coverage'][0]['status'],'qualified')
            self.assertEqual(events[1]['signals'][0]['symbol'],'TEST')

    def test_real_child_process_resume_needs_no_provider_requests(self):
        # Run the actual CLI in another interpreter using one complete saved batch.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'candles').mkdir()
            manifest=dict(cutoff_ms=1900000000000,universe=dict(symbols=['TEST'],source='offline fixture',as_of='2026-10-29'),feed='sip',timeframes=['4h'],recent_bars=10,history_bars=250,batch_size=10,rules=rsi_pb.load_rules())
            (root/'manifest.json').write_text(json.dumps(manifest))
            (root/'candles'/'4h-0000.json').write_text(json.dumps({'TEST':[asdict(b) for b in bars()]}))
            events=queue.Queue()
            with patch.dict(os.environ,ALPACA_API_KEY='offline',ALPACA_SECRET_KEY='offline'):
                job=ScanJob(scan_command([],1,250,resume=root),events);job.start();job.thread.join(20)
            self.assertFalse(job.thread.is_alive())
            received=[]
            while not events.empty():received.append(events.get_nowait())
            self.assertEqual([value for kind,value in received if kind=='exit'],[0])
            self.assertEqual([value['event'] for kind,value in received if kind=='event'],['started','batch','finished'])
            self.assertTrue((root/'report.md').exists())


if __name__=='__main__':unittest.main()
