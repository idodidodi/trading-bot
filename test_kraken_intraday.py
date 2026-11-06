"""Native short Kraken intervals, with no live network or database access."""
from datetime import datetime, timezone
import io
import json
import unittest
from unittest.mock import patch

from kraken_feed import fetch_kraken
from managed_assets import validate_asset
from scan_schedule import next_due
from scanner import check_candles, interval_end


class IntradayTests(unittest.TestCase):
    def setUp(self):
        pacing = patch('kraken_feed.pace_public_request', create=True)
        pacing.start(); self.addCleanup(pacing.stop)
        self.asset = dict(id='BTCUSD', provider='kraken', exchange='Kraken', symbol='BTC/USD',
                          enabled=True, feed_confirmed=True, timeframes=['5m','15m','30m'])

    def test_native_durations_and_uncommitted_bar_even_after_its_nominal_close(self):
        for frame, minutes in [('5m',5),('15m',15),('30m',30)]:
            with self.subTest(frame=frame):
                start = 1800000000
                payload = dict(error=[], result={'BTC/USD':[
                    [start,'10','12','9','11'], [start+minutes*60,'11','13','10','12']], 'last':start})
                with patch('kraken_feed.urllib.request.urlopen', return_value=io.StringIO(json.dumps(payload))) as request:
                    bars = fetch_kraken(self.asset, frame)
                self.assertIn(f'interval={minutes}&', request.call_args.args[0])
                self.assertEqual(len(bars), 1)
                self.assertEqual(bars[0].end-bars[0].start, minutes*60000)
                self.assertEqual(check_candles(bars, start*1000+3*minutes*60000), bars)
                self.assertEqual(bars.next_close_at, start*1000+2*minutes*60000)
                dt = datetime.fromtimestamp(start,timezone.utc)
                self.assertEqual((interval_end(dt,frame)-dt).total_seconds(), minutes*60)

    def test_new_intervals_require_an_explicit_supported_provider(self):
        validate_asset(self.asset)
        for provider in ('twelvedata','oanda','tiingo','alpaca','fmp','yahoo'):
            with self.subTest(provider=provider), self.assertRaisesRegex(ValueError,'require Kraken or CSV'):
                validate_asset(self.asset | dict(provider=provider))
        validate_asset(self.asset | dict(provider='csv'))
        # Mixed feeds can explicitly select Kraken without changing the primary venue.
        validate_asset(self.asset | dict(provider='twelvedata', exchange='Binance',
                                        timeframe_providers={f:'kraken' for f in self.asset['timeframes']}))
        with self.assertRaisesRegex(ValueError,'require Kraken or CSV'):
            validate_asset(self.asset | dict(timeframe_providers={'5m':'oanda'}))

    def test_native_deadline_and_short_error_retry(self):
        now = 1800000000000
        for frame, minutes in [('5m',5),('15m',15),('30m',30)]:
            with self.subTest(frame=frame):
                raw = type('Bars',(list,),{})([])
                raw.next_close_at = now+minutes*60000
                self.assertEqual(next_due(self.asset,frame,raw,[],now,dict(status='scanned')), raw.next_close_at)
                self.assertEqual(next_due(self.asset,frame,[],[],now,dict(status='unavailable')), now+60000)
        # Higher-timeframe provider failures keep the existing backoff.
        self.assertEqual(next_due(self.asset,'1h',[],[],now,dict(status='unavailable')), now+3600000)

    def test_schedule_can_recover_native_alignment_without_provider_hint(self):
        from scanner import Candle
        now = 1800000000000
        for frame, minutes in [('5m',5),('15m',15),('30m',30)]:
            period = minutes*60000
            last = Candle(now-period,now,10,12,9,11)
            self.assertEqual(next_due(self.asset,frame,[last],[last],now+1000,dict(status='scanned')), now+period)

    def test_unknown_kraken_interval_has_accurate_error(self):
        with self.assertRaisesRegex(ValueError,'Unsupported Kraken'):
            fetch_kraken(self.asset,'2m')
        with self.assertRaisesRegex(ValueError,'calendar-month'):
            fetch_kraken(self.asset,'monthly')
