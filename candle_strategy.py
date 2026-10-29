"""Shared persistence for independently versioned closed-candle strategies."""
from postgres_store import RETRYABLE, DatabaseError
import hashlib
import json


def initialize(db, engine):
    db.require_table(f'{engine.TABLE_PREFIX}_state')
    db.require_table(f'{engine.TABLE_PREFIX}_signals')


def scan(store, asset, timeframe, candles, rules, now, selection_start=None, catchup=False, *, engine):
    minimum = engine.minimum_history(rules)
    if len(candles) < minimum:
        return dict(status='insufficient history', candles=len(candles), reason=f'Need {minimum} closed candles')
    if asset.get('continuous') and any(a.end != b.start for a,b in zip(candles,candles[1:])):
        return dict(status='unavailable', reason='Missing candles in continuous-market feed')
    age = max(0, (now-candles[-1].end)//1000)
    if asset.get('max_data_age_seconds') and age > asset['max_data_age_seconds']:
        return dict(status='unavailable', reason='Latest closed candle is older than the configured freshness limit')
    source = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
    feed = f"{asset['id']}@{source}@{rules['version']}"
    matches = engine.detect(candles, rules, source, asset['id'], timeframe)
    with store.connect() as db:
        initialize(db, engine)
        from scanner_control import initialize as initialize_notifications
        initialize_notifications(db)
        state = db.execute(f'SELECT last_end FROM {engine.TABLE_PREFIX}_state WHERE feed=%s AND timeframe=%s', (feed,timeframe)).fetchone()
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
            inserted = db.execute(f'INSERT INTO {engine.TABLE_PREFIX}_signals VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                  (key,signal['confirmed_at'],json.dumps(signal))).rowcount
            new += inserted
            if inserted and not catchup:
                from telegram_routing import remember
                remember(db, 'notification:' + engine.STRATEGY + ':' + key, asset.get('market') or 'unknown', engine.STRATEGY)
                db.execute('INSERT INTO notification_queue(id,text) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                           (engine.STRATEGY + ':' + key, engine.message(signal)))
        db.execute(f'INSERT INTO {engine.TABLE_PREFIX}_state AS saved VALUES(%s,%s,%s) ON CONFLICT(feed,timeframe) DO UPDATE SET last_end=GREATEST(saved.last_end,excluded.last_end)',
                   (feed,timeframe,candles[-1].end))
    return dict(status='scanned' if state else 'baseline set', candles=len(candles),
                last_closed_at=candles[-1].end, new_signals=new, historical_matches=len(matches),
                limited_history=len(candles)<250, last_closed_age_seconds=age)


def view(store, page=0, page_size=50, *, engine):
    with store.connect() as db:
        initialize(db, engine)
        rows = db.execute(f'SELECT payload FROM {engine.TABLE_PREFIX}_signals ORDER BY confirmed_at DESC,id LIMIT %s OFFSET %s', (page_size+1,page*page_size)).fetchall()
        exists = db.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='scanner_status'").fetchone()
        status = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone() if exists else None
    coverage = []
    for row in json.loads(status[0]).get('coverage',[]) if status else []:
        pin = row.get(engine.COVERAGE_KEY, dict(status=row['status'], reason=row.get('reason','Awaiting Pin-Bars scan')))
        coverage.append({k:row[k] for k in ('asset','timeframe','provider','symbol','exchange','checked_at','check_state','next_close_at','data_notes') if k in row} | pin)
    return dict(strategy=engine.STRATEGY, signals=[json.loads(r[0]) for r in rows[:page_size]],
                has_more=len(rows)>page_size, coverage=coverage, rules=engine.load_rules(),
                scanned_at=json.loads(status[0]).get('scanned_at') if status else None)
