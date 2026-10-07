"""Workspace visibility, kept separately from scanner delivery/deduplication records."""
import json
import time
from finding_feedback import finding_id


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS finding_state (
        finding_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('active','archived','deleted')),
        revision INTEGER NOT NULL, updated_at REAL NOT NULL)''')


def change(store, root, body):
    key, action, revision = body.get('finding_id'), body.get('action'), body.get('revision')
    if (body.get('source') != 'live' or not isinstance(key, str) or len(key) != 64
            or action not in ('archive', 'restore', 'delete') or type(revision) is not int or revision < 0):
        raise ValueError('Invalid signal action')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not any(finding_id('live', json.loads(r[0])) == key for r in db.execute('SELECT payload FROM signals')):
            raise LookupError('Signal not found. Reload before saving.')
        current = db.execute('SELECT status,revision FROM finding_state WHERE finding_id=?', (key,)).fetchone()
        if revision != (current[1] if current else 0):
            raise LookupError('Signal changed in another session. Reload before saving.')
        if current and current[0] == 'deleted':
            raise LookupError('Signal has been deleted.')
        status = dict(archive='archived', restore='active', delete='deleted')[action]
        value = dict(finding_id=key, status=status, revision=revision+1, updated_at=time.time())
        db.execute('INSERT OR REPLACE INTO finding_state VALUES(?,?,?,?)',
                   (key, status, revision+1, value['updated_at']))
        from cloud_sync import enqueue
        enqueue(db, 'finding_state', key, value)
    return dict(saved=True, **value)
