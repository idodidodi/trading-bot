"""Export authoritative Postgres state into NEW legacy recovery files, never restart."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from psycopg import sql
from .legacy import snapshot,read_snapshot


def export(db,baseline,output,installation):
    output=Path(output)
    if output.exists():raise ValueError('Recovery directory must be new')
    if db.execute('SELECT installation::text,enabled FROM runtime_control WHERE id=1').fetchone()!=(str(installation),False):raise ValueError('Disable runtime and stop workers before recovery export')
    if not db.execute('SELECT pg_try_advisory_xact_lock(714269002)').fetchone()[0]:raise ValueError('Active worker still owns the database')
    output.mkdir(mode=0o700)
    target=output/'signals.sqlite3';snapshot(baseline,target)
    manifest={}
    with closing(sqlite3.connect(target)) as legacy,legacy:
        legacy.executescript((Path(__file__).with_name('legacy-schema.sql')).read_text())
        baseline_tables=read_snapshot(target)
        for table in baseline_tables:
            if table.startswith('cloud_'):
                legacy.execute('DELETE FROM "'+table+'"');continue
            if not db.execute('SELECT to_regclass(%s)',('bot_runtime.'+table,)).fetchone()[0]:continue
            columns=[r[1] for r in legacy.execute('PRAGMA table_info("'+table+'")')]
            rows=db.execute(sql.SQL('SELECT {} FROM bot_runtime.{}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table))).fetchall()
            if table=='telegram_routes':
                routing=db.execute('SELECT public.telegram_read_routes(%s)',(installation,)).fetchone()[0]
                rows=[(1,routing['revision'],json.dumps(routing['routes']))]

            legacy.execute('DELETE FROM "'+table+'"')
            legacy.executemany('INSERT INTO "'+table+'" ('+','.join('"'+c+'"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')',rows)
            manifest[table]=len(rows)
        # Old worker cannot honor new holds. Cancel held work in recovery COPY only.
        for queue,key in db.execute('SELECT queue,key FROM migration_holds'):
            if queue in ('signals','notification_queue'):legacy.execute('UPDATE "'+queue+'" SET status=\'cancelled\' WHERE id=?',(key,))
            if queue=='scanner_commands':legacy.execute("UPDATE scanner_commands SET status='failed' WHERE id=?",(key,))
        legacy.execute("INSERT OR REPLACE INTO local_runtime_settings VALUES('paper_trading_enabled',0)")
        if legacy.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise ValueError('Recovery SQLite integrity failed')
    with closing(sqlite3.connect(output/'tiingo-cache.sqlite3')) as cache,cache:
        cache.execute('CREATE TABLE requests(at REAL NOT NULL)');cache.execute('CREATE TABLE responses(key TEXT PRIMARY KEY,fetched REAL NOT NULL,payload TEXT NOT NULL)')
        cache.executemany('INSERT INTO requests VALUES(?)',db.execute('SELECT at FROM tiingo_requests').fetchall())
        cache.executemany('INSERT INTO responses VALUES(?,?,?)',db.execute('SELECT key,fetched,payload FROM tiingo_responses').fetchall())
    # Include all evidence that the old runtime cannot express, and external states
    # for operator comparison with Alpaca's order/position/account screens.
    extras={}
    for table in ('migration_audit','legacy_findings','legacy_artifacts','telegram_delivery','migration_holds','backtest_jobs'):
        extras[table]=[r[0] for r in db.execute(sql.SQL('SELECT to_jsonb(t) FROM bot_runtime.{} t').format(sql.Identifier(table)))]
    (output/'unmapped-history.json').write_text(json.dumps(extras,indent=2,default=str)+'\n')
    (output/'manifest.json').write_text(json.dumps({'counts':manifest,'requires_review':True,'broker_reconciliation':'Compare all paper_trades client IDs/orders/positions; do not retry submission unknown.', 'limitations':'Hosted-only legacy findings remain in unmapped-history.json and Postgres; old dashboard cannot display them. Never restart using the original stale SQLite. Clear old sync credentials and keep old updater disabled.'},indent=2)+'\n')
    for f in output.iterdir():f.chmod(0o600)
    return manifest
