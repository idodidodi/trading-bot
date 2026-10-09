"""Bounded cloud mirror with disk-backed retry queue; disabled without credentials."""
import csv
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
from finding_feedback import canonical, finding_id, latest_report
from platform_app import ROOT


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS cloud_pending(kind TEXT,key TEXT,event_id TEXT,payload TEXT,PRIMARY KEY(kind,key))')
    db.execute('CREATE TABLE IF NOT EXISTS cloud_seen(kind TEXT,key TEXT,event_id TEXT,PRIMARY KEY(kind,key))')
    db.execute('CREATE TABLE IF NOT EXISTS cloud_meta(key TEXT PRIMARY KEY,value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS cloud_conflicts(id TEXT PRIMARY KEY,payload TEXT,created REAL)')


def enqueue(db,kind,key,payload):
    raw=canonical(payload);event=hashlib.sha256((kind+'\0'+key+'\0'+raw).encode()).hexdigest()
    row=db.execute('SELECT event_id FROM cloud_seen WHERE kind=? AND key=?',(kind,key)).fetchone()
    if row and row[0]==event:return
    db.execute('INSERT OR REPLACE INTO cloud_pending VALUES(?,?,?,?)',(kind,key,event,raw))
    db.execute('INSERT OR REPLACE INTO cloud_seen VALUES(?,?,?)',(kind,key,event))


def meta(db,key,default='0'):
    row=db.execute('SELECT value FROM cloud_meta WHERE key=?',(key,)).fetchone();return row[0] if row else default


def snapshot(store):
    from finding_followup import stored
    with store.connect() as db:
        initialize(db)
        config=db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if config:
            enqueue(db,'config','current',dict(revision=config[0],applied_revision=config[1],config=json.loads(config[2])))
        for raw,status,attempts,error in db.execute('SELECT payload,status,attempts,error FROM signals'):
            signal=json.loads(raw);followups=stored(db,finding_id('live',signal));enqueue(db,'live',finding_id('live',signal),dict(id=finding_id('live',signal),signal=signal,followups=followups,status=status,attempts=attempts,error=error))
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='activity_log'").fetchone()
        if exists:
            cutoff=int(meta(db,'log_snapshot'))
            last=cutoff
            for id,t,event,details in db.execute('SELECT id,occurred,event,details FROM activity_log WHERE id>? ORDER BY id LIMIT 1000',(cutoff,)):
                enqueue(db,'log',str(id),dict(occurred=t,event=event,details=details));last=id
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('log_snapshot',str(last)))
        for feed,asset,tf,start,end,op,high,low,close in db.execute('SELECT * FROM candle_cache'):
            enqueue(db,'candle',f'live:{feed}:{tf}:{start}',dict(source='live',symbol=feed,asset=asset,timeframe=tf,start=start,end=end,open=op,high=high,low=low,close=close))
        for key,status,revision,updated in db.execute('SELECT * FROM finding_state'):
            enqueue(db,'finding_state',key,dict(finding_id=key,status=status,revision=revision,updated_at=updated))
        for key,source,evidence,rating,comment,revision,updated in db.execute('SELECT * FROM finding_feedback'):
            enqueue(db,'feedback',key,dict(finding_id=key,source=source,evidence=json.loads(evidence),rating=rating,comment=comment,revision=revision,updated_at=updated))
        for seq,key,source,evidence,rating,comment,revision,updated in db.execute('SELECT * FROM feedback_history'):
            enqueue(db,'feedback_history',f'{key}:{revision}',dict(finding_id=key,source=source,evidence=json.loads(evidence),rating=rating,comment=comment,revision=revision,updated_at=updated))
        status_exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='scanner_status'").fetchone()
        scan=db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone() if status_exists else None
        if scan:
            enqueue(db,'summary','live',json.loads(scan[0]).get('coverage',[]))
            enqueue(db,'summary','scanner',json.loads(scan[0]))
        selections={}
        for table,field,order in [('daily_universe','daily_selection','session'),('weekly_stock_screen','weekly_stock_selection','day')]:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                selected=db.execute(f'SELECT payload FROM {table} ORDER BY {order} DESC LIMIT 1').fetchone()
                if selected:selections[field]=json.loads(selected[0])
        enqueue(db,'summary','selections',selections)
        from scanner_control import initialize as control_initialize
        control_initialize(db)
        for key,status,payload,created in db.execute('SELECT * FROM scanner_commands'):
            enqueue(db,'summary','catchup:'+key,dict(id=key,status=status,created=created,**json.loads(payload)))
        has_runs=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backtest_runs'").fetchone()
        if has_runs:
            reports=[json.loads(row[0]) for row in db.execute('SELECT report FROM backtest_runs ORDER BY id')]
        else:
            report=latest_report(db,ROOT)
            reports=[report] if report else []
        latest_summary=None
        for report in reports:
            summary=[dict(asset=r['asset'],timeframe=r['timeframe'],status=r['status'],signals=len(r['signals']),note=r.get('reason',r.get('note',''))) for r in report['results']]
            findings=[signal for row in report['results'] for signal in row['signals']]
            report_key=hashlib.sha256(canonical(report).encode()).hexdigest()
            for r in report['results']:
                for signal in r['signals']:
                    key=finding_id('backtest',signal);enqueue(db,'backtest',key,dict(id=key,signal=signal,followups=r.get('followups',{}).get(key) or stored(db,key)))
            enqueue(db,'backtest_run',report_key,dict(year=report.get('year'),start_at=report.get('start_at'),end_at=report.get('end_at'),generated_at=report.get('generated_at') or report.get('end_at',0)/1000,rules=report.get('rules'),sources=report.get('sources'),results=[{k:v for k,v in r.items() if k not in ('signals','followups')} for r in report['results']],summary=summary,finding_ids=[finding_id('backtest',signal) for signal in findings]))
            latest_summary=summary
        if latest_summary is not None:
            enqueue(db,'summary','backtest',latest_summary)
        enqueue(db,'heartbeat','current',dict(observed_at=time.time()))
    # Stream historical CSVs into the durable queue; import only changed files.
    from scanner import timestamp
    for path in (ROOT/'data/historical-2020').glob('*.csv'):
        signature=str(path.stat().st_mtime_ns)+':'+str(path.stat().st_size)
        with store.connect() as db:
            if meta(db,'import:'+path.name,'')==signature:continue
        asset,tf=path.stem.rsplit('-',1)
        batch=[]
        with path.open(newline='') as source:
            for r in csv.DictReader(source):
                start=timestamp(r['timestamp']);batch.append((f'backtest:{asset}:{tf}:{start}',dict(source='backtest',symbol=asset,timeframe=tf,start=start,end=timestamp(r['closed_at']),**{k:float(r[k]) for k in ('open','high','low','close')})))
                if len(batch)>=100:
                    with store.connect() as db:
                        for key,value in batch:enqueue(db,'candle',key,value)
                    batch=[]
        with store.connect() as db:
            for key,value in batch:enqueue(db,'candle',key,value)
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('import:'+path.name,signature))


