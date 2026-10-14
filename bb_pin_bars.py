"""Independent Bollinger Band pin bars; wick direction defines bias, without EMA filters."""
import hashlib
import json
import math

STRATEGY = 'bb-pin-bars'
TABLE_PREFIX = 'bb_pin_bar'
COVERAGE_KEY = 'bb_pin_bars'


def load_rules():
    rules = dict(bb_period=20, bb_multiplier=2.0, ratio=2.0,
                 seed='20-close SMA and population deviation; includes signal close')
    rules['version'] = 'bb-pin-bars-v1-' + hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]
    return rules


def minimum_history(rules):
    return rules['bb_period']


def bollinger(closes, period, multiplier):
    result = []
    for i in range(len(closes)):
        if i + 1 < period:
            result.append((None, None))
            continue
        window = closes[i+1-period:i+1]
        # Explicit accumulation matches JavaScript across Python versions.
        total = 0.0
        for value in window:
            total += value
        middle = total / period
        variance = 0.0
        for value in window:
            variance += (value-middle)**2
        deviation = math.sqrt(variance / period)
        result.append((middle-multiplier*deviation, middle+multiplier*deviation))
    return result


def direction(bar, lower_bb, upper_bb, ratio):
    body = abs(bar.open-bar.close)
    lower = min(bar.open,bar.close)-bar.low
    upper = bar.high-max(bar.open,bar.close)
    # Body color is irrelevant; a matching BB touch is the sole location filter.
    if lower > body*ratio and lower > upper and bar.low <= lower_bb:
        return 'bullish'
    if upper > body*ratio and upper > lower and bar.high >= upper_bb:
        return 'bearish'


def detect(candles, rules, source, symbol, timeframe):
    bands = bollinger([b.close for b in candles], rules['bb_period'], rules['bb_multiplier'])
    signals = []
    for i in range(minimum_history(rules)-1, len(candles)):
        b = candles[i]
        lower, upper = bands[i]
        bias = direction(b, lower, upper, rules['ratio'])
        if bias:
            signals.append(dict(strategy=STRATEGY, version=rules['version'], source=source,
                symbol=symbol, timeframe=timeframe, direction=bias, candle_at=b.start,
                confirmed_at=b.end, open=b.open, high=b.high, low=b.low, close=b.close,
                lower_bb=lower, upper_bb=upper, body=abs(b.open-b.close),
                lower_wick=min(b.open,b.close)-b.low, upper_wick=b.high-max(b.open,b.close),
                rules=dict(rules)))
    return signals


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
    rules = signal['rules']
    return (f"{signal['direction'].upper()} BB Pin-Bars\n{signal['symbol']} · {signal['timeframe']}\n"
            f"BB {rules['bb_period']}/{rules['bb_multiplier']:g}: {signal['lower_bb']:g} / {signal['upper_bb']:g}\n"
            f"Body: {signal['body']:g} · lower wick: {signal['lower_wick']:g} · upper wick: {signal['upper_wick']:g}\n"
            f"Source: {signal['source']}\nConfirmed: {stamp}\nSignal only · completed BB Pin-Bar candle")
