"""Import a stopped-writer export without modifying broker or signal identities."""
import argparse
import hashlib
import json
from pathlib import Path
from runtime_store import Store

TABLES={'paper_trades':('id','payload'),'paper_status':('id','payload'),
        'demo_execution_trades':('id','state','payload','updated_at'),
        'demo_execution_policy':('id','revision','enabled','activated_at','config','status'),
        'momentum_reports':('id','created','payload'),'momentum_history':('market','symbol','payload'),'momentum_reviews':('id','payload')}


def import_snapshot(store,snapshot):
    if snapshot.get('format')!=1 or snapshot.get('writers_stopped') is not True:raise ValueError('Stopped-writer export required')
    if set(snapshot['tables'])!=set(TABLES):raise ValueError('Incomplete export')
    digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with store.connect() as db:
        if db.execute('SELECT 1 FROM migration_receipts WHERE digest=?',(digest,)).fetchone():return digest
        if db.execute('SELECT enabled FROM control WHERE id=1').fetchone()[0]:raise ValueError('Disable execution before import')
        for table in TABLES:
            if db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():raise ValueError('Import requires an empty execution journal')
        for table,columns in TABLES.items():
            for row in snapshot['tables'][table]:
                if len(row)!=len(columns):raise ValueError('Invalid export row')
                row=[json.dumps(v) if isinstance(v,(dict,list)) else v for v in row]
                if table=='demo_execution_policy':row[2]=0
                db.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for c in columns)+')',row)
        db.execute('UPDATE control SET enabled=0,reconciled=0 WHERE id=1')
        manifest=dict(counts={k:len(v) for k,v in snapshot['tables'].items()},source=snapshot.get('source'),signal_references='Original embedded evidence and source_id retained; no broker ID is regenerated')
        db.execute('INSERT INTO migration_receipts VALUES(?,?)',(digest,json.dumps(manifest)))
    return digest


def verify_snapshot(store,snapshot):
    """Exact preserved rows, before enabling execution or writing new data."""
    def canonical_rows(rows,columns):
        result=[]
        for row in rows:
            values=[]
            for name,value in zip(columns,row):
                if name in ('payload','config','status') and isinstance(value,str):value=json.loads(value)
                if name=='enabled':value=0
                values.append(value)
            result.append(json.dumps(values,sort_keys=True,separators=(',',':')))
        return sorted(result)
    with store.connect() as db:
        for table,columns in TABLES.items():
            actual=db.execute('SELECT '+','.join(columns)+' FROM '+table).fetchall()
            if canonical_rows(actual,columns)!=canonical_rows(snapshot['tables'][table],columns):
                raise ValueError('Migration mismatch: '+table)
    return {table:len(rows) for table,rows in snapshot['tables'].items()}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--snapshot',required=True,type=Path);p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    store=Store(a.database);store.acquire_worker()
    try:
        snapshot=json.loads(a.snapshot.read_text())
        digest=None if a.verify_only else import_snapshot(store,snapshot)
        print(json.dumps(dict(digest=digest,verified_counts=verify_snapshot(store,snapshot),execution_enabled=bool(store.state()[0]))))
    finally:store.close()
