import json
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import stock_reports as reports
from platform_app import load_rules
from scanner import Candle
from test_postgres_support import PostgresTestCase, TestStore


def instant(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=reports.ZONE).timestamp()*1000)


def fixture(frame='weekly'):
    now=instant('2026-11-01T08:00:00')
    return dict(day='2026-11-01',frame=frame,period=reports.period(frame,now),cutoff=reports.period(frame,now)['end']-1,
                rules=reports.rules_snapshot(load_rules()),feed='sip',
                membership=dict(members={'AAPL':dict(name='Apple Inc.',sector='Information Technology')},source='fixture',as_of='2026-11-01'),
                rows=[],coverage=[],completed=[])


class ReportTests(unittest.TestCase):
    def test_schedules_and_month_year_rollovers(self):
        self.assertEqual(reports.due(instant('2026-11-01T07:59:59')),[])
        self.assertEqual(reports.due(instant('2026-11-01T08:00:00')),['weekly','monthly'])
        self.assertEqual(reports.due(instant('2026-11-02T08:00:00')),[])
        self.assertEqual(reports.due(instant('2027-01-01T08:00:00')),['monthly'])
        p=reports.period('monthly',instant('2027-01-01T08:00:00'))
        self.assertEqual(datetime.fromtimestamp(p['start']/1000,reports.NY).date().isoformat(),'2026-12-01')
        p=fixture()['period']
        self.assertEqual(datetime.fromtimestamp(p['start']/1000,reports.NY).date().isoformat(),'2026-10-26')
        self.assertEqual(datetime.fromtimestamp(reports.expected_close(p)/1000,reports.NY).isoformat(),'2026-10-30T16:00:00-04:00')

    def test_dst_next_sunday_and_early_close(self):
        before=instant('2026-10-24T08:00:00')/1000
        self.assertEqual(reports.seconds_to_scan(before),25*3600)
        p=reports.period('weekly',instant('2026-11-29T08:00:00'))
        self.assertEqual(datetime.fromtimestamp(reports.expected_close(p)/1000,reports.NY).hour,13)
        p=reports.period('weekly',instant('2026-04-05T08:00:00'))
        self.assertEqual(datetime.fromtimestamp(reports.expected_close(p)/1000,reports.NY).date().isoformat(),'2026-04-02')

    def test_evaluate_deduplicates_uses_pivot_stage_and_signal_prices(self):
        run=fixture();end=reports.expected_close(run['period']);start=run['period']['start']
        bars=[Candle(start-(249-i)*604800000,end-(249-i)*604800000,10,14,9,12) for i in range(250)]
        duplicate=dict(direction='bullish',pivot2=start,candle_at=start,confirmed_at=end)
        old=duplicate | dict(confirmed_at=run['period']['start']-1)
        with patch('scanner.detect',return_value=[duplicate,duplicate,old]) as rsi, \
             patch('pin_bars.detect',return_value=[duplicate]),patch('bb_pin_bars.detect',return_value=[]),patch('rsi_pb.detect',return_value=[duplicate]):
            rows,coverage=reports.evaluate('AAPL',bars,run)
        self.assertEqual(len(rows),3)
        self.assertTrue(rsi.call_args.kwargs['provisional'])
        self.assertEqual(rows[0]['open'],10)
        self.assertEqual(rows[0]['close'],12)
        self.assertEqual(datetime.fromtimestamp(rows[0]['start']/1000,reports.NY).hour,9)
        self.assertTrue(all(not c['reason'] for c in coverage))

    def test_missing_stale_short_and_incomplete_history(self):
        run=fixture();end=reports.expected_close(run['period'])
        rows,coverage=reports.evaluate('AAPL',[],run)
        self.assertFalse(rows)
        self.assertTrue(all(c['reason'] for c in coverage))
        stale=[Candle(end-604800000,end-86400000,10,11,9,10)]
        self.assertTrue(all('unavailable' in c['reason'] for c in reports.evaluate('AAPL',stale,run)[1]))
        short=[Candle(end-86400000,end,10,11,9,10)]
        self.assertTrue(all('Insufficient' in c['reason'] for c in reports.evaluate('AAPL',short,run)[1]))

    def test_pdf_grouping_prices_and_empty_coverage_reports(self):
        from pypdf import PdfReader
        from stock_report_pdf import render
        run=fixture()
        row=dict(ticker='AAPL',name='Apple Inc.',sector='Information Technology',strategy='rsi-pb',direction='bullish',
                 start=instant('2026-10-26T15:30:00'),end=instant('2026-10-30T22:00:00'),open=10,close=12)
        run['rows']=[row,row | dict(ticker='MSFT',name='Microsoft')]
        run['coverage']=[dict(ticker='AAPL',strategy=s,reason='',candles=250) for s in reports.STRATEGIES]
        docs=reports.artifacts(run)
        self.assertEqual(len(docs),2)
        self.assertIn('no-matches',docs[1]['filename'])
        text=PdfReader(BytesIO(docs[0]['content'])).pages[0].extract_text()
        self.assertEqual(text.count('26 Oct 2026'),1)
        self.assertIn('10.00',text);self.assertIn('Information Technology',text)
        run['coverage'].append(dict(ticker='BAD',strategy='rsi',reason='Insufficient history'))
        docs=reports.artifacts(run)
        self.assertEqual(len(docs),3)
        self.assertIn('Coverage limitations',PdfReader(BytesIO(docs[0]['content'])).pages[0].extract_text())
        # Force multipage wrapping and check that every stock survives rendering.
        many=[row | dict(ticker=f'Z{i:03d}',name='A very long company name '*3) for i in range(100)]
        reader=PdfReader(BytesIO(render(run,'rsi-pb','bullish',many)))
        self.assertGreater(len(reader.pages),1)
        self.assertIn('Z099',''.join(p.extract_text() for p in reader.pages))

    def test_metadata_parser_and_snapshot_fallback(self):
        from stock_report_data import parse_members,membership
        page='<table id="constituents">'+''.join(f'<tr><td>T{i:03d}</td><td>Company &amp; Co.</td><td>Energy</td></tr>' for i in range(500))+'</table>'
        members=parse_members(page)
        self.assertEqual(members['T000']['name'],'Company & Co.')
        with patch('urllib.request.urlopen',side_effect=OSError):
            saved=membership(dict(members=members,source='test',as_of='yesterday'))
        self.assertTrue(saved['fallback'])
        self.assertEqual(len(saved['members']),500)


class DurableReportTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        self.config={'assets':[],'timeframes':['weekly','monthly'],'stock_reports':{'enabled':True}}
        self.now=instant('2026-11-01T08:00:00')

    def process(self, **kwargs):
        with patch('stock_reports.eligible',return_value=True),patch('stock_report_data.membership',return_value=fixture()['membership']), \
             patch('stock_report_data.fetch_batch',return_value={}) as fetch,patch('local_settings.delivery_dry_run',return_value=kwargs.get('dry',False)), \
             patch('telegram_documents.deliver_document',side_effect=kwargs.get('send_error')) as send:
            reports.run_due(self.store,self.config,load_rules(),self.now)
        return fetch,send

    def test_durable_documents_and_repeat_runs(self):
        fetch,send=self.process()
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(send.call_count,2) # coverage only: unavailable data must never mean no matches
        fetch,send=self.process()
        self.assertFalse(fetch.called);self.assertFalse(send.called)
        status=reports.status(self.store)
        self.assertEqual(len(status['runs']),2)
        self.assertTrue(all(r['state']=='incomplete' for r in status['runs']))

    def test_dry_run_saves_pdfs_without_sending(self):
        _,send=self.process(dry=True)
        self.assertFalse(send.called)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM stock_report_documents').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM stock_report_runs WHERE state='dry-run'").fetchone()[0],2)
            db.execute('UPDATE stock_report_runs SET next_attempt=0')
        fetch,send=self.process()
        self.assertFalse(fetch.called)
        self.assertEqual(send.call_count,2)

    def test_delivery_retry_keeps_artifacts_and_skips_successful_documents(self):
        self.process(send_error=RuntimeError('request failed'))
        with self.store.connect() as db:
            db.execute('UPDATE stock_report_runs SET next_attempt=0')
        fetch,send=self.process()
        self.assertFalse(fetch.called)
        self.assertEqual(send.call_count,2)

    def test_lease_prevents_concurrent_claims_and_weekdays_never_send(self):
        run=fixture()
        with self.store.connect() as db:
            db.execute("INSERT INTO stock_report_runs(id,day,frame,state,payload,locked_until) VALUES(%s,%s,%s,'scanning',%s,extract(epoch FROM clock_timestamp())+10000)",
                       ('weekly:2026-11-01',run['day'],'weekly',json.dumps(run)))
        fetch,send=self.process()
        self.assertEqual(fetch.call_count,1) # only monthly can claim
        self.assertEqual(send.call_count,1)
        with patch('telegram_documents.deliver_document') as send:
            reports.run_due(self.store,self.config,load_rules(),instant('2026-11-02T09:00:00'))
        self.assertFalse(send.called)
        self.assertIn('missed',[r['state'] for r in reports.status(self.store)['runs']])

    def test_provider_retry_resumes_without_refreshing_membership_or_rules(self):
        with patch('stock_reports.datetime',wraps=datetime) as clock,patch('stock_reports.eligible',return_value=True),patch('stock_report_data.membership',return_value=fixture()['membership']), \
             patch('stock_report_data.fetch_batch',side_effect=ValueError('unavailable')),patch('telegram_documents.deliver_document') as send:
            clock.now.return_value=datetime.fromtimestamp(self.now/1000,reports.ZONE)
            reports.run_due(self.store,self.config,load_rules(),self.now)
        self.assertFalse(send.called)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM stock_report_documents').fetchone()[0],0)
            db.execute('UPDATE stock_report_runs SET next_attempt=0')
        with patch('stock_report_data.membership') as membership:
            self.process()
        self.assertFalse(membership.called)

    def test_existing_permanent_pending_retained_and_rotating_pending_retired(self):
        import rsi_pb
        version=rsi_pb.load_rules()['version']
        asset=dict(id='STOCK-AAPL',provider='alpaca',symbol='AAPL',exchange='Alpaca-sip-split',market='stock',enabled=True,
                   feed_confirmed=True,timeframes=['weekly'],scan_strategies=['rsi-pb'],weekly_screen_selected=True)
        setup=dict(asset_config=asset,timeframe='weekly',version=version,source=rsi_pb.source_for(asset))
        with self.store.connect() as db:
            db.execute('INSERT INTO rsi_pb_pending VALUES(%s,%s,%s,%s)',('x','feed','weekly',json.dumps(setup)))
        configured=self.config | dict(assets=[asset])
        result=rsi_pb.retain_pending(self.store,dict(configured),configured,version)
        self.assertEqual(result['assets'],[asset])
        result=rsi_pb.retain_pending(self.store,dict(self.config),self.config,version)
        self.assertEqual(result['assets'],[])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rsi_pb_pending').fetchone()[0],0)

    def test_migration_rerun_and_browser_role_isolation(self):
        from pathlib import Path
        import psycopg
        from test_postgres_support import test_dsn
        with psycopg.connect(test_dsn(),autocommit=True) as db:
            db.execute((Path(__file__).parent/'cloud/stock-reports.sql').read_text(),prepare=False)
            allowed=db.execute("SELECT has_table_privilege('authenticated','bot_runtime.stock_report_documents','SELECT'),has_table_privilege('trading_worker','bot_runtime.stock_report_documents','INSERT')").fetchone()
            self.assertEqual(allowed,(False,True))

    def test_all_strategy_directions_have_separate_pdfs_without_live_alerts(self):
        run=fixture();end=reports.expected_close(run['period'])
        rows=[dict(ticker='AAPL',name='Apple',sector='Information Technology',strategy=s,direction=d,
                   start=run['period']['start'],end=end,open=10,close=12)
              for s in reports.STRATEGIES for d in reports.DIRECTIONS]
        coverage=[dict(ticker='AAPL',strategy=s,reason='',candles=250) for s in reports.STRATEGIES]
        with patch('stock_reports.evaluate',return_value=(rows,coverage)):
            _,send=self.process()
        self.assertEqual(send.call_count,16)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM stock_report_runs WHERE state='sent'").fetchone()[0],2)
            for table in ('signals','pin_bar_signals','bb_pin_bar_signals','rsi_pb_signals','notification_queue','scan_state','pin_bar_state','bb_pin_bar_state','rsi_pb_state'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)



