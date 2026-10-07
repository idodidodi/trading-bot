"""Read-only US equity bars, with explicit feed identity and bounded pagination."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FRAMES = {'1h': '1Hour', '4h': '4Hour', 'daily': '1Day', 'weekly': '1Week', 'monthly': '1Month'}
_lock = threading.Lock()
_last_request = 0


def supported(asset):
    return (asset.get('market') not in ('forex', 'crypto', 'index', 'indices', 'commodity', 'commodities')
            and asset.get('feed_confirmed') is True
            and re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', asset.get('symbol', '')) is not None
            and (asset.get('market') in ('stock', 'stocks', 'equity', 'equities', 'etf')
                 or asset.get('exchange') in ('NASDAQ', 'NYSE', 'AMEX', 'NYSE ARCA')))


def fallback_asset(asset):
    if not supported(asset):
        raise ValueError('Alpaca fallback requires a confirmed US equity symbol')
    feed = os.environ.get('ALPACA_DATA_FEED', 'sip')
    if feed not in ('sip', 'iex'):
        raise ValueError('ALPACA_DATA_FEED must be sip or iex')
    return asset | dict(provider='alpaca', exchange='Alpaca-' + feed + '-split',
                        alpaca_feed=feed, tradingview_symbol='', continuous=False)


def request_page(params):
    global _last_request
    key, secret = os.environ.get('ALPACA_API_KEY', ''), os.environ.get('ALPACA_SECRET_KEY', '')
    if not key or not secret or any(v.lower().startswith('your_') for v in (key, secret)):
        raise ValueError('Set actual ALPACA_API_KEY and ALPACA_SECRET_KEY credentials locally')
    request = urllib.request.Request('https://data.alpaca.markets/v2/stocks/bars?' + urllib.parse.urlencode(params),
        headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret})
    try:
        # Shared across scanning and historical downloads, below Basic's 200/min.
        with _lock:
            time.sleep(max(0, .35 - (time.monotonic() - _last_request)))
            _last_request = time.monotonic()
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError({401: 'Alpaca credentials are invalid', 403: 'Alpaca feed access is unavailable',
                          429: 'Alpaca quota reached; retry later'}.get(code, 'Alpaca data request failed')) from None
    except (OSError, ValueError):
        raise ValueError('Alpaca data request or response failed') from None


def fetch_alpaca(asset, timeframe, *, start=None, end=None):
    from scanner import Candle, ProviderCandles, check_candles, interval_end, timestamp
    if timeframe not in FRAMES or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', asset.get('symbol', '')):
        raise ValueError('Unsupported Alpaca stock symbol or timeframe')
    feed = asset.get('alpaca_feed', os.environ.get('ALPACA_DATA_FEED', 'sip'))
    if feed not in ('sip', 'iex'):
        raise ValueError('Unsupported Alpaca data feed')
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=16)
    end = min(timestamp(end), int(cutoff.timestamp()*1000)) if end else int(cutoff.timestamp()*1000)
    live = start is None
    params = dict(symbols=asset['symbol'], timeframe=FRAMES[timeframe], feed=feed,
                  adjustment='split', asof='-', sort='desc' if live else 'asc', limit=500 if live else 10000,
                  start=start or '2016-01-01T00:00:00Z',
                  end=datetime.fromtimestamp(end/1000, timezone.utc).isoformat())
    bars, seen_tokens = [], set()
    for _ in range(200):
        data = request_page(params)
        values = data.get('bars') if isinstance(data, dict) else None
        if not isinstance(values, dict) or set(values) - {asset['symbol']}:
            raise ValueError('Alpaca returned an invalid or different symbol')
        rows = values.get(asset['symbol'], [])
        if not isinstance(rows, list):
            raise ValueError('Invalid Alpaca candle response')
        try:
            for row in rows:
                dt = datetime.fromisoformat(row['t'].replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    raise ValueError()
                local = dt.astimezone(ZoneInfo('America/New_York'))
                close = interval_end(local, timeframe) if timeframe in ('daily','weekly','monthly') else interval_end(dt, timeframe)
                bars.append(Candle(int(dt.timestamp()*1000), int(close.timestamp()*1000),
                                   *(float(row[k]) for k in ('o','h','l','c'))))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError('Invalid Alpaca OHLC or timestamp') from None
        token = data.get('next_page_token')
        if live or not token:
            break
        if not isinstance(token, str) or token in seen_tokens:
            raise ValueError('Alpaca pagination did not advance')
        seen_tokens.add(token)
        params['page_token'] = token
    else:
        raise ValueError('Alpaca historical download exceeded page limit')
    bars.sort(key=lambda c:c.start)
    closed = check_candles(bars, end)
    return ProviderCandles(closed, [f'Alpaca {feed}; split-adjusted; 16-minute delay; conservative calendar closes'])
