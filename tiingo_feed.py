"""Read-only Tiingo forex OHLC; exact pairs, explicit UTC calendar aggregation."""
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

FRAMES = {'1h': '1hour', '4h': '4hour', 'daily': '1day', 'weekly': '1day', 'monthly': '1day'}


def validate_feed(asset, timeframe):
    if timeframe not in FRAMES or not re.fullmatch(r'[A-Z]{3}/[A-Z]{3}', asset.get('symbol', '')):
        raise ValueError('Tiingo requires an exact forex BASE/QUOTE pair and supported timeframe')
    if asset.get('exchange') not in ('Tiingo', 'Tiingo-UTC') or asset.get('feed_confirmed') is not True:
        raise ValueError('Confirm the exact Tiingo forex feed in Assets')


def database():
    from platform_app import ROOT
    path = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / 'tiingo-cache.sqlite3', timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS requests(at REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS responses(key TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL)')
    return db


def request_json(symbol, frequency, start, end):
    token = os.environ.get('TIINGO_API_KEY', '').strip()
    if not token or token.lower().startswith('your_'):
        raise ValueError('Set an actual TIINGO_API_KEY locally in .env')
    # A one-way credential namespace avoids using cached access after a token change.
    # The actual token is never persisted or included in URLs.
    namespace = hashlib.sha256(token.encode()).hexdigest()
    key = json.dumps([namespace, symbol, frequency, start, end])
    now = time.time()
    hourly = int(os.environ.get('TIINGO_REQUESTS_PER_HOUR', '50'))
    daily = int(os.environ.get('TIINGO_REQUESTS_PER_DAY', '1000'))
    if hourly <= 0 or daily <= 0:
        raise ValueError('Tiingo request budgets must be positive')
    db = database()
    try:
        db.execute('BEGIN IMMEDIATE')
        cached = db.execute('SELECT fetched,payload FROM responses WHERE key=?', (key,)).fetchone()
        if cached and now-cached[0] < 300:
            return json.loads(cached[1])
        db.execute('DELETE FROM requests WHERE at<?', (now-86400,))
        hour_count = db.execute('SELECT count(*) FROM requests WHERE at>?', (now-3600,)).fetchone()[0]
        day_count = db.execute('SELECT count(*) FROM requests').fetchone()[0]
        if hour_count >= hourly or day_count >= daily:
            raise ValueError('Tiingo local hourly/daily request budget reached; retry later')
        db.execute('INSERT INTO requests VALUES(?)', (now,))
        db.commit()
        params = urllib.parse.urlencode(dict(startDate=start, endDate=end, resampleFreq=frequency))
        request = urllib.request.Request('https://api.tiingo.com/tiingo/fx/' + symbol.replace('/', '').lower() + '/prices?' + params,
                                         headers={'Authorization': 'Token ' + token, 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            code = exc.code
            # Read only to classify a provider-defined auth error; never log its body.
            body = exc.read(65536).decode(errors='replace').lower()
            exc.close()
            reason = ('Tiingo API token is invalid' if 'invalid' in body and 'token' in body else
                      {401: 'Tiingo API token is invalid', 403: 'Tiingo account lacks access to this forex feed',
                       404: 'Tiingo exact forex pair is unavailable', 429: 'Tiingo quota reached; retry later'}.get(code,
                        'Tiingo data request failed; check pair, access and timeframe'))
            raise ValueError(reason) from None
        except (OSError, ValueError):
            raise ValueError('Tiingo connection or JSON response failed; retry later') from None
        if not isinstance(data, list):
            raise ValueError('Tiingo returned an invalid candle response')
        db.execute('INSERT OR REPLACE INTO responses VALUES(?,?,?)', (key,now,json.dumps(data)))
        db.execute('DELETE FROM responses WHERE fetched<?', (now-86400,))
        db.commit()
        return data
    finally:
        db.close()


def aggregate_daily(bars, timeframe, now):
    """UTC Monday weeks/calendar months; only complete calendar buckets."""
    from scanner import Candle, interval_end
    groups = {}
    for bar in bars:
        dt = datetime.fromtimestamp(bar.start/1000, timezone.utc)
        if dt.hour or dt.minute or dt.second or dt.microsecond:
            raise ValueError('Tiingo daily timestamps are not UTC midnight; calendar alignment needs verification')
        start = dt.replace(day=1) if timeframe == 'monthly' else dt-timedelta(days=dt.weekday())
        groups.setdefault(start, []).append(bar)
    result = []
    for start, members in sorted(groups.items()):
        finish = int(interval_end(start, timeframe).timestamp()*1000)
        # Reject a potentially truncated leading period. Holidays/closures inside a period remain gaps.
        if bars[0].start > int(start.timestamp()*1000) or finish > now:
            continue
        result.append(Candle(int(start.timestamp()*1000), finish, members[0].open,
                             max(c.high for c in members), min(c.low for c in members), members[-1].close))
    return result


def fetch_tiingo(asset, timeframe, *, start=None, end=None):
    from scanner import Candle, ProviderCandles, check_candles, interval_end, timestamp
    validate_feed(asset, timeframe)
    now = int(time.time()*1000)
    cutoff = min(now, timestamp(end)) if end else now
    final = datetime.fromtimestamp(cutoff/1000, timezone.utc)
    first = start or ('2020-01-01' if timeframe in ('daily','weekly','monthly') else (final-timedelta(days=120)).date().isoformat())
    values = request_json(asset['symbol'], FRAMES[timeframe], first, final.date().isoformat())
    try:
        bars = []
        native = 'daily' if timeframe in ('weekly','monthly') else timeframe
        for row in values:
            if row.get('ticker') != asset['symbol'].replace('/','').lower():
                raise ValueError('Tiingo returned a different forex pair')
            stamp = timestamp(row['date'])
            dt = datetime.fromtimestamp(stamp/1000, timezone.utc)
            bars.append(Candle(stamp, int(interval_end(dt,native).timestamp()*1000),
                               *(float(row[k]) for k in ('open','high','low','close'))))
    except (KeyError, TypeError, OverflowError):
        raise ValueError('Tiingo returned malformed OHLC candles') from None
    except ValueError as exc:
        if str(exc) == 'Tiingo returned a different forex pair':
            raise
        raise ValueError('Tiingo returned malformed prices or timezone timestamps') from None
    closed = check_candles(bars, cutoff)
    if timeframe in ('weekly','monthly'):
        closed = aggregate_daily(closed,timeframe,cutoff)
    result = ProviderCandles(closed, ['Tiingo forex OHLC; UTC boundaries' +
        ('; aggregated from daily OHLC, complete calendar periods only' if timeframe in ('weekly','monthly') else '; native '+FRAMES[timeframe])])
    return result
