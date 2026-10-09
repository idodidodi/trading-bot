"""Durable per-feed deadlines; successful reads wait for another candle close."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from market_calendar import is_stock, next_market_time, stock_candle_end


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS scan_schedule(key TEXT PRIMARY KEY,next_due INTEGER NOT NULL,payload TEXT NOT NULL)')


def identity(asset, timeframe, rules):
    fields={k:asset.get(k) for k in ('id','provider','symbol','exchange','path','max_data_age_seconds','continuous')}
    fields.update(timeframe=timeframe,skill=rules['skill_hash'])
    if asset['provider']=='csv' and asset.get('path'):
        from pathlib import Path
        from platform_app import ROOT
        path=Path(asset['path'].format(asset=asset['id'],timeframe=timeframe))
        if not path.is_absolute():path=ROOT/path
        try:fields['file_revision']=(path.stat().st_mtime_ns,path.stat().st_size)
        except OSError:pass
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()


def saved(store, key, now):
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT next_due,payload FROM scan_schedule WHERE key=?',(key,)).fetchone()
    if row and now<row[0]:
        return json.loads(row[1]) | dict(check_state='waiting for candle close',next_close_at=row[0],new_signals=0)
    return None


def next_due(asset, timeframe, raw, candles, now, row):
    from scanner import interval_end
    delay=16*60*1000 if asset['provider']=='alpaca' and asset.get('alpaca_feed','sip')=='sip' else 0
    if row['status']=='unavailable':
        return next_market_time(asset,now+3600000)
    candidates=[c.end+delay for c in raw if c.end+delay>now]
    if getattr(raw,'next_close_at',None) and raw.next_close_at+delay>now:
        candidates.append(raw.next_close_at+delay)
    if candidates:
        return min(candidates)
    if candles:
        last=candles[-1]
        start=datetime.fromtimestamp(last.start/1000,timezone.utc)
        for _ in range(2000):
            start=interval_end(start,timeframe)
            if is_stock(asset):
                end=stock_candle_end(start,timeframe)
            else:
                end=interval_end(start,timeframe)
            value=int(end.timestamp()*1000)+delay
            if value>now:
                return next_market_time(asset,value) if timeframe in ('1h','4h','daily') else value
    return next_market_time(asset,now+3600000)


def provider_lag(store,key,row,now,deadline,asset=None):
    """A successful response with an unchanged old bar is not a new close."""
    with store.connect() as db:
        previous=db.execute('SELECT next_due,payload FROM scan_schedule WHERE key=?',(key,)).fetchone()
    if previous and previous[0]<=now and row.get('last_closed_at'):
        old=json.loads(previous[1])
        if old.get('last_closed_at')==row['last_closed_at'] and row['status']!='unavailable':
            row['data_notes']='; '.join(filter(None,[row.get('data_notes'),'Awaiting the next completed provider candle; retry in one hour']))
            return min(deadline,next_market_time(asset or {},now+3600000))
    return deadline


def save(store,key,deadline,row):
    with store.connect() as db:
        initialize(db)
        db.execute('INSERT OR REPLACE INTO scan_schedule VALUES(?,?,?)',(key,deadline,json.dumps(row)))
