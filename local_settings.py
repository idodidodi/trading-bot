"""Installation-local runtime preferences; never included in cloud snapshots."""
import os

from runtime_env import environment

KEYS = {'telegram_enabled': 'DEV_TELEGRAM_ENABLED',
        'paper_trading_enabled': 'PAPER_TRADING_ENABLED'}


def initialize(db):
    db.require_table('local_runtime_settings')


def read(store=None):
    values = {key: os.environ.get(env, 'false').lower() == 'true' for key, env in KEYS.items()}
    if store is not None:
        with store.connect() as db:
            values.update({key: bool(value) for key, value in db.execute('SELECT key,value FROM local_runtime_settings') if key in KEYS})
    if environment() == 'dev':
        values['paper_trading_enabled'] = False
    return dict(environment=environment(), editable=environment() == 'dev',
                paper_trading_editable=environment() == 'prod', **values)


def save(store, body):
    if not body or not set(body) <= set(KEYS) or any(type(value) is not bool for value in body.values()):
        raise ValueError('Supply known local settings with true or false values.')
    if environment() != 'dev' and set(body) & {'telegram_enabled', 'cloud_sync_enabled'}:
        raise ValueError('Development overrides are only available in development.')
    if environment() == 'dev' and body.get('paper_trading_enabled'):
        raise ValueError('Paper trading remains disabled in development.')
    from telegram_routing import read as routing_read
    if body.get('telegram_enabled') and not (os.environ.get('TELEGRAM_BOT_TOKEN') and (os.environ.get('TELEGRAM_CHAT_ID') or routing_read(store)['routes']['general'])):
        raise ValueError('Configure TELEGRAM_BOT_TOKEN locally and a General Telegram destination before enabling delivery.')
    if body.get('paper_trading_enabled') and not (os.environ.get('ALPACA_API_KEY') and os.environ.get('ALPACA_SECRET_KEY')):
        raise ValueError('Configure ALPACA_API_KEY and ALPACA_SECRET_KEY locally before enabling paper trading.')
    with store.connect() as db:
        db.executemany('INSERT INTO local_runtime_settings VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value', [(key, int(value)) for key, value in body.items()])
    return read(store)


def delivery_dry_run(store, fallback):
    return not read(store)['telegram_enabled'] if environment() == 'dev' else fallback


def paper_trading_enabled(store=None):
    return read(store)['paper_trading_enabled'] if environment() == 'prod' else False
