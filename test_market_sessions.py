from datetime import datetime
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from market_sessions import state, closed_row, next_request_time, classify_unavailable, MarketClosed


def ms(value):
    return int(datetime.fromisoformat(value).timestamp()*1000)


def asset(symbol):
    return dict(id=symbol, provider='colmex', exchange='Colmex-MT4', symbol=symbol,
                market='commodity' if symbol == 'USOIL#' else 'index', feed_confirmed=True)


class SessionCalendarTests(unittest.TestCase):
    def test_observed_weekend_and_distinct_reopenings(self):
        for symbol, opening in [('GER40#', '2026-11-16T01:10:00+00:00'),
                                ('SPX500#', '2026-11-16T00:00:00+00:00'),
                                ('USDINDEX#', '2026-11-16T01:00:00+00:00'),
                                ('USOIL#', '2026-11-16T01:00:00+00:00')]:
            with self.subTest(symbol=symbol):
                row = closed_row(asset(symbol), ms('2026-11-14T06:16:58+00:00'))
                self.assertEqual(row['next_open_at'], ms(opening))
                self.assertEqual(row['status'], 'market closed')
                self.assertEqual(row['new_signals'], 0)
                self.assertEqual(state(asset(symbol), ms(opening))['session_state'], 'open')
                self.assertEqual(state(asset(symbol), ms(opening)-1)['session_state'], 'closed')

    def test_daily_break_boundaries_and_genuine_outages(self):
        for symbol, close, opening in [
            ('GER40#', '2026-11-12T21:00:00+00:00', '2026-11-13T01:10:00+00:00'),
            ('SPX500#', '2026-11-12T21:15:00+00:00', '2026-11-12T21:30:00+00:00'),
            ('SPX500#', '2026-11-12T22:00:00+00:00', '2026-11-12T23:05:00+00:00'),
            ('USDINDEX#', '2026-11-12T22:00:00+00:00', '2026-11-13T01:00:00+00:00'),
            ('USOIL#', '2026-11-12T23:00:00+00:00', '2026-11-13T01:00:00+00:00')]:
            with self.subTest(symbol=symbol, close=close):
                self.assertEqual(state(asset(symbol), ms(close)-1)['session_state'], 'open')
                self.assertEqual(next_request_time(asset(symbol), ms(close)), ms(opening))
        for symbol, stamp in [('GER40#', '2026-11-06T03:00:00+00:00'),
                              ('USDINDEX#', '2026-11-09T04:25:00+00:00'),
                              ('USOIL#', '2026-11-10T22:20:00+00:00')]:
            self.assertEqual(classify_unavailable(asset(symbol), ms(stamp))['availability_classification'],
                             'open-session data failure')

    def test_seasonal_zones_and_different_dst_weeks(self):
        for symbol, closed, opening in [
            ('GER40#', '2026-10-23T21:00:00+00:00', '2026-10-26T01:10:00+00:00'),
            ('SPX500#', '2026-10-23T21:00:00+00:00', '2026-10-25T23:00:00+00:00'),
            ('SPX500#', '2026-10-30T21:00:00+00:00', '2026-11-02T00:00:00+00:00'),
            ('USDINDEX#', '2026-03-06T22:00:00+00:00', '2026-03-09T00:00:00+00:00')]:
            self.assertEqual(next_request_time(asset(symbol), ms(closed)), ms(opening))

    def test_unverified_oil_season_does_not_suppress_requests(self):
        self.assertEqual(state(asset('USOIL#'), ms('2027-03-28T12:00:00+00:00'))['session_state'], 'unknown')

    def test_broker_thanksgiving_notice_and_extra_overnight_window(self):
        for symbol, close in [('SPX500#', '18:00'), ('USDINDEX#', '18:15'), ('USOIL#', '18:30')]:
            stamp = ms('2026-11-26T'+close+':00+00:00')
            self.assertEqual(state(asset(symbol), stamp-1)['session_state'], 'open')
            self.assertEqual(state(asset(symbol), stamp)['session_state'], 'closed')
        self.assertEqual(next_request_time(asset('SPX500#'), ms('2026-11-26T18:00:00+00:00')),
                         ms('2026-11-26T23:00:00+00:00'))
        for stamp in ('2026-11-26T23:30:00+00:00', '2026-11-27T00:30:00+00:00'):
            self.assertEqual(state(asset('SPX500#'), ms(stamp))['session_state'], 'open')
        self.assertEqual(state(asset('SPX500#'), ms('2026-11-27T18:15:00+00:00'))['session_state'], 'closed')
        self.assertEqual(state(asset('GER40#'), ms('2026-11-26T19:00:00+00:00'))['session_state'], 'open')

    def test_unknown_identity_never_borrows_calendar_and_crypto_stays_open(self):
        now = ms('2026-11-14T12:00:00+00:00')
        for a in [asset('SPX500'), asset('OTHER#'), asset('SPX500#') | dict(exchange='Other'),
                  dict(provider='tiingo', market='commodity', symbol='XAU/USD'),
                  dict(provider='yahoo', symbol='^GDAXI', exchange='XETRA')]:
            self.assertEqual(state(a, now)['session_state'], 'unknown')
            self.assertIsNone(closed_row(a, now))
        self.assertEqual(state(dict(provider='kraken', market='crypto'), now)['session_state'], 'open')
        self.assertEqual(state(dict(provider='kraken', market='forex'), now)['session_state'], 'closed')
        self.assertEqual(state(dict(provider='csv', market='forex'), now)['session_state'], 'not applicable')

    def test_equity_holidays_early_close_and_final_bar_allowance(self):
        a = dict(provider='alpaca', market='stock', symbol='AAPL')
        self.assertEqual(next_request_time(a, ms('2026-11-26T16:00:00+00:00')),
                         ms('2026-11-27T14:30:00+00:00'))
        self.assertEqual(state(a, ms('2026-11-27T18:16:00+00:00'))['session_state'], 'closing data')
        self.assertEqual(next_request_time(a, ms('2026-11-27T18:20:00+00:00')),
                         ms('2026-11-30T14:30:00+00:00'))

    def test_direct_colmex_read_does_not_touch_terminal_when_closed(self):
        from colmex_feed import fetch_colmex
        with patch('colmex_feed.time.time', return_value=ms('2026-11-14T12:00:00+00:00')/1000), \
             patch('colmex_feed.bridge') as reader:
            with self.assertRaises(MarketClosed):
                fetch_colmex(asset('SPX500#'), '4h')
            reader.assert_not_called()

    def test_retry_moves_out_of_closure_but_open_failure_retries(self):
        from scan_schedule import next_due
        a = asset('SPX500#')
        now = ms('2026-11-13T21:59:45+00:00')
        self.assertEqual(next_due(a, '4h', [], [], now, dict(status='unavailable')),
                         ms('2026-11-16T00:00:00+00:00'))
        now = ms('2026-11-13T12:00:00+00:00')
        self.assertEqual(next_due(a, '4h', [], [], now, dict(status='unavailable')), now+30000)

    def test_terminal_failure_is_not_excused_by_weekend(self):
        result = classify_unavailable(asset('SPX500#'), ms('2026-11-14T06:00:00+00:00'),
                                      'Colmex terminal disconnected, stale or misconfigured')
        self.assertEqual(result['availability_classification'], 'data failure during closure')

    def test_log_audit_distinguishes_closure_outage_and_unknown(self):
        spec = importlib.util.spec_from_file_location('session_audit', Path(__file__).parent/'scripts/audit-market-sessions.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        rows = [asset(symbol) | dict(status='unavailable', checked_at=ms(stamp), reason='Colmex quote is stale or timezone mapping conflicts with live time')
                for symbol, stamp in [('SPX500#', '2026-11-14T06:00:00+00:00'),
                                      ('GER40#', '2026-11-06T03:00:00+00:00'),
                                      ('UNKNOWN', '2026-11-14T06:00:00+00:00')]]
        self.assertEqual({r['classification'] for r in module.audit(rows)},
                         {'closure-compatible stale quote', 'open-session data failure', 'schedule unverified'})


