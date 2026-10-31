"""Per-user delivery decisions and persistent dismissal state for personal groups."""
from functools import partial
import json
from telegram_routing import market_group


def recipients(store):
    with store.connect() as db:
        if not db.execute("SELECT to_regprocedure('bot_runtime.personal_recipients()')").fetchone()[0]:
            return []
        return db.execute('SELECT bot_runtime.personal_recipients()').fetchone()[0]


def route_for(market, strategy):
    if not market or not strategy:
        return 'general'
    strategy = 'pin-bars' if strategy in ('pin-bars', 'bb-pin-bars') else strategy
    return f'{market_group(market)}.{strategy}'


def deliver_personal(store, token, text, event, send, silent=False, payload=None):
    from telegram_delivery import deliver, DeliveryPending
    from postgres_store import WorkerOwned, RETRYABLE
    people = recipients(store)
    if not people:
        return
    with store.connect() as db:
        market, strategy = db.execute('SELECT market,strategy FROM telegram_event_routes WHERE event=%s', (event,)).fetchone()
    route = route_for(market, strategy)
    failed = False
    for person in people:
        chat = person['routes'].get(route)
        if not chat:
            continue  # No fallback to somebody else's General group.
        allowed = person['enabled'].get(route, True)
        # Exclude findings predating enrollment, including a backlog after restart.
        observed = (payload or {}).get('detected_at') or (payload or {}).get('confirmed_at')
        if observed and observed / 1000 < float(person['created_at']):
            allowed = False
        with store.connect() as db:
            db.execute('INSERT INTO telegram_personal_events VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                       (person['user_id'], event, chat, route, allowed))
            saved_chat, eligible = db.execute('SELECT chat,enabled FROM telegram_personal_events WHERE user_id=%s AND event=%s',
                                             (person['user_id'], event)).fetchone()
            if not allowed:
                # Once suppressed, this event stays suppressed after re-enabling.
                db.execute('UPDATE telegram_personal_events SET enabled=false WHERE user_id=%s AND event=%s', (person['user_id'], event))
        if not eligible or not allowed:
            continue
        try:
            deliver(store, token, saved_chat, text, event,
                    partial(send, dismissal_user_id=person['telegram_user_id']), silent=silent)
            from chasing_runtime import enabled as chasing_enabled, people as chasing_people, enroll
            if chasing_enabled() and strategy == 'rsi' and payload:
                for recipient in chasing_people(store):
                    if str(recipient['user_id']) == str(person['user_id']):
                        enroll(store,payload,recipient)
        except (WorkerOwned, *RETRYABLE):
            raise
        except RuntimeError:
            # Continue other recipients; queue retry uses per-destination dedup.
            failed = True
    if failed:
        raise DeliveryPending('A personal Telegram delivery needs retry')


def prepare_message(store, token, chat, text, entities, user_id):
    from telegram_dismissal import DismissibleMessage
    from telegram_checklist import bot_key
    item = DismissibleMessage(int(chat), 0, dict(text=text, entities=entities), user_id=int(user_id) if user_id else None)
    with store.connect() as db:
        db.execute('INSERT INTO telegram_personal_messages VALUES(%s,%s,%s)',
                   (item.key, bot_key(token), json.dumps(item.snapshot())))
    return item


def bind_message(store, item, message):
    item.chat_id = message['chat']['id']
    item.message_id = message['message_id']
    with store.connect() as db:
        db.execute('UPDATE telegram_personal_messages SET state=%s WHERE id=%s', (json.dumps(item.snapshot()), item.key))


def process(store, token, query=None):
    """Serialized by the existing single-bot listener; process due deletions too."""
    from telegram_dismissal import DismissibleMessage
    from telegram_checklist import api, bot_key
    from postgres_store import assert_worker
    import re
    match = re.fullmatch(r'dismiss:(?:done|undo):([a-f0-9]{32})', (query or {}).get('data', ''))
    answers = []
    def call(method, body):
        if method == 'answerCallbackQuery':
            answers.append(body)
            return
        return api(token, method, body)
    with store.connect() as db:
        if query is not None:
            if not match:
                return False
            rows = db.execute('SELECT id,state FROM telegram_personal_messages WHERE bot=%s AND id=%s FOR UPDATE',
                              (bot_key(token), match[1])).fetchall()
        else:
            rows = db.execute("SELECT id,state FROM telegram_personal_messages WHERE bot=%s AND state::jsonb->>'phase'='pending' FOR UPDATE",
                              (bot_key(token),)).fetchall()
        for key, state in rows:
            item = DismissibleMessage.restore(json.loads(state))
            assert_worker(store)
            if query is None:
                item.expire(call)
            else:
                item.handle(query, call)
            db.execute('UPDATE telegram_personal_messages SET state=%s WHERE id=%s', (json.dumps(item.snapshot()), key))
    # Persist the edit/deletion before acknowledging a possibly expired query.
    for answer in answers:
        try:
            api(token, 'answerCallbackQuery', answer)
        except RuntimeError:
            pass
    return bool(rows)


def next_deadline(store, token):
    from telegram_checklist import bot_key
    with store.connect() as db:
        row = db.execute("SELECT min((state::jsonb->>'delete_at')::double precision) FROM telegram_personal_messages WHERE bot=%s AND state::jsonb->>'phase'='pending'",
                         (bot_key(token),)).fetchone()
    return row[0] if row else None
