"""Public signal contract v1. No subscriber or account policy belongs here."""
import hashlib
import json

# Explicit evidence allowlist: never expose recipient IDs, credentials or asset config.
EVIDENCE = {'symbol','source','market','timeframe','direction','confirmed_at','detected_at',
    'published_at','lifecycle','observed_at','pivot1','pivot2','price1','price2','rsi1','rsi2','band1','band2',
    'band_touch_price','band_slope_pct','spacing','signal_status','alert_timing',
    'pivot_closed_at','candle_at','candle','pivot_candle','followup_candle','version',
    'fast_sma','slow_sma','previous_fast_sma','previous_slow_sma',
    'open','high','low','close','body','upper_wick','lower_wick','ema_fast','ema_slow','atr','breakout_level','roc_pct','efficiency','extension_atr','score'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def signal_event(strategy, signal, *, origin='live', kind='detected', root=None, instrument=None):
    if origin not in ('live','replay') or kind not in ('detected','updated','invalidated'):
        raise ValueError('Invalid event type or origin')
    evidence = {k:v for k,v in signal.items() if k in EVIDENCE}
    version = str(signal.get('version') or signal.get('rules',{}).get('skill_hash') or '')
    if not version:
        raise ValueError('Strategy version required')
    at = signal.get('confirmed_at')
    direction = {'buy':'bullish','sell':'bearish'}.get(signal.get('direction'),signal.get('direction'))
    if type(at) is not int or at <= 0 or direction not in ('bullish','bearish'):
        raise ValueError('Invalid signal time or direction')
    source = signal.get('source') or signal['symbol']
    identity = [strategy,version,source,signal['timeframe'],direction,
                signal.get('pivot2',signal.get('candle_at',at)),signal.get('signal_status','confirmed')]
    sid = hashlib.sha256(canonical(identity).encode()).hexdigest()
    if root:
        evidence['root'] = {k:v for k,v in root.items() if k in EVIDENCE}
    value = dict(contract_version=1,signal_id=sid,kind=kind,origin=origin,
                 strategy=strategy,strategy_version=version,
                 instrument=dict(symbol=instrument or signal['symbol'],source=source,market=signal.get('market','unknown')),
                 timeframe=signal['timeframe'],direction=direction,occurred_at=at,evidence=evidence)
    value['event_id'] = hashlib.sha256(canonical(value).encode()).hexdigest()
    return value
