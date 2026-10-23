"""Delayed Yahoo DAX cash-index OHLC. Never turn quote snapshots into candles."""
from datetime import datetime, timedelta, timezone
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

FRAMES = ('1h', '4h', 'daily', 'weekly', 'monthly')
DELAY_MS = 16 * 60 * 1000  # 15-minute feed delay plus publication margin.
BERLIN = ZoneInfo('Europe/Berlin')
UTC = timezone.utc
_cache = {}


def validate_feed(asset, timeframe):
    if (timeframe not in FRAMES or asset.get('symbol') != '^GDAXI'
            or asset.get('exchange') != 'XETRA' or asset.get('market') != 'index'
            or asset.get('feed_confirmed') is not True):
        raise ValueError('Yahoo requires the confirmed ^GDAXI DAX cash index, XETRA exchange and index market')


def request_chart(interval):
    now = time.time()
    cached = _cache.get(interval)
    if cached and now - cached[0] < 300:
        return cached[1]
    params = dict(interval=interval, range='10y' if interval == '1d' else '1y', includePrePost='false')
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGDAXI?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            chart = json.load(response)['chart']
        if chart.get('error') or len(chart['result']) != 1:
            raise ValueError('Invalid chart')
        result = chart['result'][0]
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError('Yahoo quota reached; retry later' if code == 429 else
                         f'Yahoo candle request failed (HTTP {code}); retry later') from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('Yahoo candle response unavailable or malformed; retry later') from None
    _cache[interval] = (now, result)
    return result


def candles_from_chart(result, timeframe, now):
    from scanner import Candle, ProviderCandles, check_candles, interval_end
    interval = timeframe if timeframe in ('1h', '4h') else '1d'
    try:
        meta = result['meta']
        if (meta['symbol'] != '^GDAXI' or meta['currency'] != 'EUR'
                or meta['instrumentType'] != 'INDEX' or meta['exchangeName'] != 'GER'
                or meta['exchangeTimezoneName'] != 'Europe/Berlin'
                or meta['dataGranularity'] != interval):
            raise ValueError('Yahoo returned a different instrument, venue, currency or interval')
        stamps = result['timestamp']
        prices = result['indicators']['quote'][0]
        if not stamps or any(len(prices[k]) != len(stamps) for k in ('open', 'high', 'low', 'close')):
            raise ValueError('Yahoo returned incomplete OHLC arrays')
        if any(type(t) not in (int, float) or not math.isfinite(t) or t <= 0 for t in stamps):
            raise ValueError('Yahoo returned invalid timestamps')
        if stamps != sorted(set(stamps)):
            raise ValueError('Yahoo returned duplicate or unordered timestamps')
        sessions = {}
        if interval != '1d':
            for group in meta['tradingPeriods']:
                for session in group:
                    start, end = session['start'], session['end']
                    if end <= start:
                        raise ValueError('Invalid Yahoo session boundaries')
                    sessions[datetime.fromtimestamp(start, BERLIN).date()] = (start, end)
        bars, rejected_dates = [], set()
        excluded = 0
        for i, stamp in enumerate(stamps):
            local = datetime.fromtimestamp(stamp, BERLIN)
            values = [prices[k][i] for k in ('open', 'high', 'low', 'close')]
            if interval != '1d':
                session = sessions.get(local.date())
                if session is None:
                    raise ValueError('Yahoo session metadata missing for an intraday candle')
                opening, closing = session
                seconds = (1 if timeframe == '1h' else 4) * 3600
                # Yahoo appends a current quote with an arbitrary timestamp.
                if stamp < opening or stamp >= closing or (stamp - opening) % seconds:
                    excluded += 1
                    continue
                start, end = int(stamp * 1000), int(min(stamp + seconds, closing) * 1000)
            else:
                day = datetime.combine(local.date(), datetime.min.time(), UTC)
                start, end = int(day.timestamp()*1000), int((day+timedelta(days=1)).timestamp()*1000)
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                rejected_dates.add(local.date())
                continue
            bars.append(Candle(start, end, *values))
    except (KeyError, TypeError, IndexError, OverflowError, OSError):
        raise ValueError('Yahoo returned malformed candle or session data') from None
    # Delay eligibility as well as polling: wall-clock closure alone is insufficient.
    cutoff = now - DELAY_MS
    closed = check_candles(bars, cutoff)
    if timeframe in ('weekly', 'monthly'):
        groups = {}
        def period(day):
            return day.replace(day=1) if timeframe == 'monthly' else day-timedelta(days=day.weekday())
        bad_periods = {period(day) for day in rejected_dates}
        for bar in closed:
            day = datetime.fromtimestamp(bar.start/1000, UTC).date()
            groups.setdefault(period(day), []).append(bar)
        aggregated = []
        for day, members in sorted(groups.items()):
            start = datetime.combine(day, datetime.min.time(), UTC)
            end = int(interval_end(start, timeframe).timestamp()*1000)
            # Omit truncated leading periods and periods with missing source OHLC.
            if day in bad_periods or int(start.timestamp()*1000) < bars[0].start or end > cutoff:
                continue
            aggregated.append(Candle(int(start.timestamp()*1000), end, members[0].open,
                                     max(c.high for c in members), min(c.low for c in members), members[-1].close))
        closed = aggregated
    notes = ['Yahoo Finance ^GDAXI DAX cash index in EUR; delayed data; 16-minute publication margin',
             'Native session-aligned intraday OHLC; session ends from Yahoo metadata' if interval != '1d'
             else 'Daily session dates use conservative UTC day closes; weekly/monthly aggregated from daily OHLC']
    if rejected_dates:
        notes.append(f'{len(rejected_dates)} source date(s) with missing OHLC excluded; affected aggregate periods omitted')
    if excluded:
        notes.append(f'{excluded} non-candle quote snapshot(s) excluded')
    result = ProviderCandles(closed, notes)
    # Recheck shortly after the next actual boundary, including delayed publication.
    future = [c.end + DELAY_MS for c in bars if c.end + DELAY_MS > now]
    if timeframe in ('weekly', 'monthly') and bars:
        day = datetime.fromtimestamp(bars[-1].start/1000, UTC).replace(hour=0)
        day = day.replace(day=1) if timeframe == 'monthly' else day-timedelta(days=day.weekday())
        future = [int(interval_end(day, timeframe).timestamp()*1000)+DELAY_MS]
    result.next_close_at = min(future) if future else now + 3600000
    return result


def fetch_yahoo(asset, timeframe):
    validate_feed(asset, timeframe)
    return candles_from_chart(request_chart(timeframe if timeframe in ('1h', '4h') else '1d'),
                              timeframe, int(time.time()*1000))
