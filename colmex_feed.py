"""Colmex scanner adapter; operator-controlled local paths, never browser paths."""
import os
import time


def bridge():
    try:
        from colmex_mt4 import reader
        return reader
    except ImportError:
        raise ValueError('Install the separate colmex-mt4 package in the scanner environment') from None


def directory():
    value = os.environ.get('COLMEX_FILES_DIR', '')
    if not value:
        raise ValueError('Colmex terminal directory is not configured')
    return value


def validate_feed(asset, timeframe):
    import re
    if (timeframe not in ('1h','4h','daily')
            or not re.fullmatch(r'[A-Za-z0-9#_.-]{1,64}', asset.get('symbol',''))
            or asset.get('symbol') in ('.','..') or asset.get('exchange') != 'Colmex-MT4'
            or asset.get('feed_confirmed') is not True):
        raise ValueError('Confirm exact Colmex symbol, Colmex-MT4 exchange and supported timeframe')
    if asset.get('tradingview_symbol'):
        raise ValueError('Colmex has no verified identical TradingView mapping')


def fetch_colmex(asset, timeframe):
    from scanner import Candle, ProviderCandles
    validate_feed(asset, timeframe)
    reader = bridge()
    policy_path = os.environ.get('COLMEX_TIME_POLICY', '')
    if not policy_path:
        raise ValueError('Colmex historical timezone mapping is not verified')
    now = time.time()
    try:
        bars = reader.candles(directory(),asset['symbol'],timeframe,reader.read_json(policy_path,65536),now)
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError('Colmex files unavailable or malformed; awaiting terminal/bridge') from exc
    result = ProviderCandles([Candle(int(a*1000),int(b*1000),o,h,l,c) for a,b,o,h,l,c in bars],
        ['Colmex MT4 bid candles; verified UTC mapping; complete contiguous M1 buckets only'])
    result.next_close_at = (int(now)//reader.FRAMES[timeframe]+1)*reader.FRAMES[timeframe]*1000
    return result
