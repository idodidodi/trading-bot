"""OANDA v20 read-only midpoint candles, with explicit environment and session alignment."""
from datetime import datetime, timedelta, timezone
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

GRANULARITIES = {'1h': 'H1', '4h': 'H4', 'daily': 'D', 'weekly': 'W', 'monthly': 'M'}
HOSTS = {'practice': 'https://api-fxpractice.oanda.com', 'live': 'https://api-fxtrade.oanda.com'}
SESSION_ZONE = ZoneInfo('America/New_York')


def environment():
    value = os.environ.get('OANDA_ENVIRONMENT', 'practice')
    if value not in HOSTS:
        raise ValueError('OANDA_ENVIRONMENT must be practice or live')
    return value


def instrument(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z]{3}/[A-Z]{3}', symbol):
        raise ValueError('OANDA forex requires an exact BASE/QUOTE pair, e.g. EUR/USD')
    return symbol.replace('/', '_')


def validate_feed(asset, timeframe):
    if timeframe not in GRANULARITIES:
        raise ValueError('Unsupported OANDA candle timeframe')
    instrument(asset.get('symbol'))
    if asset.get('feed_confirmed') is not True:
        raise ValueError('Confirm the exact OANDA forex feed in Assets')
    if asset.get('exchange') not in ('OANDA', 'OANDA-practice', 'OANDA-live'):
        raise ValueError('Select OANDA as the provider exchange')


def request_json(path, params=None):
    token = os.environ.get('OANDA_API_TOKEN', '').strip()
    if not token:
        raise ValueError('OANDA_API_TOKEN is missing; configure it locally in .env')
    url = HOSTS[environment()] + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
                                                 'Accept-Datetime-Format': 'RFC3339'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
            if not isinstance(data, dict):
                raise ValueError('Invalid provider response')
            return data
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError({401: 'OANDA token is invalid for the selected practice/live environment',
                          403: 'OANDA account does not permit access to this feed',
                          404: 'OANDA instrument or account is unavailable; verify exact coverage',
                          429: 'OANDA rate limit reached; retry next scan'}.get(code,
                          'OANDA request rejected; check account access and provider availability')) from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('OANDA connection failed; retry next scan') from None
    except (ValueError, TypeError):
        raise ValueError('OANDA returned an invalid JSON response') from None


def available_forex():
    account = os.environ.get('OANDA_ACCOUNT_ID', '')
    if not re.fullmatch(r'[A-Za-z0-9-]+', account):
        raise ValueError('Set OANDA_ACCOUNT_ID locally to verify account-specific forex coverage')
    data = request_json('/v3/accounts/' + account + '/instruments')
    if not isinstance(data.get('instruments'), list):
        raise ValueError('OANDA returned an invalid instrument catalogue')
    return {r['name'].replace('_', '/') for r in data['instruments']
            if r.get('type') == 'CURRENCY' and re.fullmatch(r'[A-Z]{3}_[A-Z]{3}', r.get('name', ''))}


def candle_end(start, timeframe):
    if timeframe in ('1h', '4h'):
        return start + timedelta(hours=1 if timeframe == '1h' else 4)
    local = start.astimezone(SESSION_ZONE)
    if timeframe == 'monthly':
        month = local.month % 12 + 1
        end = local.replace(year=local.year + (local.month == 12), month=month, day=1, hour=17)
    else:
        end = local + timedelta(days=1 if timeframe == 'daily' else 7)
    return end.astimezone(timezone.utc)


def fetch_oanda(asset, timeframe):
    from scanner import Candle, ProviderCandles, timestamp
    validate_feed(asset, timeframe)
    name = instrument(asset['symbol'])
    data = request_json('/v3/instruments/' + name + '/candles',
                        dict(granularity=GRANULARITIES[timeframe], count=500, price='M', smooth='false',
                             dailyAlignment=17, alignmentTimezone='America/New_York', weeklyAlignment='Friday'))
    if data.get('instrument') != name or data.get('granularity') != GRANULARITIES[timeframe]:
        raise ValueError('OANDA returned a different instrument or candle granularity')
    rows = data.get('candles')
    if not isinstance(rows, list):
        raise ValueError('OANDA returned an invalid candle response')
    try:
        starts = [timestamp(row['time']) for row in rows]
        if any(a >= b for a, b in zip(starts, starts[1:])):
            raise ValueError('OANDA returned duplicate or unordered candle timestamps')
        bars = ProviderCandles([], [f'OANDA {environment()} midpoint candles; 17:00 New York daily alignment, Friday weekly alignment'])
        for i, row in enumerate(rows):
            if type(row.get('complete')) is not bool:
                raise ValueError('OANDA candle completion flag is missing or invalid')
            start = datetime.fromtimestamp(starts[i] / 1000, timezone.utc)
            end = int(candle_end(start, timeframe).timestamp() * 1000)
            # Retain gaps across market closures; only shorten an overlapping native boundary.
            if i + 1 < len(starts):
                end = min(end, starts[i + 1])
            if not row['complete']:
                if i != len(rows) - 1:
                    raise ValueError('OANDA returned an uncommitted candle inside closed history')
                bars.next_close_at = end
                continue
            prices = row['mid']
            bars.append(Candle(starts[i], end, *(float(prices[k]) for k in ('o', 'h', 'l', 'c'))))
        return bars
    except ValueError as exc:
        known = {'OANDA returned duplicate or unordered candle timestamps',
                 'OANDA candle completion flag is missing or invalid',
                 'OANDA returned an uncommitted candle inside closed history'}
        if str(exc) in known:
            raise
        raise ValueError('OANDA returned malformed candle data') from None
    except (KeyError, TypeError, OverflowError, AttributeError):
        raise ValueError('OANDA returned malformed candle data') from None
