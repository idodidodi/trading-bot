"""Optional Telegram checklist; collapsing preserves a durable Undo action."""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid


def enabled():
    return os.environ.get('TELEGRAM_CHECKLIST_ENABLED', 'false').lower() == 'true'


def group_dismissal_enabled():
    return os.environ.get('TELEGRAM_GROUP_DISMISSAL_ENABLED', 'false').lower() == 'true'


def keyboard(key, done=False):
    return {'inline_keyboard': [[{'text': '↩ Undo' if done else '✓ Done',
                                 'callback_data': f'cl:{"undo" if done else "done"}:{key}'}]]}


def api(token, method, body):
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            result = json.load(exc)
        except Exception:
            result = {}
        if method == 'editMessageText' and 'message is not modified' in result.get('description', ''):
            return None
        if method == 'deleteMessage' and 'message to delete not found' in result.get('description', '').lower():
            return True
        raise RuntimeError(f'Telegram checklist HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('Telegram checklist connection failed') from None
    if not result.get('ok'):
        raise RuntimeError('Telegram checklist request rejected')
    return result.get('result')


def bot_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


def prepare(store, token, text, entities):
    key = uuid.uuid4().hex
    with store.connect() as db:
        db.execute('INSERT INTO telegram_checklist(id,bot,original) VALUES(%s,%s,%s)',
                   (key, bot_key(token), json.dumps(dict(text=text, entities=entities))))
    return key


def bind(store, key, message):
    with store.connect() as db:
        db.execute('UPDATE telegram_checklist SET chat=%s,message_id=%s WHERE id=%s',
                   (str(message['chat']['id']), message['message_id'], key))


def handle(store, token, query):
    from postgres_store import assert_worker
    match = re.fullmatch(r'cl:(done|undo):([a-f0-9]{32})', query.get('data', ''))
    if not match:
        return
    action, key = match.groups()
    message = query.get('message') or {}
    chat = str(message.get('chat', {}).get('id', ''))
    message_id = message.get('message_id')
    with store.connect() as db:
        row = db.execute('SELECT original,done FROM telegram_checklist WHERE id=%s AND bot=%s AND chat=%s AND message_id=%s FOR UPDATE',
                         (key, bot_key(token), chat, message_id)).fetchone()
        if not row:
            api(token, 'answerCallbackQuery', dict(callback_query_id=query['id'], text='This alert is unavailable.'))
            return
        done = action == 'done'
        original = json.loads(row[0])
        body = dict(text='✓ Completed', entities=[]) if done else original
        assert_worker(store)
        api(token, 'editMessageText', dict(chat_id=chat, message_id=message_id,
            **body, reply_markup=keyboard(key, done)))
        db.execute('UPDATE telegram_checklist SET done=%s WHERE id=%s', (done, key))
    api(token, 'answerCallbackQuery', dict(callback_query_id=query['id'], text='Completed' if done else 'Restored'))


def worker(store, token, dry_run, stop):
    from postgres_store import WorkerOwned
    delay = 1
    while not stop.is_set():
        try:
            return _worker(store, token, dry_run, stop)
        except WorkerOwned:
            raise
        except RuntimeError as exc:
            # API errors are sanitized in api(); never print credential URLs.
            print(f'Telegram checklist: {exc}; retry in {delay}s', flush=True)
            if stop.wait(delay):
                return
            delay = min(30, delay * 2)


def _worker(store, token, dry_run, stop):
    """Long-poll promptly; keep offsets/deadlines in memory between updates."""
    from local_settings import delivery_dry_run
    from postgres_store import assert_worker
    from telegram_personal import recipients, next_deadline, process
    if not token or (not enabled() and not group_dismissal_enabled() and not recipients(store)):
        return
    bot = bot_key(token)
    # Reuse WorkerGuard; no additional database connection is needed.
    assert_worker(store)
    with store.connect() as db:
        row = db.execute('SELECT next_update FROM telegram_checklist_offsets WHERE bot=%s', (bot,)).fetchone()
        modern = bool(db.execute("SELECT to_regclass('bot_runtime.telegram_personal_messages')").fetchone()[0])
    offset = row[0] if row else 0
    due = next_deadline(store, token) if modern else None
    while not stop.is_set():
        if delivery_dry_run(store, dry_run):
            stop.wait(2)
            continue
        # Keep the worker/effects checks before polling/acknowledging updates.
        assert_worker(store)
        if due is not None and time.time() >= due:
            process(store, token)
            due = next_deadline(store, token)
        updates = api(token, 'getUpdates', dict(offset=offset, timeout=0 if due is not None else 10,
                                               allowed_updates=['callback_query']))
        for update in updates:
            query = update.get('callback_query')
            if query:
                if query.get('data', '').startswith('chase:enter:'):
                    from chasing_runtime import enabled as chasing_enabled, callback
                    if chasing_enabled():callback(store,token,query)
                elif query.get('data', '').startswith('dismiss:'):
                    if modern:
                        process(store, token, query)
                        due = next_deadline(store, token)
                else:
                    handle(store, token, query)
        if updates:
            offset = updates[-1]['update_id'] + 1
            with store.connect() as db:
                db.execute('INSERT INTO telegram_checklist_offsets VALUES(%s,%s) ON CONFLICT(bot) DO UPDATE SET next_update=excluded.next_update',
                           (bot, offset))
        elif due is not None:
            stop.wait(.05)
