"""Restartable, conflict-explicit SQLite -> Postgres import, including hosted edits.

Only this offline tool may import sqlite3
from collections import Counter,defaultdict. All destination changes are one manual
transaction; no provider calls, worker starts, schema creation or deployment.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
from collections import Counter,defaultdict
from contextlib import closing
from psycopg import sql
from psycopg.types.json import Jsonb

# Retired alert-review completion slots have no runtime consumer. Preserve every
# row in migration_audit alongside retired mirror state rather than replaying it.
IGNORED = {'cloud_pending','cloud_seen','cloud_meta','cloud_conflicts','sqlite_sequence','alert_reviews'}
# Operational records remain byte-for-byte unless a named conflict is resolved.
IDENTITIES = {'activity_log':'id','backtest_runs':'id','momentum_reports':'id',
              'momentum_reviews':'id','feedback_history':'sequence'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or destination.exists():
        raise ValueError('Snapshot requires an existing source and a new destination')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(destination)) as dest:
        src.backup(dest)
        if dest.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('SQLite integrity check failed')
    destination.chmod(0o600)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def read_snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Snapshot is corrupt')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={}
        for table in sorted(tables):
            if table.startswith('sqlite_'):continue
            columns=[r[1] for r in db.execute('PRAGMA table_info("'+table.replace('"','""')+'")')]
            result[table]=[dict(zip(columns,r)) for r in db.execute('SELECT * FROM "'+table.replace('"','""')+'"')]
        return result


def _target(db):
    names=[r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='bot_runtime' AND table_type='BASE TABLE'")]
    return {name:[r[0] for r in db.execute('SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY array_position(i.indkey,a.attnum)',('bot_runtime.'+name,))] for name in names}


def hosted_rows(db, installation):
    """Capture complete hosted edits + legacy evidence; no payload/window caps."""
    if not db.execute("SELECT to_regclass('public.dashboard_records')").fetchone()[0]:return {}
    rows = db.execute('SELECT kind,key,payload FROM public.dashboard_records WHERE installation=%s',(installation,)).fetchall()
    result={}
    for kind,key,value in rows:
        result.setdefault(kind,{})[key]=value
    return result


def prepare(db, path, installation, resolutions=None, tiingo=None, sender=None, history_dirs=None):
    resolutions=resolutions or {}
    local=read_snapshot(path)
    digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    tiingo_paths=list(tiingo) if isinstance(tiingo,(tuple,list)) else ([tiingo] if tiingo else [])
    for extra in tiingo_paths+([sender] if sender else []):
        if extra:digest=hashlib.sha256((digest+hashlib.sha256(Path(extra).read_bytes()).hexdigest()).encode()).hexdigest()
    targets=_target(db); conflicts=[];audit=[]
    history_dirs=history_dirs or []
    for directory in history_dirs:
        for file in sorted(Path(directory).glob('*.csv')):
            digest=hashlib.sha256((digest+hashlib.sha256(file.read_bytes()).hexdigest()).encode()).hexdigest()
    unknown=set(local)-set(targets)-IGNORED
    if unknown:raise ValueError('Unmapped legacy tables: '+', '.join(sorted(unknown)))
    # Retain retired mirror queues/conflicts as migration evidence, never replay them.
    for name in IGNORED & set(local):
        audit.append((name,'legacy-archive',local[name],None,'archive'))
    tables={name:rows for name,rows in local.items() if name in targets}
    hosted=hosted_rows(db,installation)
    def resolve(table,key,left,right):
        token=table+':'+str(key)
        if left == right:return left
        decision=resolutions.get(token)
        if left is None:decision='hosted'
        if decision not in ('local','hosted'):
            conflicts.append(dict(key=token,local=left,hosted=right))
            return left
        audit.append((table,str(key),left,right,decision))
        return right if decision=='hosted' else left
    if 'current' in hosted.get('config',{}):
        r=hosted['config']['current'];remote=dict(id=1,revision=r['revision'],applied_revision=r.get('applied_revision',0),payload=json.dumps(r['config']))
        old=next(iter(tables.get('managed_config',[])),None)
        # Parse payloads for semantic equality, ignoring JSON whitespace.
        if old and old['revision']==remote['revision'] and json.loads(old['payload'])==r['config']:
            remote=old
        selected=resolve('managed_config','1',old,remote)
        tables['managed_config']=[selected] if selected else []
    if tables.get('managed_config'):
        cfg=tables['managed_config'][0]
        rows={r['revision']:r for r in tables.get('config_history',[])}
        old=rows.get(cfg['revision'])
        if old and json.loads(old['payload'])!=json.loads(cfg['payload']):
            audit.append(('config_history',str(cfg['revision']),old,cfg,'selected-current-config; original retained here'))
        rows[cfg['revision']]=dict(revision=cfg['revision'],payload=cfg['payload'],changed=old['changed'] if old else 0)
        tables['config_history']=list(rows.values())
    for kind,table,fields in [('finding_state','finding_state',('finding_id','status','revision','updated_at')),
        ('feedback','finding_feedback',('finding_id','source','evidence','rating','comment','revision','updated_at'))]:
        rows={r['finding_id']:r for r in tables.get(table,[])}
        for key,r in hosted.get(kind,{}).items():
            remote={k:r.get(k) for k in fields}
            remote['finding_id']=key
            if 'evidence' in remote:remote['evidence']=canonical(remote['evidence'])
            old=rows.get(key)
            if old and 'evidence' in old:old=old|{'evidence':canonical(json.loads(old['evidence']))}
            if old and all(old[k]==remote[k] for k in fields if k!='updated_at'):remote=old
            rows[key]=resolve(table,key,old,remote)
        tables[table]=list(rows.values())
    # Every hosted immutable feedback revision is preserved. Divergent same-revision
    # edits require explicit choice and BOTH originals stay in migration_audit.
    history={(r['finding_id'],r['revision']):r for r in tables.get('feedback_history',[])}
    for key,r in hosted.get('feedback_history',{}).items():
        value={k:r.get(k) for k in ('finding_id','source','evidence','rating','comment','revision','updated_at')}
        value['evidence']=canonical(value['evidence'])
        pair=(value['finding_id'],value['revision']);old=history.get(pair)
        if old:
            normalized={k:v for k,v in old.items() if k!='sequence'}
            normalized['evidence']=canonical(json.loads(normalized['evidence']))
            if all(normalized[k]==value[k] for k in value if k!='updated_at'):continue
            selected=resolve('feedback_history',key,normalized,value)
            if selected is normalized:continue
            # Retain local original in audit and selected hosted canonical revision.
            value=selected
            if 'sequence' in old:value=value|{'sequence':old['sequence']}
        history[pair]=value
    # Current feedback must also have its final immutable history record.
    for r in tables.get('finding_feedback',[]):
        pair=(r['finding_id'],r['revision']);existing=history.get(pair)
        value={k:v for k,v in r.items() if k!='sequence'}
        if existing and any(existing.get(k)!=value[k] for k in ('comment','rating')):
            selected=resolve('feedback-final',r['finding_id'],existing,value)
            history[pair]=selected
            if selected is existing:
                r.update({k:v for k,v in existing.items() if k!='sequence'})
        else:history.setdefault(pair,value)
    tables['feedback_history']=list(history.values())
    # Hosted-only evidence is a frozen legacy archive, queried directly by the view.
    tables['legacy_findings']=[dict(source=kind,id=key,payload=json.dumps(value)) for kind in ('live','backtest') for key,value in hosted.get(kind,{}).items()]
    # Imported summaries are archival artifacts, never a runtime snapshot pipeline.
    tables['legacy_artifacts']=[dict(kind=kind,key=key,payload=Jsonb(value)) for kind,records in hosted.items() if kind not in ('config','feedback','feedback_history','finding_state','live','backtest') for key,value in records.items()]
    # These hosted tables are outside the old mirror record stream. Keep their
    # complete evidence, including replay jobs and ambiguous delivery attempts.
    for name in ('dashboard_followups','dashboard_backtest_jobs','telegram_delivery_attempts',
                 'telegram_delivery_claims','telegram_sound_slots','telegram_routes'):
        if db.execute('SELECT to_regclass(%s)',('public.'+name,)).fetchone()[0]:
            for value, in db.execute(sql.SQL('SELECT to_jsonb(t) FROM public.{} t WHERE installation=%s').format(sql.Identifier(name)),(installation,)):
                tables['legacy_artifacts'].append(dict(kind='hosted_table:'+name,key=hashlib.sha256(canonical(value).encode()).hexdigest(),payload=Jsonb(value)))
    if db.execute("SELECT to_regclass('public.dashboard_followups')").fetchone()[0]:
        followups={r['finding_id']:r for r in tables.get('finding_followup',[])}
        for key,results,calculated in db.execute('SELECT finding_id,results,extract(epoch FROM calculated_at) FROM public.dashboard_followups WHERE installation=%s',(installation,)):
            remote=dict(finding_id=key,results=canonical(results),calculated_at=float(calculated))
            old=followups.get(key)
            if old and json.loads(old['results'])==results:continue
            followups[key]=resolve('finding_followup',key,old,remote)
        tables['finding_followup']=list(followups.values())
    # Full hosted candle series, including historical CSVs absent from local cache.
    candle_index={table:{(r['feed'],r['timeframe'],r['start']):r for r in tables.get(table,[])}
                  for table in ('candle_cache','backtest_candle_cache')}
    for key,value in hosted.get('candle',{}).items():
        table='candle_cache' if value['source']=='live' else 'backtest_candle_cache'
        row=dict(feed=value['symbol'],timeframe=value['timeframe'],**{k:value[k] for k in ('start','end','open','high','low','close')})
        if table=='candle_cache':row['asset']=value.get('asset',value['symbol'])
        pk=(row['feed'],row['timeframe'],row['start'])
        existing=candle_index[table].get(pk)
        if existing:
            if any(existing[k]!=row[k] for k in ('end','open','high','low','close')):
                selected=resolve(table,canonical(pk),existing,row)
                if selected is row:existing.update(row)
        else:
            tables.setdefault(table,[]).append(row)
            candle_index[table][pk]=row
    # Historical CSV files were never fully represented in operational SQLite.
    # Import every row explicitly, with no 500-finding/payload mirror cap.
    if history_dirs:
        from scanner import read_csv
        cache={ (r['feed'],r['timeframe'],r['start']):r for r in tables.get('backtest_candle_cache',[]) }
        for directory in history_dirs:
            directory=Path(directory)
            for file in sorted(directory.glob('*.csv')):
                asset,frame=file.stem.rsplit('-',1)
                if frame not in ('monthly','weekly','daily','4h','1h'):raise ValueError('Unsupported historical CSV filename')
                bars=read_csv(dict(id=asset,path=str(file.resolve())),frame)
                for c in bars:
                    row=dict(feed=asset,timeframe=frame,start=c.start,end=c.end,open=c.open,high=c.high,low=c.low,close=c.close)
                    pair=(asset,frame,c.start);old=cache.get(pair)
                    if old and old!=row:
                        # Distinguish files contesting the same candle so each
                        # decision and both originals get their own audit row.
                        key=canonical(pair)+':csv:'+directory.name+'/'+file.name
                        cache[pair]=resolve('backtest_candle_cache',key,row,old)
                    else:cache[pair]=row
            manifest=directory/'manifest.json'
            if manifest.is_file():tables['legacy_artifacts'].append(dict(kind='source_manifest',key=hashlib.sha256(manifest.read_bytes()).hexdigest(),payload=Jsonb(json.loads(manifest.read_text()))))
        tables['backtest_candle_cache']=list(cache.values())
    if tiingo_paths:
        requests=[];responses={}
        for cache_path in tiingo_paths:
            extra=read_snapshot(cache_path)
            requests.extend(extra.get('requests',[]))
            for row in extra.get('responses',[]):
                if row['key'] not in responses or row['fetched']>responses[row['key']]['fetched']:
                    responses[row['key']]=row
        tables['tiingo_requests']=requests
        tables['tiingo_responses']=list(responses.values())
    if sender:
        extra=read_snapshot(sender)
        tables['telegram_senders']=[dict(device='legacy-import',id=r['id']) for r in extra.get('sender',[])]
    # Preserve hosted CSV reports and pending catch-up requests.
    if db.execute("SELECT to_regclass('public.dashboard_csv_backtests')").fetchone()[0]:
        known={r['id']:r for r in tables.get('csv_backtest_runs',[])}
        for key,report,created in db.execute('SELECT id,report,extract(epoch from created)*1000 FROM public.dashboard_csv_backtests WHERE installation=%s',(installation,)):
            new=dict(id=key,created=int(created),report=json.dumps(report),csv=None)
            if key in known and json.loads(known[key]['report'])!=report:
                known[key]=resolve('csv_backtest_runs',key,known[key],new)
            else:known.setdefault(key,new)
        tables['csv_backtest_runs']=list(known.values())
    commands={r['id']:r for r in tables.get('scanner_commands',[])}
    for key,r in hosted.get('command',{}).items():
        summary=hosted.get('summary',{}).get('catchup:'+key,{})
        commands.setdefault(key,dict(id=key,status=summary.get('status','pending'),payload=json.dumps(summary),created=r['created']))
    tables['scanner_commands']=list(commands.values())
    if db.execute("SELECT to_regclass('public.telegram_delivery_claims')").fetchone()[0]:
        deliveries={r['id']:r for r in tables.get('telegram_delivery',[])}
        for dest,event,state,message_id in db.execute('SELECT destination_hash,event_hash,state,message_id FROM public.telegram_delivery_claims WHERE installation=%s',(installation,)):
            if state=='sent':deliveries[dest+':'+event]=dict(id=dest+':'+event,owner='legacy-hosted',locked_until=0,accepted=1,message_id=message_id,outcome='sent',attempts=1)
        tables['telegram_delivery']=list(deliveries.values())
    return dict(snapshot_hash=digest,tables=tables,conflicts=conflicts,audit=audit)


def apply(db, prepared, installation):
    if prepared['conflicts']:raise ValueError('Resolve every reported conflict before importing')
    digest=prepared['snapshot_hash']
    if db.execute('SELECT 1 FROM bot_runtime.migration_runs WHERE snapshot_hash=%s',(digest,)).fetchone():
        return verify(db,prepared)
    if db.execute('SELECT enabled FROM bot_runtime.runtime_control WHERE id=1').fetchone() not in (None,(False,)):
        raise ValueError('Runtime must be disabled before importing')
    # Prevent two importers or worker ownership during an import.
    if not db.execute('SELECT pg_try_advisory_xact_lock(714269002)').fetchone()[0]:raise ValueError('Stop the active worker first')
    db.execute('INSERT INTO bot_runtime.runtime_control VALUES(1,%s,false) ON CONFLICT(id) DO NOTHING',(installation,))
    if db.execute('SELECT installation::text FROM bot_runtime.runtime_control WHERE id=1').fetchone()[0]!=str(installation):raise ValueError('Installation mismatch')
    targets=_target(db)
    for table,rows in prepared['tables'].items():
        groups=defaultdict(list)
        for row in rows:groups[tuple(row)].append(tuple(row.values()))
        identity=IDENTITIES.get(table)
        ordered=sorted(groups.items(),key=lambda item:identity not in item[0]) if identity else groups.items()
        for columns,values in ordered:
            if identity and identity not in columns:
                # Explicit legacy IDs must land before allocating hosted-only
                # IDs. Sequence increments survive transaction rollback.
                db.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,%s),GREATEST(COALESCE(max({}),0),1),count(*)>0) FROM bot_runtime.{}").format(sql.Identifier(identity),sql.Identifier(table)),('bot_runtime.'+table,identity))
            query=sql.SQL('INSERT INTO bot_runtime.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
            with db.cursor() as cursor:
                cursor.executemany(query,values)
    for table,key,left,right,decision in prepared['audit']:
        db.execute('INSERT INTO bot_runtime.migration_audit VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(digest,table,key,Jsonb(left),Jsonb(right),decision))
    # Build only identity indexes; evidence remains in operational records.
    from postgres_store import remember_finding
    for raw, in db.execute('SELECT payload FROM bot_runtime.signals'):
        remember_finding(db,'live',json.loads(raw))
    for raw, in db.execute('SELECT report FROM bot_runtime.backtest_runs'):
        for result in json.loads(raw)['results']:
            for signal in result['signals']:remember_finding(db,'backtest',signal)
    for table in ('signals','notification_queue','scanner_commands'):
        db.execute(sql.SQL("INSERT INTO bot_runtime.migration_holds SELECT %s,id,'legacy pending work; explicit review required' FROM bot_runtime.{} WHERE status IN ('pending','running') ON CONFLICT DO NOTHING").format(sql.Identifier(table)),(table,))
    for table,column in IDENTITIES.items():
        db.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s,%s),GREATEST(COALESCE((SELECT max({}) FROM bot_runtime.{}),0),1),COALESCE((SELECT max({}) FROM bot_runtime.{}),0)>0)").format(sql.Identifier(column),sql.Identifier(table),sql.Identifier(column),sql.Identifier(table)),('bot_runtime.'+table,column))
    result=verify(db,prepared)
    if result['mismatches']:raise ValueError('Destination differs from snapshot; import rolled back. Use a fresh rehearsal database or resolve destination conflicts.')
    db.execute('INSERT INTO bot_runtime.migration_runs(snapshot_hash,manifest) VALUES(%s,%s)',(digest,Jsonb(result)))
    return result


def verify(db,prepared):
    from decimal import Decimal
    def freeze(value):
        if isinstance(value,bool) or value is None:return (type(value).__name__,value)
        if isinstance(value,(int,float,Decimal)):return Decimal(str(value)).normalize()
        if isinstance(value,dict):return tuple(sorted((k,freeze(v)) for k,v in value.items()))
        if isinstance(value,(tuple,list)):return tuple(map(freeze,value))
        return value
    counts={};mismatches=[]
    for table,rows in prepared['tables'].items():
        counts[table]=dict(source=len(rows),destination=db.execute(sql.SQL('SELECT count(*) FROM bot_runtime.{}').format(sql.Identifier(table))).fetchone()[0])
        groups=defaultdict(list)
        for row in rows:
            columns=tuple(c for c in row if c!='sequence' or table!='feedback_history')
            values=[v.obj if isinstance(v,Jsonb) else v for c,v in row.items() if c in columns]
            groups[columns].append(values)
        for columns,expected in groups.items():
            saved=db.execute(sql.SQL('SELECT {} FROM bot_runtime.{}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table))).fetchall()
            difference=Counter(freeze(v) for v in expected)-Counter(freeze(v) for v in saved)
            if difference:mismatches.append(dict(table=table,different_rows=sum(difference.values())))
    return dict(snapshot_hash=prepared['snapshot_hash'],counts=counts,mismatches=mismatches)
