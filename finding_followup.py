"""Directional excursions from P2, kept outside immutable finding evidence."""
import json
import time
from dataclasses import asdict
from pathlib import Path
from finding_feedback import finding_id

MODES = ('recovery', 'all')


def calculate(signal, candles, mode='recovery'):
    if mode not in MODES:
        raise ValueError('Invalid follow-up window')
    bars = sorted((asdict(c) if not isinstance(c, dict) else c for c in candles), key=lambda c: c['start'])
    start, price = signal['pivot2'], signal['price2']
    result = dict(version=1, mode=mode, baseline_at=start, baseline_price=price,
                  status='unavailable', rating=None, candles=0)
    if price <= 0 or not any(c['start'] == start for c in bars):
        return result | dict(reason='The original pivot candle is missing from stored history.')
    future = [c for c in bars if c['start'] > start]
    if not future:
        return result | dict(reason='No closed candles after the divergence pivot yet.')
    bullish = signal['direction'] == 'bullish'
    dd = gain = 0.0
    dd_at = gain_at = dd_price = gain_price = recovery_at = adverse_at = None
    count = 0
    for c in future:
        count += 1
        unfavorable, favorable = (c['low'], c['high']) if bullish else (c['high'], c['low'])
        adverse = max(0.0, (price-unfavorable if bullish else unfavorable-price) / price * 100)
        favorable_pct = max(0.0, (favorable-price if bullish else price-favorable) / price * 100)
        if adverse > dd:
            dd, dd_at, dd_price = adverse, c['end'], unfavorable
            recovery_at = None
        if favorable_pct > gain:
            gain, gain_at, gain_price = favorable_pct, c['end'], favorable
        # OHLC cannot establish wick order within a candle. Require a later close.
        if dd_at is not None and dd_at < c['end'] and recovery_at is None and (c['close'] >= price if bullish else c['close'] <= price):
            recovery_at = c['end']
        if adverse > 0 and adverse_at is None:
            adverse_at = c['end']
        if recovery_at is not None and mode == 'recovery':
            break
    ratio = gain / dd if dd else None
    rating = 1 if gain == 0 else 5 if dd == 0 or ratio >= 3 else 4 if ratio >= 2 else 3 if ratio >= 1 else 2 if ratio >= .5 else 1
    return result | dict(status='recovered' if recovery_at else 'ongoing' if adverse_at else 'no drawdown',
        candles=count, through_at=c['end'], drawdown_pct=dd, drawdown_price=dd_price,
        drawdown_at=dd_at, drawdown_elapsed_ms=dd_at-start if dd_at else None,
        gain_pct=gain, gain_price=gain_price, gain_at=gain_at,
        gain_elapsed_ms=gain_at-start if gain_at else None, recovery_at=recovery_at,
        recovery_elapsed_ms=recovery_at-start if recovery_at else None,
        gain_drawdown_ratio=ratio, rating=rating)


def both(signal, candles):
    return {mode: calculate(signal, candles, mode) for mode in MODES}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS finding_followup(finding_id TEXT PRIMARY KEY, results TEXT NOT NULL, calculated_at REAL NOT NULL)')


def stored(db, key):
    initialize(db)
    row = db.execute('SELECT results FROM finding_followup WHERE finding_id=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def run(store, root, source, key, mode='recovery'):
    from candle_views import lookup
    from finding_feedback import latest_report
    from scanner import Candle, check_candles, timestamp
    if mode not in MODES:
        raise ValueError('Invalid follow-up window')
    signal = lookup(store, root, source, key)
    with store.connect() as db:
        state = db.execute('SELECT status FROM finding_state WHERE finding_id=?', (key,)).fetchone()
        if state and state[0] == 'deleted':
            raise LookupError('Finding not found')
    if source == 'backtest':
        with store.connect() as db:
            report = latest_report(db, root)
        for row in report['results']:
            if key in row.get('followups', {}):
                return dict(signal=signal, followup=row['followups'][key][mode])
    table = 'backtest_candle_cache' if source == 'backtest' else 'candle_cache'
    with store.connect() as db:
        exists = db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone()
        rows = db.execute(f'SELECT start,end,open,high,low,close FROM {table} WHERE feed=? AND timeframe=? AND start>=? ORDER BY start',
                          (signal['symbol'], signal['timeframe'], signal['pivot2'])).fetchall() if exists else []
    bars = [Candle(*r) for r in rows]
    if source == 'backtest' and not bars:
        from scanner import read_csv
        symbol = signal['symbol']
        if '/' in symbol or '\\' in symbol:
            raise ValueError('Invalid historical asset')
        path = Path(root)/'data'/'historical-2020'/f"{symbol}-{signal['timeframe']}.csv"
        if path.is_file():
            bars = read_csv(dict(id=symbol, path=str(path)), signal['timeframe'])
    cutoff = timestamp('2021-01-01T00:00:00Z') if source == 'backtest' else int(time.time()*1000)
    results = both(signal, check_candles(bars, cutoff))
    with store.connect() as db:
        initialize(db)
        db.execute('INSERT OR REPLACE INTO finding_followup VALUES(?,?,?)', (key, json.dumps(results), time.time()))
    return dict(signal=signal, followup=results[mode])
