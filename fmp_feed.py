"""FMP cash-index daily OHLC and complete UTC calendar aggregates.

Daily rows carry a session date, not a timestamp. Conservatively close them at
midnight UTC after that date; never manufacture intraday bars from quotes.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

FRAMES = ('daily', 'weekly', 'monthly')
_cache = {}


def validate_feed(asset, timeframe):
    if timeframe not in FRAMES:
        raise ValueError('FMP supports daily, weekly and monthly index candles; intraday access requires an upgraded subscription')
    if asset.get('symbol') not in ('^GSPC', '^GDAXI') or asset.get('exchange') != 'INDEX' or asset.get('feed_confirmed') is not True:
        raise ValueError('Confirm the exact FMP cash-index symbol and INDEX exchange')


def request_json(endpoint, **params):
    key = os.environ.get('FMP_API_KEY', '').strip()
    if not key:
        raise ValueError('Set FMP_API_KEY locally in .env')
    cache_key = (hashlib.sha256(key.encode()).hexdigest(), endpoint, tuple(sorted(params.items())))
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < 900:
        return cached[1]
    url = 'https://financialmodelingprep.com/stable/' + endpoint + '?' + urllib.parse.urlencode(params | {'apikey': key})
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError({401: 'FMP API key is invalid', 402: 'FMP subscription does not include this symbol or endpoint; upgrade required',
                          403: 'FMP access denied; check subscription', 429: 'FMP quota reached; retry later'}.get(code, 'FMP data request failed')) from None
    except (OSError, ValueError):
        raise ValueError('FMP connection or JSON response failed; retry later') from None
    if not isinstance(data, list):
        raise ValueError('FMP returned an invalid data response')
    if len(_cache) > 64:
        _cache.clear()
    _cache[cache_key] = (now, data)
    return data


def daily_history(symbol, start, end):
    """Page backwards through FMP's 5,000-row response limit."""
    rows = []
    until = end
    for _ in range(30):
        page = request_json('historical-price-eod/full', symbol=symbol, **{'from': start, 'to': until})
        if not page:
            break
        try:
            earliest = min(date.fromisoformat(r['date']) for r in page)
        except (KeyError, TypeError, ValueError):
            raise ValueError('FMP returned malformed session dates') from None
        rows.extend(page)
        if len(page) < 5000 or earliest.isoformat() <= start:
            break
        previous = (earliest - timedelta(days=1)).isoformat()
        if previous >= until:
            raise ValueError('FMP historical pagination did not advance')
        until = previous
    else:
        raise ValueError('FMP historical pagination limit reached')
    return rows


def candles_from_rows(rows, symbol, timeframe, cutoff):
    from scanner import Candle, ProviderCandles, check_candles, interval_end
    bars = []
    try:
        for row in rows:
            if row['symbol'] != symbol:
                raise ValueError('FMP returned a different index symbol')
            day = date.fromisoformat(row['date'])
            start = datetime.combine(day, datetime.min.time(), timezone.utc)
            bars.append(Candle(int(start.timestamp()*1000), int((start+timedelta(days=1)).timestamp()*1000),
                               *(float(row[k]) for k in ('open', 'high', 'low', 'close'))))
    except (KeyError, TypeError, OverflowError):
        raise ValueError('FMP returned malformed OHLC candles') from None
    closed = check_candles(bars, cutoff)
    if timeframe != 'daily':
        groups = {}
        for bar in closed:
            dt = datetime.fromtimestamp(bar.start/1000, timezone.utc)
            start = dt.replace(day=1) if timeframe == 'monthly' else dt-timedelta(days=dt.weekday())
            groups.setdefault(start, []).append(bar)
        result = []
        for start, members in sorted(groups.items()):
            end = int(interval_end(start, timeframe).timestamp()*1000)
            # Drop a truncated leading period and all still-open periods.
            if closed[0].start > int(start.timestamp()*1000) or end > cutoff:
                continue
            result.append(Candle(int(start.timestamp()*1000), end, members[0].open,
                                 max(c.high for c in members), min(c.low for c in members), members[-1].close))
        closed = result
    return ProviderCandles(closed, ['FMP cash-index OHLC; session dates with conservative UTC calendar-day closes; '
                                    + ('native daily data' if timeframe == 'daily' else 'complete calendar periods aggregated from daily OHLC')])


def fetch_fmp(asset, timeframe, *, start=None, end=None):
    from scanner import timestamp
    validate_feed(asset, timeframe)
    now = int(time.time()*1000)
    cutoff = min(now, timestamp(end)) if end else now
    first = (start or '2000-01-01')[:10]
    final = datetime.fromtimestamp(cutoff/1000, timezone.utc).date().isoformat()
    rows = daily_history(asset['symbol'], first, final)
    return candles_from_rows(rows, asset['symbol'], timeframe, cutoff)
