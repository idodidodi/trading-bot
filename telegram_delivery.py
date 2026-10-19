"""Shared automatic-delivery claims; manual tests only use the source footer."""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid

from runtime_env import data_directory, environment


class DeliveryPending(RuntimeError):
    """Safe, retryable delivery classification."""


def source(directory=None):
    if directory is None:
        from platform_app import ROOT
        directory = data_directory(ROOT)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / 'telegram-sender.sqlite3', timeout=5)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS sender(singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO sender VALUES(1,?)', (str(uuid.uuid4()),))
        sender = db.execute('SELECT id FROM sender WHERE singleton=1').fetchone()[0]
    label = os.environ.get('TELEGRAM_DEVICE_LABEL') or socket.gethostname()
    label = ' '.join(label.split())[:80] or 'device'
    return dict(sender_id=sender, device=label, environment=environment())


def footer(text, identity):
    return (text + '\n\nSource: ' + identity['device'] + ' · ' + identity['environment']
            + ' · ' + identity['sender_id'][:8])


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def coordinator(payload):
    configured = os.environ.get('TELEGRAM_COORDINATION_URL', '')
    sync = os.environ.get('CLOUD_SYNC_URL', '')
    url = configured or (sync.rsplit('/', 1)[0] + '/telegram-delivery' if sync else '')
    token = os.environ.get('CLOUD_SYNC_TOKEN', '')
    if urllib.parse.urlsplit(url).scheme != 'https' or not token:
        raise DeliveryPending('Telegram coordination is not configured')
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        if result.get('status') not in ('acquired', 'busy', 'already_sent', 'completed', 'released'):
            raise ValueError('Invalid coordination result')
        return result
    except Exception:
        # Remote exception bodies and URLs may contain secrets.
        raise DeliveryPending('Telegram coordination unavailable') from None


def deliver(store, token, chat, text, event_id, send, silent=False):
    """Return sent/suppressed, retaining accepted sends until cloud completion.

    A local lease prevents two workers sharing SQLite from reusing a cloud token.
    Network calls are bounded to 10/15 seconds; renew immediately before sending.
    """
    identity = source(Path(store.path).parent)
    body_hash = digest(text)
    # Numeric Telegram bot ID is stable across token rotation; never store token.
    destination = digest(token.split(':', 1)[0] + '\0' + str(chat).strip())
    event_hash = digest(event_id or text)
    local_key = destination + ':' + event_hash
    lock = str(uuid.uuid4())
    now = time.time()
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS telegram_delivery(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, locked_until REAL NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 0, message_id INTEGER,
            outcome TEXT, attempts INTEGER NOT NULL DEFAULT 0)''')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT owner,locked_until,accepted,message_id,outcome FROM telegram_delivery WHERE id=?', (local_key,)).fetchone()
        if row and row[4]:
            return row[4]
        if row and row[1] > now:
            raise DeliveryPending('Telegram delivery already active locally')
        owner = row[0] if row and row[2] else lock
        db.execute('''INSERT INTO telegram_delivery(id,owner,locked_until,attempts) VALUES(?,?,?,1)
            ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,locked_until=excluded.locked_until,
            attempts=telegram_delivery.attempts+1''', (local_key, owner, now + 90))
    payload = dict(destination_hash=destination, event_hash=event_hash, body_hash=body_hash,
                   owner=owner, **identity)

    def call(action, **extra):
        return coordinator(dict(payload, action=action, **extra))['status']

    def finish(outcome):
        with store.connect() as db:
            db.execute('UPDATE telegram_delivery SET outcome=?,locked_until=0 WHERE id=? AND owner=?',
                       (outcome, local_key, owner))
        store.log('Telegram delivery ' + outcome,
                  f'{event_hash} · {identity["device"]} · {identity["environment"]} · {identity["sender_id"]}')
        return outcome

    try:
        if row and row[2]:
            if call('complete', message_id=row[3]) not in ('completed', 'already_sent'):
                raise DeliveryPending('Telegram completion ownership lost; review required')
            return finish('sent')
        status = call('acquire')
        if status == 'already_sent':
            return finish('suppressed')
        if status != 'acquired':
            raise DeliveryPending('Telegram delivery claimed by another sender')
        renewal = coordinator(dict(payload, action='renew', silent=silent))
        if renewal['status'] != 'acquired':
            raise DeliveryPending('Telegram delivery lease lost')
        # Older coordinators cannot allocate sound slots; default to silent
        # until their migration is applied. Explicit quiet sends stay quiet.
        silent = silent or renewal.get('silent', True) is not False
        try:
            message_id = send(token, chat, text, silent=silent, source_identity=identity)
        except Exception:
            # A timeout is ambiguous. The selected at-least-once policy retries.
            try:
                call('release')
            except DeliveryPending:
                pass
            raise
        with store.connect() as db:
            db.execute('UPDATE telegram_delivery SET accepted=1,message_id=? WHERE id=? AND owner=?',
                       (message_id, local_key, owner))
        if call('complete', message_id=message_id) not in ('completed', 'already_sent'):
            raise DeliveryPending('Telegram completion ownership lost; review required')
        return finish('sent')
    finally:
        with store.connect() as db:
            db.execute('UPDATE telegram_delivery SET locked_until=0 WHERE id=? AND owner=?', (local_key, owner))
