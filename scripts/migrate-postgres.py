#!/usr/bin/env python3
"""Explicit offline snapshot, conflict plan, import, validation and rollback export."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from migration.legacy import snapshot,prepare,apply,verify


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation',choices=['snapshot','plan','import','verify','rollback-export'])
    p.add_argument('--source',type=Path,required=True,help='Legacy SQLite for snapshot; consistent snapshot for all other operations')
    p.add_argument('--output',type=Path,required=True,help='New snapshot file, JSON report, or new rollback-export directory')
    p.add_argument('--installation')
    p.add_argument('--tiingo',type=Path,action='append',help='Consistent snapshot of tiingo-cache.sqlite3')
    p.add_argument('--sender',type=Path,help='Snapshot of telegram-sender.sqlite3')
    p.add_argument('--history-dir',type=Path,action='append',default=[],help='Import ALL historical CSV rows from this directory; repeat for additional years')
    p.add_argument('--resolutions',type=Path,help='JSON mapping conflict keys to local or hosted')
    p.add_argument('--writers-stopped',action='store_true')
    p.add_argument('--broker-reconciled',action='store_true')
    a=p.parse_args()
    if a.operation=='snapshot':
        print('Snapshot SHA256:',snapshot(a.source,a.output));return
    if not a.installation:p.error('--installation is required')
    if a.operation in ('import','rollback-export') and not a.writers_stopped:p.error('Stop ALL old/new writers and confirm --writers-stopped')
    if a.operation=='rollback-export' and not a.broker_reconciled:p.error('Reconcile broker and ambiguous Telegram history first; confirm --broker-reconciled')
    if a.output.exists():raise ValueError('Output must be a new path; do not overwrite migration reports')
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ.get('TRADING_MIGRATION_URL','')
    options=conninfo_to_dict(dsn)
    if not dsn:raise ValueError('TRADING_MIGRATION_URL must explicitly select the admin destination')
    if options.get('host') not in ('127.0.0.1','localhost','/private/tmp','/tmp') and options.get('sslmode') not in ('verify-full','verify-ca'):raise ValueError('Migration connection requires verified TLS')
    with psycopg.connect(dsn,options='-c search_path=bot_runtime,pg_catalog -c extra_float_digits=3',prepare_threshold=None) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        db.execute('SET LOCAL extra_float_digits=3')
        if a.operation=='rollback-export':
            from migration.recovery import export
            report=export(db,a.source,a.output,a.installation)
        else:
            plan=prepare(db,a.source,a.installation,json.loads(a.resolutions.read_text()) if a.resolutions else {},a.tiingo,a.sender,a.history_dir)
            if a.operation=='plan':report={k:v for k,v in plan.items() if k!='tables'}|{'counts':{k:len(v) for k,v in plan['tables'].items()}}
            elif a.operation=='import':report=apply(db,plan,a.installation)
            else:report=verify(db,plan)
    # Leave the connection context (and confirm COMMIT) before reporting success.
    if a.operation=='rollback-export':
        print('Recovery export prepared. Review manifest and operational runbook before any restart.')
        return
    a.output.write_text(json.dumps(report,indent=2,default=str)+'\n');a.output.chmod(0o600)
    if report.get('conflicts') or report.get('mismatches'):
        print('Review required; report contains conflicts or validation mismatches.')
        sys.exit(2)
    print('Completed; review the saved report. No workers or external effects were started.')



if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Migration failed ('+type(exc).__name__+'). No worker was started. A lost COMMIT reply can be ambiguous; inspect the receipt by rerunning verify/import with the same snapshots.',file=sys.stderr)
        sys.exit(1)
