from test_postgres_support import TestStore as Store, PostgresTestCase
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from csv_backtests import parse, run, save, view
from test_pin_bars import bars
import pin_bars


def text(candles):
    return 'time,open,high,low,close,EMA,EMA\n'+'\n'.join(f'{b.start},{b.open},{b.high},{b.low},{b.close},999,999' for b in candles)

class CSVTests(PostgresTestCase):
    def test_uploaded_dates_warmup_open_exclusion_and_no_alerts(self):
        candles=bars();body=dict(csv=text(candles),symbol='TEST',timeframe='4h',strategy='pin-bars')
        report=run(body,candles[-2].end)
        self.assertEqual(report['excluded_open_candles'],1)
        self.assertEqual(report['start_at'],candles[0].start)
        self.assertEqual(report['end_at'],candles[-2].end)
        self.assertEqual(report['signal_count'],0)
        report=run(body,candles[-1].end)
        self.assertEqual(report['signal_count'],1)
        self.assertEqual(report['signals'][0]['confirmed_at'],candles[-1].end)
        self.assertEqual(report['signals'][0]['forward']['5']['status'],'incomplete')
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db')
            saved=save(store,body)
            self.assertEqual(view(store,saved['report']['id'])['report']['signal_count'],1)
            self.assertEqual(store.rows(),[])
            with store.connect() as db:
                self.assertFalse(db.execute("SELECT 1 FROM notification_queue LIMIT 1").fetchone())

    def test_schema_timezone_duplicates_invalid_ohlc_and_limits(self):
        for csv in ['time,open,high,low,close\n2026-01-01T00:00:00,1,2,0,1',
                    'time,open,high,low,close\n1700000000,1,0,2,1',
                    'time,open,high,low,close\n1700000000,1,2,0,1\n1700000000,1,2,0,1',
                    'time,open,high,low,close\n1700000000,nan,2,0,1',
                    'time,open\n1700000000,1']:
            with self.assertRaises(ValueError):parse(csv,'4h',2000000000000)
        with self.assertRaises(ValueError):parse('x'*2000001,'4h',2000000000000)
        with self.assertRaises(ValueError):run(dict(csv=text(bars()[:21]),symbol='TEST',timeframe='4h'))

    def test_explicit_close_unsorted_rows_and_unix_seconds(self):
        csv='timestamp,closed_at,open,high,low,close\n2026-01-02T00:00:00Z,2026-01-02T04:00:00Z,1,2,0,1\n2026-01-01T00:00:00Z,2026-01-01T04:00:00Z,1,2,0,1'
        parsed,excluded,alignment=parse(csv,'4h',2000000000000)
        self.assertLess(parsed[0].start,parsed[1].start)
        self.assertEqual(alignment,'explicit closed_at')
        self.assertEqual(parse('time,open,high,low,close\n1700000000,1,2,0,1','4h',2000000000000)[0][0].start,1700000000000)

    def test_pin_alert_queue_is_atomic_deduplicated_and_suppressed_in_catchup(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db');candles=bars();asset=dict(id='TEST',provider='csv');rules=pin_bars.load_rules()
            pin_bars.scan(store,asset,'4h',candles[:-1],rules,candles[-2].end)
            pin_bars.scan(store,asset,'4h',candles,rules,candles[-1].end)
            pin_bars.scan(store,asset,'4h',candles,rules,candles[-1].end)
            with store.connect() as db:
                row=db.execute('SELECT text,status FROM notification_queue').fetchall()
                self.assertEqual(len(row),1);self.assertIn('Pin-Bars',row[0][0]);self.assertEqual(row[0][1],'pending')
            second=asset|dict(exchange='other')
            pin_bars.scan(store,second,'4h',candles[:-1],rules,candles[-2].end)
            pin_bars.scan(store,second,'4h',candles,rules,candles[-1].end,catchup=True)
            with store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_queue').fetchone()[0],1)
