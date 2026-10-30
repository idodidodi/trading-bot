"""Independent closed-candle EMA Pin-Bar detection and persistent live results."""
import hashlib
import math
import re

from platform_app import ROOT


def load_rules():
    text = (ROOT / 'skills/pin-bars/SKILL.md').read_text(encoding='utf-8')
    rules = {}
    for key, pattern, cast in (
        ('fast', r'- Fast EMA: (\d+),', int),
        ('slow', r'- Slow EMA: (\d+),', int),
        ('ratio', r'- Pin-Bar ratio: ([\d.]+)\.', float),
        ('max_short_wick_ratio', r'- Maximum short-wick ratio: ([\d.]+)\.', float),
    ):
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f'Cannot read Pin-Bars {key} from skill')
        rules[key] = cast(match[1])
    if not (0 < rules['fast'] < rules['slow'] and math.isfinite(rules['ratio']) and rules['ratio'] > 0
            and math.isfinite(rules['max_short_wick_ratio']) and 0 <= rules['max_short_wick_ratio'] < 1):
        raise ValueError('Invalid Pin-Bars parameters')
    rules['seed'] = 'first close; exclude first slow-period candles'
    rules['version'] = 'pin-bars-v1-' + hashlib.sha256(text.encode()).hexdigest()[:16]
    return rules


def ema(closes, period):
    result = []
    value = closes[0] if closes else 0
    alpha = 2 / (period + 1)
    for close in closes:
        value += alpha * (close - value)
        result.append(value)
    return result


def direction(bar, fast, slow, ratio, max_short_wick_ratio=0.66):
    body = abs(bar.open - bar.close)
    lower = min(bar.open, bar.close) - bar.low
    upper = bar.high - max(bar.open, bar.close)
    # Bullish bias comes from the dominant lower wick, regardless of body color.
    # User override: allow bullish pin bars below the slow EMA or in a downtrend.
    # fast > slow
    # bar.close > slow
    if (lower > body * ratio
            and lower > upper and upper <= lower * max_short_wick_ratio and (bar.low <= fast or bar.low <= slow)):
        return 'bullish'
    if (fast < slow and bar.close < slow and upper > body * ratio
            and upper > lower and lower <= upper * max_short_wick_ratio and (bar.high >= fast or bar.high >= slow)):
        return 'bearish'


def detect(candles, rules, source, symbol, timeframe):
    fast = ema([b.close for b in candles], rules['fast'])
    slow = ema([b.close for b in candles], rules['slow'])
    signals = []
    for i in range(rules['slow'], len(candles)):
        b = candles[i]
        bias = direction(b, fast[i], slow[i], rules['ratio'], rules['max_short_wick_ratio'])
        if bias:
            signals.append(dict(strategy='pin-bars', version=rules['version'], source=source,
                symbol=symbol, timeframe=timeframe, direction=bias, candle_at=b.start,
                confirmed_at=b.end, open=b.open, high=b.high, low=b.low, close=b.close,
                fast_ema=fast[i], slow_ema=slow[i], body=abs(b.open-b.close),
                lower_wick=min(b.open,b.close)-b.low, upper_wick=b.high-max(b.open,b.close),
                rules=dict(rules)))
    return signals


STRATEGY = 'pin-bars'
TABLE_PREFIX = 'pin_bar'
COVERAGE_KEY = 'pin_bars'


def minimum_history(rules):
    return rules['slow'] + 1


def initialize(db):
    import sys
    from candle_strategy import initialize as initialize_strategy
    return initialize_strategy(db, sys.modules[__name__])


def scan(store, asset, timeframe, candles, rules, now, selection_start=None, catchup=False):
    import sys
    from candle_strategy import scan as scan_strategy
    return scan_strategy(store, asset, timeframe, candles, rules, now, selection_start, catchup, engine=sys.modules[__name__])


def view(store, page=0, page_size=50):
    import sys
    from candle_strategy import view as view_strategy
    return view_strategy(store, page, page_size, engine=sys.modules[__name__])


def message(signal):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    stamp = datetime.fromtimestamp(signal['confirmed_at']/1000,ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y at %H:%M (Israel time)')
    provider_key = signal['source'].split(':', 1)[0]
    provider = {'kraken': 'Kraken', 'twelvedata': 'Twelve Data', 'oanda': 'OANDA',
                'tiingo': 'Tiingo', 'alpaca': 'Alpaca', 'fmp': 'FMP',
                'yahoo': 'Yahoo', 'csv': 'CSV'}.get(provider_key.lower(), provider_key)
    return (f"{signal['direction'].upper()} Pin-Bars\n{signal['symbol']} · {signal['timeframe']} · Price: {signal['close']}\n"
            f"Confirmed: {stamp}\n--------------------\nProvider: {provider}")
