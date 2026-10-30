"""Durable Sunday/first-of-month S&P 500 reports; no live signal inserts."""
from datetime import datetime, timedelta, time as wall_time
import json
import os
import time
import uuid
from zoneinfo import ZoneInfo

from postgres_store import DatabaseError, RETRYABLE

ZONE = ZoneInfo('Asia/Jerusalem')
NY = ZoneInfo('America/New_York')
STRATEGIES = ('rsi','pin-bars','bb-pin-bars','rsi-pb')
DIRECTIONS = ('bullish','bearish')


def enabled(config):
    return config.get('stock_reports', {}).get('enabled', False)


def due(now):
    local = datetime.fromtimestamp(now/1000, ZONE)
    if local.hour < 8:
        return []
    return (['weekly'] if local.weekday()==6 else []) + (['monthly'] if local.day==1 else [])


def seconds_to_scan(now):
    local = datetime.fromtimestamp(now, ZONE)
    if due(int(now*1000)):
        return 300  # Revisit same-day retries even with a long normal poll interval.
    for offset in range(8):
        day = local.date()+timedelta(days=offset)
        boundary = datetime.combine(day,wall_time(8),ZONE)
        if (day.weekday()==6 or day.day==1) and boundary>local:
            return max(1,boundary.timestamp()-now)
    return 300


def period(frame, now):
    """Previous US market week/calendar month, independent of Israel's DST."""
    local = datetime.fromtimestamp(now/1000,ZONE)
    if frame=='weekly':
        end = local.date()-timedelta(days=(local.weekday()+1)%7+1) # Saturday before report Sunday
        start = end-timedelta(days=5)  # Monday through Saturday exclusive next Sunday
        end += timedelta(days=1)
    else:
        end = local.date().replace(day=1)
        start = (end-timedelta(days=1)).replace(day=1)
    return dict(start=int(datetime.combine(start,wall_time.min,NY).timestamp()*1000),
                end=int(datetime.combine(end,wall_time.min,NY).timestamp()*1000),
                label=f'{start.isoformat()} to {(end-timedelta(days=1)).isoformat()} (US market dates)')


def rules_snapshot(rules):
    import pin_bars, bb_pin_bars, rsi_pb
    return {'rsi':rules,'pin-bars':pin_bars.load_rules(),'bb-pin-bars':bb_pin_bars.load_rules(),'rsi-pb':rsi_pb.load_rules(rules)}


def evaluate(ticker, bars, run):
    import scanner, pin_bars, bb_pin_bars, rsi_pb
    bars = scanner.check_candles(bars,run['cutoff'])
    rules = run['rules']; frame = run['frame']; p = run['period']
    metadata = run['membership']['members'][ticker]
    source = f'alpaca:{run["feed"]}:{ticker}'
    expected = expected_close(p)
    result=[]; coverage=[]
    for strategy in STRATEGIES:
        module = {'rsi':scanner,'pin-bars':pin_bars,'bb-pin-bars':bb_pin_bars,'rsi-pb':rsi_pb}[strategy]
        minimum = module.minimum_history(rules[strategy])
        note = ''
        if not bars or bars[-1].end != expected:
            note = 'Latest completed period candle unavailable'
        elif len(bars)<minimum:
            note = f'Insufficient history: {len(bars)} candles; need {minimum}'
        coverage.append(dict(ticker=ticker,strategy=strategy,candles=len(bars),reason=note,
                             limited_history=len(bars)<250))
        if note:
            continue
        if strategy=='rsi':
            signals=module.detect(bars,rules[strategy],source,frame,provisional=True)
        elif strategy=='rsi-pb':
            signals=module.detect(bars,rules[strategy],source,ticker,frame,dict(provider='alpaca',market='stock',symbol=ticker))
        else:
            signals=module.detect(bars,rules[strategy],source,ticker,frame)
        indexed={bar.start:bar for bar in bars}
        seen=set()
        for signal in signals:
            if not p['start']<=signal['confirmed_at']<p['end']:
                continue
            start=signal['pivot2'] if strategy=='rsi' else signal['candle_at']
            bar=indexed[start]; key=(signal['direction'],bar.start,bar.end)
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(ticker=ticker,name=metadata['name'],sector=metadata['sector'],strategy=strategy,
                               direction=signal['direction'],start=session_open(bar),end=bar.end,open=bar.open,close=bar.close))
    return result, coverage


def session_open(bar):
    from market_calendar import stock_trading_day
    day=datetime.fromtimestamp(bar.start/1000,NY).date()
    while not stock_trading_day(day):
        day+=timedelta(days=1)
    return int(datetime.combine(day,wall_time(9,30),NY).timestamp()*1000)


