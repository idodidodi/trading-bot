"""Disk-backed catch-up requests and summary notifications, local or hosted."""
import json
import time
import uuid
from postgres_store import DatabaseError, OperationalError


def initialize(db):
    db.require_table('scanner_commands')
    db.require_table('notification_queue')


def request(store, key=None):
    with store.connect() as db:
        initialize(db)
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        existing=db.execute("SELECT id,status,payload FROM scanner_commands WHERE status IN ('pending','running') ORDER BY created LIMIT 1").fetchone()
        if existing and key is None:
            return dict(id=existing[0],status=existing[1],**json.loads(existing[2]))
        key=key or str(uuid.uuid4())
        uuid.UUID(key)
        db.execute('INSERT INTO scanner_commands VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(key,'pending','{}',time.time()))
        row=db.execute('SELECT status,payload FROM scanner_commands WHERE id=%s',(key,)).fetchone()
    return dict(id=key,status=row[0],**json.loads(row[1]))


def status(store):
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT id,status,payload FROM scanner_commands ORDER BY created DESC LIMIT 1').fetchone()
    return dict(id=row[0],status=row[1],**json.loads(row[2])) if row else dict(status='idle')


def claim(store):
    with store.connect() as db:
        row = db.execute("""WITH picked AS (
            SELECT id FROM scanner_commands c WHERE status='pending'
            AND NOT EXISTS(SELECT 1 FROM migration_holds h WHERE h.queue='scanner_commands' AND h.key=c.id)
            ORDER BY created FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE scanner_commands c SET status='running' FROM picked p WHERE c.id=p.id RETURNING c.id""").fetchone()
    return row[0] if row else None


def finish(store,key,coverage,new_findings):
    with store.connect() as db:
        db.require_table('catchup_findings')
        saved=db.execute('SELECT COUNT(*) FROM catchup_findings WHERE command_id=%s',(key,)).fetchone()[0]
    new_findings=max(saved,new_findings)
    failures=[r for r in coverage if r['status'] in ('unavailable','insufficient history')]
    pin_count=sum(r.get('pin_bars',{}).get('new_signals',0) for r in coverage if not r.get('check_state'))
    payload=dict(pin_bars=pin_count,findings=new_findings,checks=sum(not r.get('check_state') for r in coverage),
                 unavailable=len(failures),completed_at=time.time(),
                 reason='Some histories are unavailable; see coverage and Logs.' if failures else '')
    text=(f"Catch-up completed\n{new_findings} new divergence findings and {pin_count} Pin-Bar signals saved.\n"
          f"{payload['checks']} asset/timeframe checks; {len(failures)} unavailable or insufficient histories.\n"
          'Open Signals and coverage for details. Catch-up is limited to available provider history.')
    with store.connect() as db:
        initialize(db)
        db.execute('UPDATE scanner_commands SET status=%s,payload=%s WHERE id=%s',('completed',json.dumps(payload),key))
        db.execute('INSERT INTO notification_queue(id,text) VALUES(%s,%s) ON CONFLICT DO NOTHING',(key,text))
    store.log('Catch-up completed',json.dumps(payload))
