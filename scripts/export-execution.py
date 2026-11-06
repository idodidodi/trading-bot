"""Read-only, repeatable-read legacy execution export. Never connects by default."""
import argparse
import json
import os
from pathlib import Path
import psycopg

TABLES={'paper_trades':('id','payload'),'paper_status':('id','payload'),
        'demo_execution_trades':('id','state','payload','updated_at'),
        'demo_execution_policy':('id','revision','enabled','activated_at','config','status'),
        'momentum_reports':('id','created','payload'),'momentum_history':('market','symbol','payload'),'momentum_reviews':('id','payload')}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--writers-stopped',action='store_true');a=p.parse_args()
    if not a.writers_stopped:p.error('Stop and fence the old execution owner before exporting')
    dsn=os.environ.get('EXECUTION_EXPORT_DATABASE_URL')
    if not dsn:p.error('Explicit EXECUTION_EXPORT_DATABASE_URL required')
    with psycopg.connect(dsn) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        data={name:db.execute('SELECT '+','.join(cols)+' FROM bot_runtime.'+name+' ORDER BY 1').fetchall() for name,cols in TABLES.items()}
    snapshot=dict(format=1,writers_stopped=True,source='legacy bot_runtime',tables=data)
    # Exclusive creation: never overwrite an earlier cutover snapshot.
    fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(snapshot,f,sort_keys=True)
    print(json.dumps(dict(counts={k:len(v) for k,v in data.items()})))


if __name__=='__main__':main()
