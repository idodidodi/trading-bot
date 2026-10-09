"""Disk-backed catch-up requests and summary notifications, local or hosted."""
import json
import time
import uuid
import sqlite3


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS scanner_commands(id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS notification_queue(id TEXT PRIMARY KEY,text TEXT NOT NULL,status TEXT NOT NULL DEFAULT \'pending\',attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0)')


def request(store, key=None):
    with store.connect() as db:
        initialize(db)
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute("SELECT id,status,payload FROM scanner_commands WHERE status IN ('pending','running') ORDER BY created LIMIT 1").fetchone()
        if existing and key is None:
            return dict(id=existing[0],status=existing[1],**json.loads(existing[2]))
        key=key or str(uuid.uuid4())
        uuid.UUID(key)
        db.execute('INSERT OR IGNORE INTO scanner_commands VALUES(?,?,?,?)',(key,'pending','{}',time.time()))
        row=db.execute('SELECT status,payload FROM scanner_commands WHERE id=?',(key,)).fetchone()
    return dict(id=key,status=row[0],**json.loads(row[1]))


def status(store):
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT id,status,payload FROM scanner_commands ORDER BY created DESC LIMIT 1').fetchone()
    return dict(id=row[0],status=row[1],**json.loads(row[2])) if row else dict(status='idle')


def claim(store):
    try:
        with store.connect() as db:
            initialize(db)
            # Idle polling needs no writer lock; cloud sync may be writing.
            if not db.execute("SELECT 1 FROM scanner_commands WHERE status='pending' LIMIT 1").fetchone():return None
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT id FROM scanner_commands WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
            if row:db.execute("UPDATE scanner_commands SET status='running' WHERE id=?",row)
        return row[0] if row else None
    except sqlite3.OperationalError as exc:
        if 'locked' not in str(exc).lower():raise
        return None  # The durable request remains pending for the next poll.


def finish(store,key,coverage,new_findings):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS catchup_findings(command_id TEXT,signal_id TEXT,PRIMARY KEY(command_id,signal_id))')
        saved=db.execute('SELECT COUNT(*) FROM catchup_findings WHERE command_id=?',(key,)).fetchone()[0]
    new_findings=max(saved,new_findings)
    failures=[r for r in coverage if r['status'] in ('unavailable','insufficient history')]
    payload=dict(findings=new_findings,checks=sum(not r.get('check_state') for r in coverage),
                 unavailable=len(failures),completed_at=time.time(),
                 reason='Some histories are unavailable; see coverage and Logs.' if failures else '')
    text=(f"Catch-up completed\n{new_findings} new divergence findings saved.\n"
          f"{payload['checks']} asset/timeframe checks; {len(failures)} unavailable or insufficient histories.\n"
          'Open Signals and coverage for details. Catch-up is limited to available provider history.')
    with store.connect() as db:
        initialize(db)
        db.execute('UPDATE scanner_commands SET status=?,payload=? WHERE id=?',('completed',json.dumps(payload),key))
        db.execute('INSERT OR IGNORE INTO notification_queue(id,text) VALUES(?,?)',(key,text))
    store.log('Catch-up completed',json.dumps(payload))