class DocumentDeliveryTests(unittest.TestCase):
    def test_multipart_upload_and_message_id(self):
        from telegram_documents import send_document
        from unittest.mock import MagicMock
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"ok":true,"result":{"message_id":42}}'
        with patch('urllib.request.urlopen',return_value=response) as request:
            self.assertEqual(send_document('secret','-100','Report','report.pdf',b'%PDF-demo',silent=True,dismissal_user_id=123),42)
        body=request.call_args.args[0].data
        self.assertIn(b'name="document"; filename="report.pdf"',body)
        self.assertIn(b'%PDF-demo',body)
        self.assertIn(b'true',body)

    def test_definitive_destination_error_and_sanitized_timeout(self):
        import urllib.error
        from telegram_documents import send_document
        from platform_app import TelegramDestinationError
        error=urllib.error.HTTPError('https://secret',403,'forbidden',{},BytesIO(b'{"description":"bot was kicked"}'))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(TelegramDestinationError):
                send_document('secret','chat','Report','report.pdf',b'PDF')
        with patch('urllib.request.urlopen',side_effect=TimeoutError('https://secret')):
            with self.assertRaisesRegex(RuntimeError,'^Telegram document request failed$'):
                send_document('secret','chat','Report','report.pdf',b'PDF')


class DocumentRoutingTests(PostgresTestCase):
    def test_stock_route_general_fallback_and_repeat_suppression(self):
        import os
        from psycopg.types.json import Jsonb
        from telegram_routing import validate
        from telegram_documents import deliver_document
        from platform_app import TelegramDestinationError
        store=TestStore()
        with store.connect() as db:
            db.execute("INSERT INTO public.telegram_routes VALUES('00000000-0000-0000-0000-000000000002',1,%s)",
                       (Jsonb(validate({'general':'-100','stocks.pin-bars':'-200'})),))
        with patch.dict(os.environ,TELEGRAM_BOT_TOKEN='123:fake'),patch('telegram_documents.send_document',side_effect=[TelegramDestinationError('gone'),42]) as send:
            result=deliver_document(store,'report-1','report.pdf','bb-pin-bars','Report',b'PDF')
            self.assertEqual(result,'sent')
            self.assertEqual([c.args[1] for c in send.call_args_list],['-200','-100'])
        with patch.dict(os.environ,TELEGRAM_BOT_TOKEN='123:fake'),patch('telegram_documents.send_document') as send:
            result=deliver_document(store,'report-1','report.pdf','bb-pin-bars','Report',b'PDF')
            self.assertEqual(result,'suppressed')
            send.assert_not_called()
