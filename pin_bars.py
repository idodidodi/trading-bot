"""Independent closed-candle EMA Pin-Bar detection and persistent live results."""
import hashlib
import json
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
    ):
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f'Cannot read Pin-Bars {key} from skill')
        rules[key] = cast(match[1])
    if not (0 < rules['fast'] < rules['slow'] and math.isfinite(rules['ratio']) and rules['ratio'] > 0):
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


def direction(bar, fast, slow, ratio):
    body = abs(bar.open - bar.close)
    lower = min(bar.open, bar.close) - bar.low
    upper = bar.high - max(bar.open, bar.close)
    if (fast > slow and bar.close > slow and lower > body * ratio
            and lower > upper and (bar.low <= fast or bar.low <= slow)):
        return 'bullish'
    if (fast < slow and bar.close < slow and upper > body * ratio
            and upper > lower and (bar.high >= fast or bar.high >= slow)):
        return 'bearish'


def detect(candles, rules, source, symbol, timeframe):
    fast = ema([b.close for b in candles], rules['fast'])
    slow = ema([b.close for b in candles], rules['slow'])
    signals = []
    for i in range(rules['slow'], len(candles)):
        b = candles[i]
        bias = direction(b, fast[i], slow[i], rules['ratio'])
        if bias:
            signals.append(dict(strategy='pin-bars', version=rules['version'], source=source,
                symbol=symbol, timeframe=timeframe, direction=bias, candle_at=b.start,
                confirmed_at=b.end, open=b.open, high=b.high, low=b.low, close=b.close,
                fast_ema=fast[i], slow_ema=slow[i], body=abs(b.open-b.close),
                lower_wick=min(b.open,b.close)-b.low, upper_wick=b.high-max(b.open,b.close),
                rules=dict(rules)))
    return signals


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS pin_bar_state(feed TEXT,timeframe TEXT,last_end INTEGER,PRIMARY KEY(feed,timeframe))')
    db.execute('CREATE TABLE IF NOT EXISTS pin_bar_signals(id TEXT PRIMARY KEY,confirmed_at INTEGER,payload TEXT NOT NULL)')


def scan(store, asset, timeframe, candles, rules, now, selection_start=None, catchup=False):
    minimum = rules['slow'] + 1
    if len(candles) < minimum:
        return dict(status='insufficient history', candles=len(candles), reason=f'Need {minimum} closed candles')
    if asset.get('continuous') and any(a.end != b.start for a,b in zip(candles,candles[1:])):
        return dict(status='unavailable', reason='Missing candles in continuous-market feed')
    age = max(0, (now-candles[-1].end)//1000)
    if asset.get('max_data_age_seconds') and age > asset['max_data_age_seconds']:
        return dict(status='unavailable', reason='Latest closed candle is older than the configured freshness limit')
    source = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
    feed = f"{asset['id']}@{source}@{rules['version']}"
    matches = detect(candles, rules, source, asset['id'], timeframe)
    with store.connect() as db:
        initialize(db)
        from scanner_control import initialize as initialize_notifications
        initialize_notifications(db)
        state = db.execute('SELECT last_end FROM pin_bar_state WHERE feed=? AND timeframe=?', (feed,timeframe)).fetchone()
        cutoff = state[0] if state else candles[-1].end
        if state and cutoff < candles[0].start:
            return dict(status='unavailable', reason='Polling gap exceeds available history; supply candle backfill')
        if selection_start is not None:
            cutoff = max(cutoff, selection_start)
        new = 0
        for signal in matches:
            if signal['confirmed_at'] <= cutoff:
                continue
            identity = [feed, timeframe, signal['direction'], signal['candle_at']]
            key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            inserted = db.execute('INSERT OR IGNORE INTO pin_bar_signals VALUES(?,?,?)',
                                  (key,signal['confirmed_at'],json.dumps(signal))).rowcount
            new += inserted
            if inserted and not catchup:
                db.execute('INSERT OR IGNORE INTO notification_queue(id,text) VALUES(?,?)',
                           ('pin-bars:' + key, message(signal)))
        db.execute('INSERT INTO pin_bar_state VALUES(?,?,?) ON CONFLICT(feed,timeframe) DO UPDATE SET last_end=MAX(last_end,excluded.last_end)',
                   (feed,timeframe,candles[-1].end))
    return dict(status='scanned' if state else 'baseline set', candles=len(candles),
                last_closed_at=candles[-1].end, new_signals=new, historical_matches=len(matches),
                limited_history=len(candles)<250, last_closed_age_seconds=age)


def view(store, page=0, page_size=50):
    with store.connect() as db:
        initialize(db)
        rows = db.execute('SELECT payload FROM pin_bar_signals ORDER BY confirmed_at DESC,id LIMIT ? OFFSET ?', (page_size+1,page*page_size)).fetchall()
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='scanner_status'").fetchone()
        status = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone() if exists else None
    coverage = []
    for row in json.loads(status[0]).get('coverage',[]) if status else []:
        pin = row.get('pin_bars', dict(status=row['status'], reason=row.get('reason','Awaiting Pin-Bars scan')))
        coverage.append({k:row[k] for k in ('asset','timeframe','provider','symbol','exchange','checked_at','check_state','next_close_at','data_notes') if k in row} | pin)
    return dict(strategy='pin-bars', signals=[json.loads(r[0]) for r in rows[:page_size]],
                has_more=len(rows)>page_size, coverage=coverage, rules=load_rules(),
                scanned_at=json.loads(status[0]).get('scanned_at') if status else None)


def message(signal):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    stamp = datetime.fromtimestamp(signal['confirmed_at']/1000,ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y at %H:%M (Israel time)')
    rules=signal['rules']
    return (f"{signal['direction'].upper()} Pin-Bars\n{signal['symbol']} · {signal['timeframe']}\n"
            f"EMA {rules['fast']}/{rules['slow']}: {signal['fast_ema']:g} / {signal['slow_ema']:g}\n"
            f"Body: {signal['body']:g} · lower wick: {signal['lower_wick']:g} · upper wick: {signal['upper_wick']:g}\n"
            f"Source: {signal['source']}\nConfirmed: {stamp}\nSignal only · completed Pin-Bar candle")
