"""Public Kraken spot candles; exact pairs, native intervals, no credentials."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

INTERVALS = {'1h': 60, '4h': 240, 'daily': 1440, 'weekly': 10080}


def validate_feed(asset, timeframe):
    if timeframe not in INTERVALS:
        raise ValueError('Kraken has no calendar-month candles; select Twelve Data for monthly')
    if not re.fullmatch(r'[A-Z0-9]+/[A-Z0-9]+', asset.get('symbol', '')):
        raise ValueError('Kraken requires an exact BASE/QUOTE spot pair')
    if asset.get('exchange') != 'Kraken' or asset.get('feed_confirmed') is not True:
        raise ValueError('Confirm the exact Kraken spot pair and exchange in Assets')


def fetch_kraken(asset, timeframe):
    from scanner import Candle, ProviderCandles
    validate_feed(asset, timeframe)
    # Canonical response keys let us verify BTC/USD rather than trusting an alias
    # such as XBTUSD. Never substitute a different quote currency or venue.
    params = urllib.parse.urlencode(dict(pair=asset['symbol'].replace('/', ''),
                                        interval=INTERVALS[timeframe], assetVersion=1))
    try:
        with urllib.request.urlopen('https://api.kraken.com/0/public/OHLC?' + params, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError('Kraken rate limit reached; retry next scan' if code == 429
                         else 'Kraken request failed; retry next scan') from None
    errors = data.get('error', [])
    if errors:
        if any('Unknown asset pair' in str(e) for e in errors):
            raise ValueError('Kraken spot pair is unavailable; verify the exact pair in Assets')
        if any('limit' in str(e).lower() or 'throttl' in str(e).lower() for e in errors):
            raise ValueError('Kraken rate limit reached; retry next scan')
        raise ValueError('Kraken rejected request; check spot pair and provider availability')
    result = data.get('result', {})
    if set(result) != {asset['symbol'], 'last'} or not isinstance(result[asset['symbol']], list):
        raise ValueError('Kraken returned a different spot pair or invalid candle response')
    rows = result[asset['symbol']]
    duration = INTERVALS[timeframe] * 60000
    # Kraken explicitly says the final entry is uncommitted, even with `since`.
    # Do not evaluate it or infer finality from a delayed response's timestamp.
    candles = []
    for row in rows[:-1]:
        start = int(row[0]) * 1000
        candles.append(Candle(start, start + duration, *(float(v) for v in row[1:5])))
    bars = ProviderCandles(candles, ['Kraken spot feed; final uncommitted candle excluded'])
    bars.next_close_at = int(rows[-1][0]) * 1000 + duration if rows else None
    return bars