from test_postgres_support import PostgresTestCase, TestStore


class ScannerSessionTests(PostgresTestCase):
    def test_closure_precedes_cached_results_fetch_and_all_strategies(self):
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        store = TestStore(); prepare_store(store)
        config = dict(assets=[asset(s) | dict(timeframes=['1h', '4h', 'daily'])
                              for s in ('SPX500#', 'GER40#', 'USDINDEX#', 'USOIL#')], timeframes=['4h'])
        with patch.dict('os.environ', {'COLMEX_FILES_DIR': ''}), \
             patch('colmex_feed.fetch_colmex') as fetch, patch('scan_schedule.saved') as saved, \
             patch('scanner.detect') as detect, patch('pin_bars.scan') as pin, \
             patch('ma_cross.scan') as cross:
            for catchup in (False, True):
                rows = scan_once(store, config, load_rules(), ms('2026-11-14T06:16:58+00:00'), catchup=catchup)
                self.assertEqual(len(rows), 12)
                self.assertTrue(all(r['status'] == 'market closed' for r in rows))
            for mock in (fetch, saved, detect, pin, cross):
                mock.assert_not_called()
        self.assertEqual(store.rows(), [])

    def test_open_session_error_stays_visible_and_retries(self):
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        store = TestStore(); prepare_store(store)
        now = ms('2026-11-13T12:00:00+00:00')
        with patch.dict('os.environ', {'COLMEX_FILES_DIR': ''}), \
             patch('colmex_feed.fetch_colmex', side_effect=ValueError('Colmex terminal disconnected')) as fetch:
            rows = scan_once(store, dict(assets=[asset('GER40#') | dict(scan_strategies=['rsi'])], timeframes=['4h']), load_rules(), now)
        fetch.assert_called_once()
        self.assertEqual(rows[0]['status'], 'unavailable')
        self.assertEqual(rows[0]['reason'], 'Colmex terminal disconnected')
        self.assertEqual(rows[0]['availability_classification'], 'open-session data failure')
        self.assertEqual(rows[0]['next_close_at'], now+30000)

    def test_timeframe_override_is_classified_after_provider_resolution(self):
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        store = TestStore(); prepare_store(store)
        a = asset('SPX500#') | dict(provider='csv', timeframe_providers={'4h': 'colmex'}, scan_strategies=['rsi'])
        with patch.dict('os.environ', {'COLMEX_FILES_DIR': ''}), patch('colmex_feed.fetch_colmex') as fetch:
            rows = scan_once(store, dict(assets=[a], timeframes=['4h']), load_rules(), ms('2026-11-14T06:00:00+00:00'))
        fetch.assert_not_called()
        self.assertEqual(rows[0]['status'], 'market closed')

    def test_equity_after_hours_blocks_reads_but_closing_data_is_allowed(self):
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        store = TestStore(); prepare_store(store)
        a = dict(id='AAPL', symbol='AAPL', provider='alpaca', market='stock', exchange='NASDAQ', scan_strategies=['rsi'])
        config = dict(assets=[a], timeframes=['daily'])
        with patch('alpaca_feed.fetch_alpaca', return_value=[]) as fetch:
            rows = scan_once(store, config, load_rules(), ms('2026-11-13T22:00:00+00:00'))
            fetch.assert_not_called()
            self.assertEqual(rows[0]['status'], 'market closed')
            rows = scan_once(store, config, load_rules(), ms('2026-11-13T21:16:00+00:00'))
            fetch.assert_called_once()
            self.assertEqual(rows[0]['session_state'], 'closing data')

    def test_reopening_resumes_and_preserves_freshness_validation(self):
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        store = TestStore(); prepare_store(store)
        config = dict(assets=[asset('SPX500#') | dict(scan_strategies=['rsi'])], timeframes=['4h'])
        with patch.dict('os.environ', {'COLMEX_FILES_DIR': ''}), \
             patch('colmex_feed.fetch_colmex', side_effect=ValueError('Colmex quote is stale')) as fetch:
            rows = scan_once(store, config, load_rules(), ms('2026-11-15T23:59:59+00:00'))
            fetch.assert_not_called()
            rows = scan_once(store, config, load_rules(), ms('2026-11-16T00:00:00+00:00'))
            fetch.assert_called_once()
            self.assertEqual(rows[0]['status'], 'unavailable')
            self.assertEqual(rows[0]['availability_classification'], 'open-session data failure')
            self.assertEqual(rows[0]['next_close_at'], ms('2026-11-16T00:00:30+00:00'))

    def test_long_live_cycle_rechecks_clock_before_each_asset(self):
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        store = TestStore(); prepare_store(store)
        config = dict(assets=[asset('GER40#') | dict(scan_strategies=['rsi'])], timeframes=['4h'])
        with patch.dict('os.environ', {'COLMEX_FILES_DIR': ''}), \
             patch('scanner.time') as clock, patch('colmex_feed.fetch_colmex') as fetch:
            clock.time.side_effect = [ms('2026-11-13T20:59:00+00:00')/1000,
                                      ms('2026-11-13T21:01:00+00:00')/1000]
            rows = scan_once(store, config, load_rules())
            fetch.assert_not_called()
            self.assertEqual(rows[0]['status'], 'market closed')
