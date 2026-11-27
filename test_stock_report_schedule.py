"""Report-only schedule/data regressions; all external transport is mocked."""
import json
import os
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch

from pypdf import PdfReader
import stock_reports as reports
from scanner import Candle
from platform_app import load_rules
from test_stock_reports import fixture
from test_postgres_support import PostgresTestCase, TestStore


def ny(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=reports.NY).timestamp()*1000)


def run_at(value, job):
    return reports.new_run(reports.schedules(ny(value))[job], load_rules())


class ScheduleTests(unittest.TestCase):
    def test_daily_waits_for_close_data_and_has_only_rsi_strategies(self):
        self.assertNotIn('daily', reports.due(ny('2026-11-23T16:19:59')))
        run = run_at('2026-11-23T16:20:00', 'daily')
        self.assertEqual(run['strategies'], ['rsi','rsi-pb'])
        self.assertEqual(run['cutoff'], ny('2026-11-23T16:00:00'))
        self.assertEqual(run['period']['start'], ny('2026-11-23T00:00:00'))
        self.assertEqual(run['period']['end'], ny('2026-11-24T00:00:00'))
        self.assertEqual(reports.due(ny('2026-11-26T16:20:00')), []) # Thanksgiving
        self.assertEqual(reports.due(ny('2026-11-28T16:20:00')), []) # Saturday

    def test_early_closes_and_holiday_last_session(self):
        for value in ('2026-11-27T11:00:00', '2026-12-24T11:00:00'):
            run = run_at(value, 'weekly-preclose')
            self.assertEqual(run['cutoff'], ny(value))
            self.assertEqual(run['expires_at']-run['cutoff'], 2*3600_000)
            self.assertEqual(run['strategies'], ['pin-bars','bb-pin-bars'])
        self.assertNotIn('weekly-preclose',reports.due(ny('2026-11-27T10:59:59')))
        self.assertNotIn('weekly-preclose',reports.due(ny('2026-11-27T13:00:00')))
        self.assertIn('daily',reports.due(ny('2026-11-27T13:20:00')))
        self.assertIn('weekly-preclose',reports.due(ny('2026-04-02T14:00:00'))) # Good Friday
        self.assertNotIn('weekly-preclose',reports.due(ny('2026-04-03T14:00:00')))

    def test_month_end_coincidence_year_rollover_and_dst(self):
        for day in ('2026-10-30','2027-12-31'):
            self.assertEqual(set(reports.due(ny(day+'T14:00:00'))), {'weekly-preclose','monthly-preclose'})
            self.assertEqual(reports.due(ny(day+'T16:20:00')), ['daily'])
        run=run_at('2026-11-30T14:00:00','monthly-preclose')
        self.assertEqual(run['period']['end'],ny('2026-12-01T00:00:00'))
        for day in ('2026-03-06','2026-03-13','2026-10-30','2026-11-06'):
            self.assertIn('weekly-preclose',reports.due(ny(day+'T14:00:00')))
            self.assertIn('daily',reports.due(ny(day+'T16:20:00')))

    def test_daily_retry_identity_survives_israel_midnight(self):
        before=reports.schedules(ny('2026-11-23T16:20:00'))['daily']
        after=reports.schedules(ny('2026-11-23T18:00:00'))['daily']
        self.assertEqual(before,after)
        with patch('stock_reports.time.time',return_value=ny('2026-11-23T18:00:00')/1000):
            self.assertTrue(reports.eligible(before))
        with patch('stock_reports.time.time',return_value=ny('2026-11-24T00:00:00')/1000):
            self.assertFalse(reports.eligible(before))

    def test_legacy_rsi_schedule_and_previous_period_preserved(self):
        instant=int(datetime(2026,11,1,8,tzinfo=reports.ZONE).timestamp()*1000)
        for name,job in reports.schedules(instant).items():
            self.assertIn(name,('weekly','monthly'))
            self.assertEqual(job['strategies'],['rsi','rsi-pb'])
            run=reports.new_run(job,load_rules())
            self.assertEqual(run['period'],reports.period(name,instant))

    def test_daily_completed_candles_only_and_strategy_isolation(self):
        run=run_at('2026-11-23T16:20:00','daily')
        run['membership']=fixture()['membership']
        start=ny('2026-11-23T00:00:00');end=run['cutoff']
        bars=[Candle(start-(249-i)*86400_000,end-(249-i)*86400_000,10,14,9,12) for i in range(250)]
        future=Candle(start+86400_000,end+86400_000,10,14,9,12)
        signal=dict(direction='bullish',pivot2=start,candle_at=start,confirmed_at=end)
        with patch('scanner.detect',return_value=[signal]) as rsi,patch('rsi_pb.detect',return_value=[signal]) as pb, \
             patch('pin_bars.detect') as pin,patch('bb_pin_bars.detect') as bb:
            rows,coverage=reports.evaluate('AAPL',bars+[future],run)
        self.assertEqual({r['strategy'] for r in rows},{'rsi','rsi-pb'})
        self.assertEqual(len(coverage),2)
        self.assertEqual(rsi.call_args.args[0],bars)
        self.assertEqual(pb.call_args.args[0],bars)
        pin.assert_not_called();bb.assert_not_called()

    def test_preclose_evaluation_and_pdf_labels(self):
        run=run_at('2026-11-27T11:00:00','weekly-preclose')
        run['membership']=fixture()['membership']
        start=run['period']['start'];end=run['cutoff']
        bars=[Candle(start-(249-i)*604800_000,end-(249-i)*604800_000,10,14,9,12) for i in range(250)]
        signal=dict(direction='bearish',candle_at=start,confirmed_at=end)
        with patch('scanner.detect') as rsi,patch('rsi_pb.detect') as rsi_pb, \
             patch('pin_bars.detect',return_value=[signal]),patch('bb_pin_bars.detect',return_value=[signal]):
            run['rows'],run['coverage']=reports.evaluate('AAPL',bars,run)
        rsi.assert_not_called();rsi_pb.assert_not_called()
        run['market_caps']=dict(source='fixture',retrieved_at='2026-11-27T16:00:00Z',values={'AAPL':dict(usd=2.45e12,as_of='2026-11-27')})
        docs=reports.artifacts(run)
        self.assertEqual(len(docs),3) # two bearish PDFs plus combined bullish no-matches
        for doc in docs:
            self.assertIn('preclose',doc['filename'])
            self.assertIn('Provisional',doc['caption'])
        text=PdfReader(BytesIO(docs[0]['content'])).pages[0].extract_text()
        for word in ('PROVISIONAL','Latest price (USD)','Market cap (USD)','$2.45T','Snapshot:'):
            self.assertIn(word,text)
        self.assertNotIn('Close (USD)',text)
        self.assertNotIn('RSI',PdfReader(BytesIO(docs[-1]['content'])).pages[0].extract_text())


