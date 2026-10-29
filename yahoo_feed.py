"""Delayed Yahoo index and selected-contract futures OHLC, never quote snapshots."""
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
INSTRUMENTS = {
    '^GSPC': dict(exchange='SNP', market='index', currency='USD', kind='INDEX',
                  venue='SNP', zone='America/New_York', label='S&P 500 cash index'),
    'DX-Y.NYB': dict(exchange='NYB', market='index', currency='USD', kind='INDEX',
                     venue='NYB', zone='America/New_York', label='US Dollar Index'),
    '^GDAXI': dict(exchange='XETRA', market='index', currency='EUR', kind='INDEX',
                   venue='GER', zone='Europe/Berlin', label='DAX cash index'),
    'ES=F': dict(exchange='CME', market='futures', currency='USD', kind='FUTURE',
                 venue='CME', zone='America/New_York', label='E-mini S&P 500 futures'),
    'CL=F': dict(exchange='NYMEX', market='futures', currency='USD', kind='FUTURE',
                 venue='NYM', zone='America/New_York', label='WTI crude oil futures'),
}


def validate_feed(asset, timeframe):
    instrument = INSTRUMENTS.get(asset.get('symbol'))
    if (instrument is None or timeframe not in FRAMES
            or asset.get('exchange') != instrument['exchange'] or asset.get('market') != instrument['market']
            or asset.get('feed_confirmed') is not True):
        raise ValueError('Confirm a supported exact Yahoo index or futures symbol, exchange and market')


def request_chart(interval, symbol='^GDAXI'):
    if symbol not in INSTRUMENTS:
        raise ValueError('Unsupported Yahoo instrument')
    now = time.time()
    key = (symbol, interval)
    cached = _cache.get(key)
    if cached and now - cached[0] < 300:
        return cached[1]
    params = dict(interval=interval, range='10y' if interval == '1d' else '1y', includePrePost='false')
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='')
           + '?' + urllib.parse.urlencode(params))
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
    _cache[key] = (now, result)
    return result


def candles_from_chart(result, timeframe, now, symbol='^GDAXI'):
    from scanner import Candle, ProviderCandles, check_candles, interval_end
    interval = timeframe if timeframe in ('1h', '4h') else '1d'
    try:
        meta = result['meta']
        instrument = INSTRUMENTS[symbol]
        zone = ZoneInfo(instrument['zone'])
        if (meta['symbol'] != symbol or meta['currency'] != instrument['currency']
                or meta['instrumentType'] != instrument['kind'] or meta['exchangeName'] != instrument['venue']
                or meta['exchangeTimezoneName'] != instrument['zone']
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
                    sessions[datetime.fromtimestamp(start, zone).date()] = (start, end)
        bars, rejected_dates = [], set()
        excluded = 0
        empty_weekend_dates = 0
        for i, stamp in enumerate(stamps):
            local = datetime.fromtimestamp(stamp, zone)
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
            # DX-Y.NYB includes an entirely empty Sunday daily slot each week.
            # It is not a candle and must not invalidate every weekly/monthly
            # aggregate. Partial rows and empty weekdays remain missing data.
            if (symbol == 'DX-Y.NYB' and interval == '1d' and local.weekday() >= 5
                    and all(v is None for v in values)):
                empty_weekend_dates += 1
                continue
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
    notes = [f"Yahoo Finance {symbol} {instrument['label']} in {instrument['currency']}; delayed data; 16-minute publication margin",
             'Native intraday OHLC; bucket boundaries from Yahoo metadata (not an independent exchange calendar)' if interval != '1d'
             else 'Daily session dates use conservative UTC day closes; weekly/monthly aggregated from daily OHLC']
    if instrument['kind'] == 'FUTURE':
        notes.append('Yahoo-selected futures contracts; provider-defined roll and adjustment convention; roll gaps possible; no local back-adjustment')
    if rejected_dates:
        notes.append(f'{len(rejected_dates)} source date(s) with missing OHLC excluded; affected aggregate periods omitted')
    if empty_weekend_dates:
        notes.append(f'{empty_weekend_dates} empty weekend daily slot(s) excluded; no candles synthesized')
    if excluded:
        notes.append(f'{excluded} non-candle quote snapshot(s) excluded')
    result = ProviderCandles(closed, notes)
    # Preserve the provider's session schedule for next-candle strategy checks.
    result.sessions = sorted((int(start*1000),int(end*1000)) for start,end in sessions.values())
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
    return candles_from_chart(request_chart(timeframe if timeframe in ('1h', '4h') else '1d', asset['symbol']),
                              timeframe, int(time.time()*1000), asset['symbol'])
