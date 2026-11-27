"""Telegram-only NEAR 5m RSI exception, evaluated without future candles."""
import math

VERSION = 'near-5m-rsi-atr14-v1'
PERIOD = 14
MULTIPLE = 3


def annotation(asset, candles, signal):
    if asset.get('id') != 'NEARUSD' or signal.get('timeframe') != '5m':
        return {}
    evidence = dict(version=VERSION, qualifies=False, period=PERIOD, multiple=MULTIPLE)
    bars = [c for c in candles if c.end <= signal['confirmed_at']]
    index = next((i for i,c in enumerate(bars) if c.start == signal['pivot2']), None)
    if index is None:
        return {'near_rsi_atr': evidence}
    # Wilder ATR: seed with 14 true ranges, then smooth. Save the ATR before
    # each candle so the candidate's own range cannot inflate its threshold.
    atr = None
    seed = []
    previous = None
    before = []
    for c in bars[:index+1]:
        if previous is not None and previous.end != c.start:
            atr = None
            seed = []
            previous = None
        before.append(atr)
        tr = max(c.high-c.low, abs(c.high-previous.close), abs(c.low-previous.close)) if previous else c.high-c.low
        if not math.isfinite(tr) or tr < 0:
            atr = None
            seed = []
            previous = None
            continue
        if atr is None:
            seed.append(tr)
            if len(seed) == PERIOD:
                atr = sum(seed)/PERIOD
        else:
            atr = (atr*(PERIOD-1)+tr)/PERIOD
        previous = c
    for candidate in (index, index-1):
        if candidate < 0:
            continue
        c = bars[candidate]
        if candidate != index and c.end != bars[index].start:
            continue
        baseline = before[candidate]
        span = c.high-c.low
        if baseline is not None and baseline > 0 and math.isfinite(span) and span >= MULTIPLE*baseline:
            evidence.update(qualifies=True, candle_at=c.start, candle_closed_at=c.end,
                            pivot_at=bars[index].start, candles_before=index-candidate,
                            candle_range=span, atr_before=baseline, range_atr=span/baseline)
            break
    return {'near_rsi_atr': evidence}


def allows(payload):
    if payload.get('asset') != 'NEARUSD' or payload.get('timeframe') != '5m':
        return True
    evidence = payload.get('near_rsi_atr') or {}
    return isinstance(evidence, dict) and evidence.get('version') == VERSION and evidence.get('qualifies') is True
