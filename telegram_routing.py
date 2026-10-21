"""Installation routing cache and durable per-alert destinations. No bot secrets."""
import json
import os
import re
from threading import RLock
import urllib.request

_cache_lock = RLock()

ROUTES = ('general',) + tuple(f'{m}.{s}' for m in ('forex', 'crypto', 'stocks', 'other') for s in ('rsi', 'pin-bars'))


def validate(routes):
    if not isinstance(routes, dict) or set(routes) - set(ROUTES):
        raise ValueError('Invalid Telegram routes')
    if any(not isinstance(v, str) or (v and not re.fullmatch(r'-?[1-9][0-9]{0,19}', v)) for v in routes.values()):
        raise ValueError('Chat IDs must be numeric strings, including any leading minus sign')
    return {key: routes.get(key, '') for key in ROUTES}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS telegram_routes(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,routes TEXT NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_event_routes(
        event TEXT PRIMARY KEY,market TEXT,strategy TEXT,chat TEXT,general TEXT)''')


def apply(store, value):
    with _cache_lock:
        if not isinstance(value, dict) or type(value.get('revision')) is not int or value['revision'] < 0:
            raise ValueError('Invalid Telegram routing revision')
        routes = validate(value.get('routes'))
        with store.connect() as db:
            initialize(db)
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT INTO telegram_routes VALUES(1,?,?) ON CONFLICT(id) DO UPDATE
                SET revision=excluded.revision,routes=excluded.routes WHERE excluded.revision>telegram_routes.revision''',
                (value['revision'], json.dumps(routes)))
            row = db.execute('SELECT revision,routes FROM telegram_routes WHERE id=1').fetchone()
        store._telegram_routes = (row[0], json.loads(row[1]))


def read(store):
    with _cache_lock:
        cached = getattr(store, '_telegram_routes', None)
        if cached is None:
            with store.connect() as db:
                initialize(db)
                row = db.execute('SELECT revision,routes FROM telegram_routes WHERE id=1').fetchone()
            cached = (row[0], json.loads(row[1])) if row else (0, validate({}))
            store._telegram_routes = cached
        return dict(revision=cached[0], routes=dict(cached[1]))


def fetch_cloud():
    url = os.environ.get('CLOUD_SYNC_URL', '')
    token = os.environ.get('CLOUD_SYNC_TOKEN', '')
    if not url.startswith('https://') or not token:
        raise RuntimeError('Cloud routing connection is not configured')
    request = urllib.request.Request(url, data=b'{"routing_only":true}',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read(16385))['telegram_routing']
    except Exception:
        raise RuntimeError('Telegram routing unavailable; retaining cached routes') from None


def refresh(store):
    apply(store, fetch_cloud())


def remember(db, event, market, strategy):
    initialize(db)
    db.execute('INSERT OR IGNORE INTO telegram_event_routes(event,market,strategy) VALUES(?,?,?)',
               (event, market, strategy))


def market_group(market):
    return {'forex': 'forex', 'crypto': 'crypto', 'stock': 'stocks', 'etf': 'stocks'}.get(market, 'other')


def recover(db, payload):
    if payload.get('market'):
        return payload['market']
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='managed_config'").fetchone()
    row = db.execute('SELECT payload FROM managed_config WHERE id=1').fetchone() if exists else None
    for asset in json.loads(row[0]).get('assets', []) if row else []:
        source = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
        if payload.get('asset') == asset['id'] or payload.get('symbol') in (asset['id'], source):
            return asset.get('market') or 'unknown'
    # Rotating US stock feeds may no longer be in configured assets.
    if payload.get('symbol', '').startswith('alpaca:'):
        return 'stock'
    return None


def select(store, event, general, payload=None, notification_key=None):
    routes = read(store)['routes']
    with store.connect() as db:
        initialize(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT market,strategy,chat,general FROM telegram_event_routes WHERE event=?', (event,)).fetchone()
        if row and row[2] is not None:
            return row[2], row[3]
        market, strategy = (row[0], row[1]) if row else (None, None)
        if payload is not None and not market:
            strategy, market = 'rsi', recover(db, payload)
        if not strategy and notification_key:
            prefix, _, key = notification_key.partition(':')
            if prefix in ('pin-bars', 'bb-pin-bars'):
                table = {'pin-bars': 'pin_bar_signals', 'bb-pin-bars': 'bb_pin_bar_signals'}[prefix]
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                signal = db.execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone() if exists else None
                if signal:
                    strategy, market = prefix, recover(db, json.loads(signal[0]))
        fallback = routes['general'] or general
        route = f'{market_group(market)}.{"pin-bars" if strategy in ("pin-bars", "bb-pin-bars") else "rsi"}' if strategy and market else 'general'
        chat = routes.get(route) or fallback
        if not chat:
            raise RuntimeError('Configure a General Telegram destination')
        db.execute('''INSERT INTO telegram_event_routes VALUES(?,?,?,?,?) ON CONFLICT(event)
            DO UPDATE SET chat=excluded.chat,general=excluded.general''', (event, market, strategy, chat, fallback))
        return chat, fallback


def deliver_routed(store, token, general, text, event, send, silent=False, payload=None, notification_key=None):
    from telegram_delivery import deliver
    from platform_app import TelegramDestinationError
    chat, fallback = select(store, event, general, payload, notification_key)
    try:
        return deliver(store, token, chat, text, event, send, silent=silent)
    except TelegramDestinationError:
        if not fallback or fallback == chat:
            raise
        with store.connect() as db:
            db.execute('UPDATE telegram_event_routes SET chat=? WHERE event=?', (fallback, event))
        store.log('Telegram destination unavailable', 'Alert routed to General')
        return deliver(store, token, fallback, text, event, send, silent=silent)
