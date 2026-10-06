"""Durable 07:00 / 11:00 Israel-time setup reviews, using cached closed candles."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import time

DESCRIPTIONS = {
    'warmup': 'Warm-up / potential divergence. The second pivot has closed; structural confirmation is still pending.',
    'confirmed': 'Confirmed divergence. The next closed candle confirms the second pivot structure.',
    '07:00': '07:00 setup review / monitoring stage. Cached closed-candle evidence; this scheduled review adds no candle confirmation.',
    '11:00': '11:00 follow-up review / monitoring stage. Cached closed-candle evidence; confirmation status is stated separately.'
}

def review(store, now=None):
    now=time.time() if now is None else now
    local=datetime.fromtimestamp(now,ZoneInfo('Asia/Jerusalem'))
    # Ten-minute delivery window; no stale missed-clock alerts after long downtime.
    if local.hour not in (7,11) or local.minute>=10:return 0
    slot=f'{local.date()} {local.hour:02}:00'
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS alert_reviews(slot TEXT PRIMARY KEY, completed REAL)')
        if db.execute('SELECT 1 FROM alert_reviews WHERE slot=?',(slot,)).fetchone():return 0
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='candle_cache'").fetchone()
        if not exists:return 0
        rows=db.execute('SELECT payload FROM signals WHERE received>? ORDER BY received DESC LIMIT 500',(now-31*86400,)).fetchall()
    count=0;seen=set()
    signals=sorted((json.loads(row[0]) for row in rows),key=lambda s:(s['confirmed_at'],s.get('signal_status')!='provisional'),reverse=True)
    for s in signals:
        pair=tuple(s[k] for k in ('symbol','timeframe','direction','pivot1','pivot2'))
        if pair in seen or s.get('review_slot'):continue
        with store.connect() as db:
            bars=db.execute('SELECT high,low,end FROM candle_cache WHERE feed=? AND timeframe=? AND end>? AND end<=? ORDER BY start',(s['symbol'],s['timeframe'],s['confirmed_at'],int(now*1000))).fetchall()
        if len(bars)>10:continue
        breached=any(lo<s['price2'] if s['direction']=='bullish' else hi>s['price2'] for hi,lo,end in bars)
        if breached:continue
        seen.add(pair)
        stage='warmup' if s.get('signal_status')=='provisional' else 'confirmed'
        latest=bars[-1][2] if bars else s['confirmed_at']
        s.update(review_slot=slot,stage_description=DESCRIPTIONS[f'{local.hour:02}:00']+' '+DESCRIPTIONS[stage]+f' Latest cached close: {datetime.fromtimestamp(latest/1000,ZoneInfo("Asia/Jerusalem")).isoformat()}.')
        count+=store.insert(s)
    with store.connect() as db:db.execute('INSERT OR IGNORE INTO alert_reviews VALUES(?,?)',(slot,now))
    store.log('Scheduled setup review',f'{slot} Israel time; {count} active setups, using cached closed candles')
    return count

def worker(store,stop):
    while not stop.is_set():
        try:review(store)
        except Exception:store.log('Scheduled review failed','Could not evaluate cached setup evidence; retrying')
        stop.wait(30)
