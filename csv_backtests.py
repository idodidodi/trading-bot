"""User-supplied OHLC replay over the file's own closed-candle date range."""
import csv
from bisect import bisect_left
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import time

from platform_app import load_rules
from scanner import Candle, check_candles, detect, interval_end, minimum_history, timestamp
import pin_bars
import bb_pin_bars
import rsi_pb

MAX_BYTES = 2_000_000
MAX_ROWS = 20_000
STRATEGIES = ('pin-bars', 'bb-pin-bars', 'rsi-pb', 'confirmed', 'warmup')


def parse_time(value):
    if re.fullmatch(r'\d+(?:\.\d+)?',value):
        number=float(value)
        return int(number if number>=1e12 else number*1000)
    return timestamp(value)


def parse(text, timeframe, now):
    if not isinstance(text,str) or not 0 < len(text.encode()) <= MAX_BYTES:
        raise ValueError('CSV must be at most 2 MB')
    if timeframe not in ('1h','4h','daily','weekly','monthly'):
        raise ValueError('Select the CSV candle timeframe')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not reader.fieldnames or not {'open','high','low','close'} <= set(reader.fieldnames):
        raise ValueError('CSV requires open, high, low and close columns')
    key='timestamp' if 'timestamp' in reader.fieldnames else 'time' if 'time' in reader.fieldnames else None
    if not key:raise ValueError('CSV requires time or timestamp with timezone, or Unix seconds/milliseconds')
    candles=[]
    try:
        for row in reader:
            if len(candles)>=MAX_ROWS:raise ValueError('CSV exceeds 20,000 candles')
            start=parse_time(row[key].strip())
            end=parse_time(row['closed_at'].strip()) if row.get('closed_at') else int(interval_end(datetime.fromtimestamp(start/1000,timezone.utc),timeframe).timestamp()*1000)
            candles.append(Candle(start,end,*(float(row[k]) for k in ('open','high','low','close'))))
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Malformed CSV row') from exc
    if not candles:raise ValueError('CSV has no candles')
    candles.sort(key=lambda c:c.start)
    closed=check_candles(candles,now)
    if not closed:raise ValueError('CSV has no completed candles')
    return closed, len(candles)-len(closed), 'explicit closed_at' if 'closed_at' in reader.fieldnames else 'nominal timeframe from each start; session closes are not verified'


def run(body, now=None):
    now=int(time.time()*1000) if now is None else now
    strategy=body.get('strategy','pin-bars');symbol=body.get('symbol','');frame=body.get('timeframe')
    if strategy not in STRATEGIES:raise ValueError('Select Pin-Bars, BB Pin-Bars or RSI divergence')
    if not isinstance(symbol,str) or not re.fullmatch(r'[A-Za-z0-9_:./!\-]{1,100}',symbol):
        raise ValueError('Enter an exact symbol or instrument label')
    bars,excluded,alignment=parse(body.get('csv'),frame,now)
    engine={'pin-bars':pin_bars,'bb-pin-bars':bb_pin_bars,'rsi-pb':rsi_pb}.get(strategy)
    rules=engine.load_rules() if engine else load_rules()
    minimum=engine.minimum_history(rules) if engine else minimum_history(rules)
    if len(bars)<minimum:raise ValueError(f'Need {minimum} completed candles; CSV has {len(bars)}')
    source='uploaded-csv:'+hashlib.sha256(body['csv'].encode()).hexdigest()[:16]
    market=body.get('market','crypto')
    if market not in ('crypto','forex','stock'):raise ValueError('Select a supported market calendar')
    signals=(rsi_pb.detect(bars,rules,source,symbol,frame,dict(market=market)) if strategy=='rsi-pb' else
             engine.detect(bars,rules,source,symbol,frame) if engine else detect(bars,rules,symbol,frame,provisional=strategy=='warmup'))
    # Outcomes describe the next 5/10 closed candles, without inventing trade rules.
    starts=[b.start for b in bars]
    for signal in signals:
        following=bars[bisect_left(starts,signal['confirmed_at']):bisect_left(starts,signal['confirmed_at'])+10]
        d=1 if signal['direction']=='bullish' else -1
        close=signal.get('close',signal.get('price2'))
        signal['forward']={}
        for horizon in (5,10):
            window=following[:horizon]
            if len(window)<horizon or close==0:
                signal['forward'][str(horizon)]=dict(status='incomplete',candles=len(window))
            else:
                signal['forward'][str(horizon)]=dict(status='complete',candles=horizon,
                    directional_close_pct=d*(window[-1].close/close-1)*100,
                    favorable_pct=max(0,max(d*((b.high if d==1 else b.low)/close-1)*100 for b in window)),
                    adverse_pct=max(0,max(-d*((b.low if d==1 else b.high)/close-1)*100 for b in window)))
    report=dict(strategy=strategy,symbol=symbol,timeframe=frame,source=source,market=market,
        filename=str(body.get('filename','upload.csv'))[:200],start_at=bars[0].start,end_at=bars[-1].end,
        candles=len(bars),excluded_open_candles=excluded,warmup_candles=minimum-1 if engine else minimum,
        limited_history=len(bars)<250,alignment=alignment,rules=rules,signals=signals,
        generated_at=now,signal_count=len(signals),
        note='Signal replay and subsequent price movement; no entry/exit rules, trading returns or win rate are defined. Uploaded indicator columns are ignored.')
    report['id']=hashlib.sha256(json.dumps([source,strategy,symbol,frame,rules,report['end_at']]+([market] if strategy=='rsi-pb' else []),sort_keys=True).encode()).hexdigest()
    return report


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS csv_backtest_runs(id TEXT PRIMARY KEY,created INTEGER,report TEXT,csv TEXT)')


def save(store, body):
    report=run(body)
    with store.connect() as db:
        initialize(db)
        db.execute('INSERT OR REPLACE INTO csv_backtest_runs VALUES(?,?,?,?)',
                   (report['id'],report['generated_at'],json.dumps(report),body['csv']))
    return view(store,report['id'])


def view(store, report_id=None, page=0, strategy=None):
    with store.connect() as db:
        initialize(db)
        rows=db.execute('SELECT id,report FROM csv_backtest_runs ORDER BY created DESC').fetchall()
    reports=[json.loads(row[1]) for row in rows]
    selected=next((r for r in reports if (report_id is None or r['id']==report_id) and (strategy is None or r['strategy']==strategy)),None)
    if report_id and selected is None:raise LookupError('CSV backtest not found')
    catalog=[{k:r[k] for k in ('id','filename','symbol','timeframe','strategy','start_at','end_at','signal_count')} for r in reports[:50]]
    if selected:
        matches=selected['signals']
        selected=selected|dict(signals=matches[page*50:(page+1)*50],has_more=len(matches)>(page+1)*50)
    return dict(report=selected,reports=catalog)
