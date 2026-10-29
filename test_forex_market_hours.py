from test_postgres_support import TestStore as Store, PostgresTestCase
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_calendar import forex_market_open, next_forex_open
from platform_app import load_rules
from scanner import Candle, prepare_store, scan_once


def ms(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


class ForexMarketHoursTests(PostgresTestCase):
    def test_week_boundaries_and_dst(self):
        for friday, sunday in [('2026-10-16', '2026-10-18'),
                               ('2026-11-06', '2026-11-08')]:
            offset = '-04:00' if '10-' in friday else '-05:00'
            close = ms(friday + 'T17:00:00' + offset)
            opening = ms(sunday + 'T17:00:00' + offset)
            self.assertTrue(forex_market_open(close - 1))
            self.assertFalse(forex_market_open(close))
            self.assertFalse(forex_market_open(opening - 1))
            self.assertTrue(forex_market_open(opening))
            self.assertEqual(next_forex_open(close), opening)
            self.assertEqual(next_forex_open(opening - 1), opening)
            self.assertEqual(next_forex_open(opening), opening)
        # New York changes offset during this particular closed weekend.
        self.assertEqual(next_forex_open(ms('2026-10-30T17:00:00-04:00')),
                         ms('2026-11-01T17:00:00-05:00'))
        self.assertEqual(next_forex_open(ms('2026-03-06T17:00:00-05:00')),
                         ms('2026-03-08T17:00:00-04:00'))

    def test_closed_market_blocks_every_timeframe_before_schedule_or_strategies(self):
        now = ms('2026-10-18T10:00:00+03:00')
        frames = ['1h', '4h', 'daily', 'weekly', 'monthly']
        for provider, market in [('twelvedata', 'forex'), ('tiingo', None), ('oanda', None)]:
            for catchup in (False, True):
                with self.subTest(provider=provider, catchup=catchup), tempfile.TemporaryDirectory() as folder:
                    store = Store(Path(folder) / 'test.sqlite3')
                    prepare_store(store)
                    config = dict(timeframes=frames, assets=[dict(
                        id='EURUSD', symbol='EUR/USD', provider=provider, market=market)])
                    with patch('scanner.fetch_twelve_data') as twelve, \
                         patch('tiingo_feed.fetch_tiingo') as tiingo, \
                         patch('oanda_feed.fetch_oanda') as oanda, \
                         patch('scan_schedule.saved') as saved, \
                         patch('pin_bars.scan') as pin, \
                         patch('bb_pin_bars.scan') as bb, \
                         patch('scanner.detect') as rsi:
                        rows = scan_once(store, config, load_rules(), now, catchup=catchup)
                    for mock in (twelve, tiingo, oanda, saved, pin, bb, rsi):
                        mock.assert_not_called()
                    self.assertEqual(len(rows), len(frames))
                    self.assertTrue(all(row['status'] == 'market closed' for row in rows))
                    self.assertTrue(all(row['next_close_at'] == ms('2026-10-19T00:00:00+03:00') for row in rows))
                    self.assertEqual(store.rows(), [])

    def test_reopening_and_crypto_continue_to_fetch(self):
        for market, now in [('forex', '2026-10-18T17:00:00-04:00'),
                            ('crypto', '2026-10-18T10:00:00+03:00')]:
            with self.subTest(market=market), tempfile.TemporaryDirectory() as folder:
                from test_postgres_support import reset
                reset()
                store = Store(Path(folder) / 'test.sqlite3')
                prepare_store(store)
                config = dict(timeframes=['daily'], assets=[dict(
                    id='PAIR', symbol='PAIR', provider='twelvedata', market=market)])
                with patch('scanner.fetch_twelve_data', return_value=[]) as fetch:
                    rows = scan_once(store, config, load_rules(), ms(now))
                fetch.assert_called_once()
                self.assertNotEqual(rows[0]['status'], 'market closed')

    def test_reopening_excludes_weekend_bars_before_caching_and_detection(self):
        # Reproduce the reported Sunday 11:00 Israel-time confirmation.
        def bar(start, end):
            return Candle(ms(start), ms(end), 1.1, 1.2, 1.0, 1.11)
        friday = bar('2026-10-16T20:00:00+00:00', '2026-10-17T00:00:00+00:00')
        saturday = bar('2026-10-17T04:00:00+00:00', '2026-10-17T08:00:00+00:00')
        sunday = bar('2026-10-18T04:00:00+00:00', '2026-10-18T08:00:00+00:00')
        reopened = bar('2026-10-18T21:00:00+00:00', '2026-10-19T01:00:00+00:00')
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'test.sqlite3')
            prepare_store(store)
            config = dict(timeframes=['4h'], assets=[dict(id='EURUSD', symbol='EUR/USD',
                          provider='twelvedata', market='forex')])
            with patch('scanner.fetch_twelve_data', return_value=[friday, saturday, sunday, reopened]), \
                 patch('scanner.usable_provider_history', side_effect=lambda raw, *_: raw), \
                 patch('managed_assets.cache_candles') as cache, \
                 patch('pin_bars.scan', return_value=dict(status='scanned')) as pin, \
                 patch('bb_pin_bars.scan'):
                rows = scan_once(store, config, load_rules(), reopened.end)
            self.assertEqual(cache.call_args.args[3], [friday, reopened])
            self.assertEqual(pin.call_args.args[3], [friday, reopened])
            self.assertIn('2 closed-session forex candle(s) excluded', rows[0]['data_notes'])
