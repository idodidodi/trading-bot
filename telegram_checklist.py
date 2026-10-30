"""Optional Telegram checklist; collapsing preserves a durable Undo action."""
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid


def enabled():
    return os.environ.get('TELEGRAM_CHECKLIST_ENABLED', 'false').lower() == 'true'


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
    """One active polling consumer per bot; never discard pending updates."""
    from local_settings import delivery_dry_run
    from postgres_store import assert_worker
    if not enabled() or not token:
        return
    bot = bot_key(token)
    # A dedicated session holds the lock across polls, including between runtimes.
    from postgres_store import Connection, settings
    lock = int(bot[:15], 16)
    with Connection.connect(settings(), autocommit=True) as guard:
        if not guard.execute('SELECT pg_try_advisory_lock(%s)', (lock,)).fetchone()[0]:
            store.log('Telegram checklist inactive', 'Another listener owns this bot')
            return
        while not stop.is_set():
            if delivery_dry_run(store, dry_run):
                stop.wait(2)
                continue
            assert_worker(store)
            with store.connect() as db:
                row = db.execute('SELECT next_update FROM telegram_checklist_offsets WHERE bot=%s', (bot,)).fetchone()
            updates = api(token, 'getUpdates', dict(offset=row[0] if row else 0, timeout=10,
                                                   allowed_updates=['callback_query']))
            for update in updates:
                assert_worker(store)
                if update.get('callback_query'):
                    handle(store, token, update['callback_query'])
                with store.connect() as db:
                    db.execute('INSERT INTO telegram_checklist_offsets VALUES(%s,%s) ON CONFLICT(bot) DO UPDATE SET next_update=excluded.next_update',
                               (bot, update['update_id'] + 1))
            if not updates:
                stop.wait(.2)