class DataTests(unittest.TestCase):
    def test_market_cap_validation_mapping_and_failures(self):
        from stock_report_data import market_caps
        rows=[dict(symbol='BRK-B',marketCap=1.2e12,date='2026-11-23'),
              dict(symbol='AAPL',marketCap=float('nan')),dict(symbol='BAD',marketCap=1),
              dict(symbol='ZERO',marketCap=0),dict(symbol='BOOL',marketCap=True)]
        with patch.dict(os.environ,{'FMP_API_KEY':'fake'}),patch('fmp_feed.request_json',return_value=rows) as request:
            caps=market_caps(['BRK.B','AAPL','BRK.B','ZERO','BOOL'])
        self.assertEqual(caps['values'],{'BRK.B':dict(usd=1.2e12,as_of='2026-11-23')})
        request.assert_called_once_with('market-capitalization-batch',symbols='AAPL,BOOL,BRK-B,ZERO')
        with patch.dict(os.environ,{'FMP_API_KEY':'fake'}),patch('fmp_feed.request_json',side_effect=ValueError('denied')):
            self.assertEqual(market_caps(['AAPL'])['values'],{})
        with patch.dict(os.environ,{'FMP_API_KEY':''}),patch('fmp_feed.request_json') as request:
            self.assertEqual(market_caps(['AAPL'])['values'],{})
            request.assert_not_called()

    def test_market_cap_formatting(self):
        from stock_report_pdf import market_cap
        self.assertEqual(market_cap(24.6e9),'$24.60B')
        self.assertEqual(market_cap(8.3e6),'$8.30M')
        for value in (None,True,0,-1,float('inf'),'100'):
            self.assertEqual(market_cap(value),'Unavailable')

    def test_minute_fetch_filters_open_bar_and_paginates(self):
        from stock_report_data import fetch_batch
        responses=[dict(bars={'AAPL':[dict(t='2026-11-23T18:59:00Z',o=10,h=11,l=9,c=10)]},next_page_token='more'),
                   dict(bars={'AAPL':[dict(t='2026-11-23T19:00:00Z',o=10,h=11,l=9,c=10)]})]
        with patch('alpaca_feed.request_page',side_effect=responses) as request:
            bars=fetch_batch(['AAPL'],'1min',ny('2026-11-23T14:00:00'),390,'sip',start_ms=ny('2026-11-23T09:30:00'))
        self.assertEqual(len(bars['AAPL']),1)
        self.assertEqual(bars['AAPL'][0].end,ny('2026-11-23T14:00:00'))
        self.assertEqual(request.call_args.args[0]['timeframe'],'1Min')

    def test_preclose_snapshot_does_not_use_later_aggregate_and_checks_coverage(self):
        from stock_report_data import fetch_preclose
        run=run_at('2026-11-27T11:00:00','weekly-preclose')
        start=run['period']['start'];cutoff=run['cutoff']
        history=[Candle(start-604800_000,start-3*86400_000,9,12,8,10)]
        daily=[Candle(ny(f'2026-11-{day}T00:00:00'),ny(f'2026-11-{day}T16:00:00'),10,14,9,12) for day in (23,24,25)]
        today=[Candle(ny('2026-11-27T09:30:00'),ny('2026-11-27T09:31:00'),12,15,11,14),
               Candle(cutoff-60_000,cutoff,14,16,13,15)]
        def fetched(symbols,frame,*args,**kwargs):
            return {'AAPL':history if frame=='weekly' else daily if frame=='daily' else today}
        with patch('stock_report_data.fetch_batch',side_effect=fetched):
            result=fetch_preclose(['AAPL'],run)['AAPL']
        self.assertEqual(result[-1],Candle(start,cutoff,10,16,9,15))
        self.assertEqual(result[:-1],history)
        daily.pop() # Missing Wednesday: cannot label this as evaluated/no-match.
        with patch('stock_report_data.fetch_batch',side_effect=fetched):
            self.assertEqual(fetch_preclose(['AAPL'],run)['AAPL'],history)


class PersistenceTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        self.config={'stock_reports':{'enabled':True}}

    def execute(self, timestamp, failure=None):
        with patch('stock_reports.time.time',return_value=timestamp/1000), \
             patch('stock_report_data.membership',return_value=fixture()['membership']), \
             patch('stock_report_data.fetch_batch',return_value={}) as fetch, \
             patch('stock_report_data.fetch_preclose',return_value={}) as preclose, \
             patch('stock_report_data.market_caps',return_value={'values':{}}), \
             patch('local_settings.delivery_dry_run',return_value=False), \
             patch('telegram_documents.deliver_document',side_effect=failure) as send:
            reports.run_due(self.store,self.config,load_rules(),timestamp)
        return fetch,preclose,send

    def test_daily_retry_after_israel_midnight_reuses_persisted_pdf(self):
        self.execute(ny('2026-11-23T16:20:00'),RuntimeError('transport'))
        with self.store.connect() as db:
            before=db.execute('SELECT id,content FROM stock_report_documents').fetchall()
        fetch,preclose,send=self.execute(ny('2026-11-23T18:00:00'))
        self.assertFalse(fetch.called);self.assertFalse(preclose.called)
        self.assertEqual(send.call_count,1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT id,content FROM stock_report_documents').fetchall(),before)
            self.assertEqual(db.execute('SELECT count(*) FROM stock_report_runs').fetchone()[0],1)
        self.assertFalse(self.execute(ny('2026-11-23T18:05:00'))[-1].called)

    def test_weekly_monthly_preclose_and_daily_are_distinct_runs(self):
        _,preclose,send=self.execute(ny('2026-10-30T14:00:00'))
        self.assertEqual(preclose.call_count,2);self.assertEqual(send.call_count,2)
        fetch,preclose,send=self.execute(ny('2026-10-30T16:20:00'))
        self.assertEqual(fetch.call_count,1);self.assertFalse(preclose.called)
        self.assertEqual(send.call_count,1)
        with self.store.connect() as db:
            payloads=[json.loads(r[0]) for r in db.execute('SELECT payload FROM stock_report_runs')]
        self.assertEqual(len(payloads),3)
        self.assertEqual({tuple(p['strategies']) for p in payloads},{reports.PIN_STRATEGIES,reports.RSI_STRATEGIES})

    def test_expired_preclose_never_delivers_after_close(self):
        self.execute(ny('2026-11-27T11:00:00'),RuntimeError('transport'))
        _,preclose,send=self.execute(ny('2026-11-27T13:00:00'))
        self.assertFalse(preclose.called);self.assertFalse(send.called)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM stock_report_runs').fetchone()[0],'missed')

    def test_schema_upgrade_preserves_saved_documents_and_allows_daily(self):
        from pathlib import Path
        import psycopg
        from test_postgres_support import test_dsn
        self.execute(ny('2026-11-27T11:00:00'))
        with psycopg.connect(test_dsn(),autocommit=True) as db:
            before=db.execute('SELECT id,content FROM bot_runtime.stock_report_documents').fetchall()
            db.execute('ALTER TABLE bot_runtime.stock_report_runs DROP CONSTRAINT stock_report_runs_frame_check')
            db.execute("ALTER TABLE bot_runtime.stock_report_runs ADD CONSTRAINT stock_report_runs_frame_check CHECK(frame IN ('weekly','monthly'))")
            migration=(Path(__file__).parent/'cloud/stock-reports.sql').read_text()
            db.execute(migration,prepare=False)
            db.execute(migration,prepare=False)
            self.assertEqual(db.execute('SELECT id,content FROM bot_runtime.stock_report_documents').fetchall(),before)
        self.execute(ny('2026-11-27T13:20:00'))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM stock_report_runs WHERE frame='daily'").fetchone()[0],1)
