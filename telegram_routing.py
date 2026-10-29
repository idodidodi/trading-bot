"""Installation routing cache and durable per-alert destinations. No bot secrets."""
import json
import os
import re
from threading import RLock
import urllib.request

_cache_lock = RLock()

ROUTES = ('general',) + tuple(f'{m}.{s}' for m in ('forex', 'crypto', 'stocks', 'other') for s in ('rsi', 'pin-bars', 'rsi-pb'))


def validate(routes):
    if not isinstance(routes, dict) or set(routes) - set(ROUTES):
        raise ValueError('Invalid Telegram routes')
    if any(not isinstance(v, str) or (v and not re.fullmatch(r'-?[1-9][0-9]{0,19}', v)) for v in routes.values()):
        raise ValueError('Chat IDs must be numeric strings, including any leading minus sign')
    return {key: routes.get(key, '') for key in ROUTES}


def initialize(db):
    db.require_table('telegram_routes')
    db.require_table('telegram_event_routes')


def read(store):
    with store.connect() as db:
        value = db.execute('SELECT bot_runtime.read_routes()').fetchone()[0]
        row = (value['revision'],value['routes'])
    return dict(revision=row[0], routes=validate(row[1])) if row else dict(revision=0, routes=validate({}))


def refresh(store):
    return read(store)


def remember(db, event, market, strategy):
    initialize(db)
    db.execute('INSERT INTO telegram_event_routes(event,market,strategy) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
               (event, market, strategy))


def market_group(market):
    return {'forex': 'forex', 'crypto': 'crypto', 'stock': 'stocks', 'stocks': 'stocks',
            'equity': 'stocks', 'equities': 'stocks', 'etf': 'stocks'}.get(market, 'other')


def recover(db, payload):
    if payload.get('market') not in (None, '', 'unknown'):
        return payload['market']
    exists = db.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='managed_config'").fetchone()
    row = db.execute('SELECT payload FROM managed_config WHERE id=1').fetchone() if exists else None
    for asset in json.loads(row[0]).get('assets', []) if row else []:
        source = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
        if payload.get('asset') == asset['id'] or payload.get('symbol') in (asset['id'], source):
            if asset.get('market') not in (None, '', 'unknown'):
                return asset['market']
    # Rotating US stock feeds may no longer be in configured assets.
    if payload.get('symbol', '').startswith('alpaca:') or payload.get('source','').startswith('alpaca:'):
        return 'stock'
    return payload.get('market') or None


def select(store, event, general, payload=None, notification_key=None):
    routes = read(store)['routes']
    with store.connect() as db:
        initialize(db)
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        row = db.execute('SELECT market,strategy,chat,general FROM telegram_event_routes WHERE event=%s', (event,)).fetchone()
        if row and row[2] is not None:
            return row[2], row[3]
        market, strategy = (row[0], row[1]) if row else (None, None)
        if payload is not None and market in (None, '', 'unknown'):
            strategy, market = strategy or 'rsi', recover(db, payload)
        if notification_key and (not strategy or market in (None, '', 'unknown')):
            prefix, _, key = notification_key.partition(':')
            if prefix in ('pin-bars', 'bb-pin-bars', 'rsi-pb'):
                table = {'pin-bars': 'pin_bar_signals', 'bb-pin-bars': 'bb_pin_bar_signals','rsi-pb':'rsi_pb_signals'}[prefix]
                exists = db.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s', (table,)).fetchone()
                signal = db.execute(f'SELECT payload FROM {table} WHERE id=%s', (key,)).fetchone() if exists else None
                if signal:
                    strategy, market = prefix, recover(db, json.loads(signal[0]))
        fallback = routes['general'] or general
        route = f'{market_group(market)}.{"pin-bars" if strategy in ("pin-bars", "bb-pin-bars") else "rsi-pb" if strategy=="rsi-pb" else "rsi"}' if strategy and market else 'general'
        chat = routes.get(route) or fallback
        if not chat:
            raise RuntimeError('Configure a General Telegram destination')
        db.execute('INSERT INTO telegram_event_routes VALUES(%s,%s,%s,%s,%s) ON CONFLICT(event)\n            DO UPDATE SET market=excluded.market,strategy=excluded.strategy,chat=excluded.chat,general=excluded.general', (event, market, strategy, chat, fallback))
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
            db.execute('UPDATE telegram_event_routes SET chat=%s WHERE event=%s', (fallback, event))
        store.log('Telegram destination unavailable', 'Alert routed to General')
        return deliver(store, token, fallback, text, event, send, silent=silent)