def expected_close(p):
    from market_calendar import previous_session, stock_close
    last=datetime.fromtimestamp(p['end']/1000,NY).date()-timedelta(days=1)
    return int(stock_close(previous_session(last)).timestamp()*1000)


def artifacts(run):
    from stock_report_pdf import render, LABELS
    docs=[]; empty=[]
    prefix=f'sp500-{run["frame"]}-{run["day"]}'
    for strategy in STRATEGIES:
        for direction in DIRECTIONS:
            rows=[r for r in run['rows'] if r['strategy']==strategy and r['direction']==direction]
            if rows:
                docs.append(dict(filename=f'{prefix}-{strategy}-{direction}.pdf',strategy=strategy,
                                 caption=f'S&P 500 {run["frame"]} | {LABELS[strategy]} | {direction}',
                                 content=render(run,strategy,direction,rows)))
            elif any(c['strategy']==strategy and not c.get('reason') for c in run['coverage']):
                empty.append((strategy,direction))
    if empty:
        docs.append(dict(filename=f'{prefix}-no-matches.pdf',strategy=None,caption=f'S&P 500 {run["frame"]} | No matches in evaluated stocks',
                         content=render(run,empty=empty)))
    issues=[i for i in run['coverage'] if i.get('reason')]
    # Short histories above minimum remain valid but disclose their limited warm-up.
    issues += [i | dict(reason=f'Limited history: {i["candles"]} of 250 recommended candles')
               for i in run['coverage'] if not i.get('reason') and i.get('limited_history')]
    for ticker, meta in run['membership']['members'].items():
        if meta['name']=='Unavailable' or meta['sector']=='Unavailable':
            issues.append(dict(ticker=ticker,reason='Company name or sector metadata unavailable'))
    if issues:
        docs.append(dict(filename=f'{prefix}-coverage.pdf',strategy=None,caption=f'S&P 500 {run["frame"]} | Coverage notes',
                         content=render(run,issues=issues)))
    return docs


def status(store):
    with store.connect() as db:
        if not db.execute("SELECT to_regclass('bot_runtime.stock_report_runs')").fetchone()[0]:
            return dict(status='migration required',reason='Apply cloud/stock-reports.sql')
        rows=db.execute('SELECT id,state,payload,next_attempt FROM stock_report_runs ORDER BY day DESC,frame LIMIT 12').fetchall()
    return dict(status='scheduled',runs=[dict(id=key,state=state,day=(p:=json.loads(raw))['day'],timeframe=p['frame'],
        period=p['period'],coverage_issues=sum(bool(i.get('reason')) for i in p.get('coverage',[])),
        checked_stocks=len(p.get('completed',[])),universe_count=len(p.get('membership',{}).get('members',{})),
        next_attempt=retry) for key,state,raw,retry in rows])


def save(store, key, owner, run, state='scanning', retry=0):
    with store.connect() as db:
        row=db.execute('UPDATE stock_report_runs SET payload=%s,state=%s,next_attempt=%s,locked_until=extract(epoch FROM clock_timestamp())+1800 WHERE id=%s AND owner=%s RETURNING id',
                       (json.dumps(run),state,retry,key,owner)).fetchone()
        if not row:
            raise RuntimeError('Stock report ownership changed')


def run_due(store, config, rules, now=None, stop=None):
    if not enabled(config):
        return
    now = int(time.time()*1000) if now is None else now
    with store.connect() as db:
        if not db.execute("SELECT to_regclass('bot_runtime.stock_report_runs')").fetchone()[0]:
            store.log('Stock reports unavailable','Apply cloud/stock-reports.sql before reports can run')
            return
        # No weekday catch-up sends. Keep missed work visible in diagnostics.
        day=datetime.fromtimestamp(now/1000,ZONE).date().isoformat()
        db.execute("UPDATE stock_report_runs SET state='missed' WHERE day<%s AND state NOT IN ('sent','missed','incomplete')",(day,))
    for frame in due(now):
        if stop is not None and stop.is_set():
            return
        process(store,frame,rules,now,stop)