def batch(store):
    with store.connect() as db:
        initialize(db)
        rows=db.execute("SELECT kind,key,event_id,payload FROM cloud_pending ORDER BY CASE WHEN kind='summary' AND (key IN ('scanner','selections') OR key LIKE 'catchup:%') THEN -1 ELSE CASE kind WHEN 'config' THEN 0 WHEN 'live' THEN 1 WHEN 'backtest' THEN 2 WHEN 'feedback' THEN 3 ELSE 4 END END,key LIMIT 100").fetchall()
        base=int(meta(db,'config_base'));cursor=int(meta(db,'feedback_cursor'));state_cursor=int(meta(db,'state_cursor'))
    out=[];size=128
    for kind,key,event,raw in rows:
        record=dict(kind=kind,key=key,event_id=event,payload=json.loads(raw));length=len(canonical(record).encode())
        if size+length>250000:
            if not out:raise ValueError('A sync record exceeds batch size; split the report before syncing')
            break
        out.append(record);size+=length
    return dict(records=out,config_base=base,cursor=cursor,state_cursor=state_cursor)


def apply(store,response):
    from scanner_control import request as catchup_request
    for command in response.get('commands',[]):
        catchup_request(store,command['id'])
    from managed_assets import validate_asset
    with store.connect() as db:
        initialize(db);db.execute('BEGIN IMMEDIATE')
        for event in response.get('ack',[]):db.execute('DELETE FROM cloud_pending WHERE event_id=?',(event,))
        cfg=response.get('config')
        pending=db.execute("SELECT 1 FROM cloud_pending WHERE kind='config'").fetchone()
        if cfg and pending and 'config' in response.get('conflicts',[]):
            db.execute('INSERT OR REPLACE INTO cloud_conflicts VALUES(?,?,?)',('config',canonical(cfg),time.time()))
        if cfg and not pending:
            for asset in cfg['config']['assets']:validate_asset(asset)
            local=db.execute('SELECT revision,payload FROM managed_config WHERE id=1').fetchone()
            if local and (local[0] != cfg['revision'] or json.loads(local[1])!=cfg['config']):
                db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1',(cfg['revision'],json.dumps(cfg['config'])))
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('config_base',str(cfg['revision'])))
            # Mark imported config as seen; application acknowledgement is a later change.
            value=dict(revision=cfg['revision'],applied_revision=cfg.get('applied_revision',0),config=cfg['config'])
            raw=canonical(value);event=hashlib.sha256(('config\0current\0'+raw).encode()).hexdigest()
            db.execute('INSERT OR REPLACE INTO cloud_seen VALUES(?,?,?)',('config','current',event))
        for record in response.get('finding_states',[]):
            value=record['payload'];key=value['finding_id']
            pending_state=db.execute("SELECT 1 FROM cloud_pending WHERE kind='finding_state' AND key=?",(key,)).fetchone()
            if pending_state:
                # Do not consume this change until the local edit is acknowledged/resolved.
                break
            db.execute('INSERT OR REPLACE INTO finding_state VALUES(?,?,?,?)',(key,value['status'],value['revision'],value['updated_at']))
            raw=canonical(value);event=hashlib.sha256(('finding_state\0'+key+'\0'+raw).encode()).hexdigest()
            db.execute('INSERT OR REPLACE INTO cloud_seen VALUES(?,?,?)',('finding_state',key,event))
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('state_cursor',str(record['change_sequence'])))
        for record in response.get('feedback',[]):
            fb=record['payload'];key=fb['finding_id'];current=db.execute('SELECT rating,comment,revision FROM finding_feedback WHERE finding_id=?',(key,)).fetchone()
            pending_fb=db.execute("SELECT 1 FROM cloud_pending WHERE kind='feedback' AND key=?",(key,)).fetchone()
            if pending_fb or (current and current[2]==fb['revision'] and (current[0],current[1])!=(fb['rating'],fb['comment'])):
                db.execute('INSERT OR IGNORE INTO cloud_conflicts VALUES(?,?,?)',(str(record['change_sequence']),canonical(fb),time.time()))
            elif not current or current[2]<fb['revision']:
                values=(key,fb['source'],canonical(fb['evidence']),fb['rating'],fb['comment'],fb['revision'],fb['updated_at'])
                db.execute('INSERT OR REPLACE INTO finding_feedback VALUES(?,?,?,?,?,?,?)',values)
                db.execute('INSERT OR IGNORE INTO feedback_history(finding_id,source,evidence,rating,comment,revision,updated_at) VALUES(?,?,?,?,?,?,?)',values)
                # Suppress an immediate outbound echo of the imported edit.
                for kind,k in [('feedback',key),('feedback_history',f"{key}:{fb['revision']}")]:
                    payload={field:fb[field] for field in ('finding_id','source','evidence','rating','comment','revision','updated_at')}
                    raw=canonical(payload);event=hashlib.sha256((kind+'\0'+k+'\0'+raw).encode()).hexdigest()
                    db.execute('INSERT OR REPLACE INTO cloud_seen VALUES(?,?,?)',(kind,k,event))
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('feedback_cursor',str(record['change_sequence'])))
        db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('last_sync',str(time.time())))
        db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('error','Conflicting edits require resolution' if response.get('conflicts') else ''))


def worker(store,stop):
    url=os.environ.get('CLOUD_SYNC_URL','');token=os.environ.get('CLOUD_SYNC_TOKEN','')
    if not url or not token:return
    if not url.startswith('https://'):raise ValueError('Cloud sync requires HTTPS')
    delay=5;snapshot_at=0
    while not stop.is_set():
        try:
            if time.monotonic()-snapshot_at>=30:snapshot(store);snapshot_at=time.monotonic()
            outgoing=batch(store)
            req=urllib.request.Request(url,data=canonical(outgoing).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as response:result=json.load(response)
            apply(store,result);delay=1 if outgoing['records'] else 30
        except Exception:
            with store.connect() as db:
                initialize(db);db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('error','Cloud sync failed; check credentials, connection and schema. Pending data retained.'))
            delay=min(300,max(5,delay*2))
        stop.wait(delay)
