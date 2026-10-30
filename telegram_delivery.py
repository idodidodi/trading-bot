"""Postgres delivery reservations. Telegram remains at-least-once on ambiguous sends."""
import hashlib
import json
import os
import socket
import uuid
from runtime_env import environment


class DeliveryPending(RuntimeError):
    pass


def source(directory=None):
    from postgres_store import connect
    label = ' '.join((os.environ.get('TELEGRAM_DEVICE_LABEL') or socket.gethostname()).split())[:80] or 'device'
    configured = os.environ.get('TELEGRAM_SENDER_ID')
    if configured:
        sender = str(uuid.UUID(configured))
    else:
        with connect() as db:
            db.execute('INSERT INTO telegram_senders VALUES(%s,%s) ON CONFLICT DO NOTHING', (label,str(uuid.uuid4())))
            sender = db.execute('SELECT id FROM telegram_senders WHERE device=%s', (label,)).fetchone()[0]
    return dict(sender_id=sender, device=label, environment=environment())


def footer(text, identity):
    from alert_display import public_text
    return public_text(text)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def deliver(store, token, chat, text, event_id, send, silent=False):
    from postgres_store import assert_worker
    assert_worker(store)
    identity = source()
    destination = digest(token.split(':',1)[0] + '\0' + str(chat).strip())
    key = destination + ':' + digest(event_id or text)
    owner = str(uuid.uuid4())
    with store.connect() as db:
        # Row lock covers both first insertion and recovery after an expired lease.
        db.execute('INSERT INTO telegram_delivery(id,owner,locked_until) VALUES(%s,%s,0) ON CONFLICT DO NOTHING', (key,owner))
        accepted,message_id,outcome,locked_until = db.execute('SELECT accepted,message_id,outcome,locked_until FROM telegram_delivery WHERE id=%s FOR UPDATE', (key,)).fetchone()
        now = db.execute('SELECT extract(epoch FROM clock_timestamp())::float8').fetchone()[0]
        if outcome:
            return 'suppressed'
        if accepted:
            db.execute("UPDATE telegram_delivery SET outcome='sent',locked_until=0 WHERE id=%s", (key,))
            return 'sent'
        if locked_until > now:
            raise DeliveryPending('Telegram delivery already active')
        db.execute('UPDATE telegram_delivery SET owner=%s,locked_until=%s,attempts=attempts+1 WHERE id=%s', (owner,now+90,key))
        if not silent:
            slot = db.execute('''INSERT INTO telegram_sound_slots VALUES(%s,%s)
                ON CONFLICT(destination) DO UPDATE SET minute=excluded.minute
                WHERE telegram_sound_slots.minute<excluded.minute RETURNING minute''', (destination,int(now//60))).fetchone()
            silent = slot is None
    try:
        assert_worker(store)
        message_id = send(token,chat,text,silent=silent,source_identity=identity)
        # Do not send again when this COMMIT reply is lost: next cycle reads outcome.
        with store.connect() as db:
            updated = db.execute("UPDATE telegram_delivery SET accepted=1,message_id=%s,outcome='sent',locked_until=0 WHERE id=%s AND owner=%s RETURNING id", (message_id,key,owner)).fetchone()
            if not updated:
                raise DeliveryPending('Delivery ownership changed; review required')
            db.execute('INSERT INTO activity_log(occurred,event,details) VALUES(extract(epoch FROM clock_timestamp()),%s,%s)',
                ('telegram-delivered', json.dumps(dict(identity, message_id=message_id,
                    chat_id=str(chat), delivery_id=key, event_id=event_id, original_text=text))))
        return 'sent'
    except Exception:
        # A provider timeout/crash after acceptance may repeat a message. Retain
        # lease for 90s before retry; never call this exactly-once delivery.
        raise
