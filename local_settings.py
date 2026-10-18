"""Installation-local runtime preferences; never included in cloud snapshots."""
import os

from runtime_env import environment

KEYS = {'telegram_enabled': 'DEV_TELEGRAM_ENABLED', 'cloud_sync_enabled': 'DEV_CLOUD_SYNC_ENABLED',
        'paper_trading_enabled': 'PAPER_TRADING_ENABLED'}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS local_runtime_settings(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')


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
    if body.get('telegram_enabled') and not (os.environ.get('TELEGRAM_BOT_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID')):
        raise ValueError('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID locally before enabling delivery.')
    if body.get('cloud_sync_enabled') and not (os.environ.get('CLOUD_SYNC_URL', '').startswith('https://') and os.environ.get('CLOUD_SYNC_TOKEN')):
        raise ValueError('Configure an HTTPS CLOUD_SYNC_URL and CLOUD_SYNC_TOKEN locally before enabling sync.')
    if body.get('paper_trading_enabled') and not (os.environ.get('ALPACA_API_KEY') and os.environ.get('ALPACA_SECRET_KEY')):
        raise ValueError('Configure ALPACA_API_KEY and ALPACA_SECRET_KEY locally before enabling paper trading.')
    with store.connect() as db:
        db.executemany('INSERT OR REPLACE INTO local_runtime_settings VALUES(?,?)', [(key, int(value)) for key, value in body.items()])
    return read(store)


def delivery_dry_run(store, fallback):
    return not read(store)['telegram_enabled'] if environment() == 'dev' else fallback


def paper_trading_enabled(store=None):
    return read(store)['paper_trading_enabled'] if environment() == 'prod' else False
