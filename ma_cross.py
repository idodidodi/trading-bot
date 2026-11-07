"""Closed-candle 50/200 SMA golden and death crosses."""
import hashlib
import json
import math

STRATEGY = 'ma-cross'
TABLE_PREFIX = 'ma_cross'
COVERAGE_KEY = 'ma_cross'


def load_rules():
    rules = dict(fast=50, slow=200, timeframe='daily', seed='SMA of closes, including signal close; 201 closed candles required')
    rules['version'] = 'ma-cross-v1-' + hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]
    return rules


def minimum_history(rules):
    return rules['slow'] + 1


def detect(candles, rules, source, symbol, timeframe):
    if timeframe != 'daily':
        return []
    closes = [bar.close for bar in candles]
    averages = []
    for i in range(len(candles)):
        if i + 1 < rules['slow']:
            averages.append(None)
            continue
        window = closes[i + 1 - rules['slow']:i + 1]
        if not all(math.isfinite(value) for value in window):
            averages.append(None)
            continue
        averages.append((math.fsum(window[-rules['fast']:]) / rules['fast'], math.fsum(window) / rules['slow']))
    signals = []
    for i in range(rules['slow'], len(candles)):
        previous, current = averages[i-1], averages[i]
        if previous is None or current is None:
            continue
        pf, ps = previous
        fast, slow = current
        bias = 'bullish' if pf <= ps and fast > slow else 'bearish' if pf >= ps and fast < slow else None
        if bias is None:
            continue
        bar = candles[i]
        signals.append(dict(strategy=STRATEGY, version=rules['version'], source=source, symbol=symbol,
                            timeframe=timeframe, direction=bias, candle_at=bar.start, confirmed_at=bar.end,
                            open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                            fast_sma=fast, slow_sma=slow, previous_fast_sma=pf, previous_slow_sma=ps,
                            rules=dict(rules)))
    return signals


def scan(store, asset, timeframe, candles, rules, now, selection_start=None, catchup=False):
    if timeframe != 'daily':
        return dict(status='disabled', reason='SMA crossovers use daily candles only')
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
    stamp = datetime.fromtimestamp(signal['confirmed_at']/1000, ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y at %H:%M (Israel time)')
    name = 'Golden cross' if signal['direction'] == 'bullish' else 'Death cross'
    return (f"{signal['direction'].upper()} {name}\n{signal['symbol']} · {signal['timeframe']}\n"
            f"SMA50: {signal['fast_sma']:g} · SMA200: {signal['slow_sma']:g}\n"
            f"Close: {signal['close']:g}\nSource: {signal['source']}\nConfirmed: {stamp}\n"
            'Signal only · completed candle')