def process(store, frame, rules, now, stop):
    from stock_report_data import membership, fetch_batch
    from postgres_store import assert_worker
    day=datetime.fromtimestamp(now/1000,ZONE).date().isoformat()
    key=f'{frame}:{day}';owner=str(uuid.uuid4())
    skeleton=dict(day=day,frame=frame,period=period(frame,now),cutoff=period(frame,now)['end']-1,
                  rules=rules_snapshot(rules),feed=os.environ.get('ALPACA_DATA_FEED','sip'),rows=[],coverage=[],completed=[])
    with store.connect() as db:
        db.execute("INSERT INTO stock_report_runs(id,day,frame,state,payload) VALUES(%s,%s,%s,'scanning',%s) ON CONFLICT DO NOTHING",(key,day,frame,json.dumps(skeleton)))
        row=db.execute("UPDATE stock_report_runs SET owner=%s,locked_until=extract(epoch FROM clock_timestamp())+1800 WHERE id=%s AND state NOT IN ('sent','missed','incomplete') AND locked_until<extract(epoch FROM clock_timestamp()) AND next_attempt<=%s RETURNING payload,state",(owner,key,now/1000)).fetchone()
    if not row:
        return
    run=json.loads(row[0]);state=row[1]
    try:
        assert_worker(store)
        if 'membership' not in run:
            with store.connect() as db:
                previous=db.execute("SELECT payload FROM stock_report_runs WHERE id<>%s AND payload::jsonb ? 'membership' ORDER BY day DESC LIMIT 1",(key,)).fetchone()
            run['membership']=membership(json.loads(previous[0]).get('membership') if previous else None)
            save(store,key,owner,run)
        if state not in ('ready','dry-run'):
            remaining=sorted(set(run['membership']['members'])-set(run['completed']))
            for offset in range(0,len(remaining),20):
                if stop is not None and stop.is_set():
                    return
                if not eligible(run):
                    save(store,key,owner,run,'missed');return
                batch=remaining[offset:offset+20]
                assert_worker(store)
                try:
                    histories=fetch_batch(batch,frame,run['cutoff'],250,run['feed'])
                except (ValueError,OSError):
                    if datetime.now(ZONE).hour < 23:
                        save(store,key,owner,run,'retry',time.time()+3600)
                        store.log('Stock report retry',f'{frame}: provider batch unavailable; saved progress')
                        return
                    histories={}
                for ticker in batch:
                    rows,coverage=evaluate(ticker,histories.get(ticker,[]),run)
                    run['rows'].extend(rows);run['coverage'].extend(coverage);run['completed'].append(ticker)
                save(store,key,owner,run)
            docs=artifacts(run)
            with store.connect() as db:
                # Fence artifact creation with the same run ownership as checkpoints.
                if not db.execute('SELECT id FROM stock_report_runs WHERE id=%s AND owner=%s FOR UPDATE',(key,owner)).fetchone():
                    raise RuntimeError('Stock report ownership changed')
                for doc in docs:
                    db.execute('INSERT INTO stock_report_documents(id,run_id,filename,strategy,caption,content) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                               (key+':'+doc['filename'],key,doc['filename'],doc['strategy'],doc['caption'],doc['content']))
                db.execute("UPDATE stock_report_runs SET state='ready' WHERE id=%s",(key,))
            state='ready'
        from local_settings import delivery_dry_run
        if delivery_dry_run(store,os.environ.get('DRY_RUN','false').lower()=='true'):
            save(store,key,owner,run,'dry-run',time.time()+300);return
        with store.connect() as db:
            docs=db.execute('SELECT id,filename,strategy,caption,content FROM stock_report_documents WHERE run_id=%s AND NOT delivered ORDER BY id',(key,)).fetchall()
        from telegram_documents import deliver_document
        for doc_id,filename,strategy,caption,content in docs:
            if stop is not None and stop.is_set():
                return
            if not eligible(run):
                save(store,key,owner,run,'missed');return
            save(store,key,owner,run,'ready')
            deliver_document(store,doc_id,filename,strategy,caption,bytes(content))
            with store.connect() as db:
                db.execute('UPDATE stock_report_documents SET delivered=true WHERE id=%s',(doc_id,))
        state='incomplete' if any(c.get('reason') for c in run['coverage']) else 'sent'
        save(store,key,owner,run,state)
        store.log('Stock reports delivered',f'{frame}: {len(run["completed"])} constituent securities evaluated')
    except (DatabaseError,*RETRYABLE):
        raise
    except Exception:
        # Never persist provider exception payloads, credentials, or request URLs.
        save(store,key,owner,run,'ready' if state in ('ready','dry-run') else 'retry',time.time()+300)
        store.log('Stock report failed',f'{frame}: report generation/delivery failed; retry scheduled')
    finally:
        with store.connect() as db:
            db.execute('UPDATE stock_report_runs SET locked_until=0 WHERE id=%s AND owner=%s',(key,owner))


def eligible(run):
    now=datetime.now(ZONE)
    return now.date().isoformat()==run['day'] and run['frame'] in due(int(now.timestamp()*1000))
