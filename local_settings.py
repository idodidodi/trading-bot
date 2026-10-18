"""Installation-local development preferences; never included in cloud snapshots."""
import os

from runtime_env import environment

KEYS = {'telegram_enabled': 'DEV_TELEGRAM_ENABLED', 'cloud_sync_enabled': 'DEV_CLOUD_SYNC_ENABLED'}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS local_runtime_settings(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')


def read(store=None):
    values = {key: os.environ.get(env, 'false').lower() == 'true' for key, env in KEYS.items()}
    if store is not None:
        with store.connect() as db:
            values.update({key: bool(value) for key, value in db.execute('SELECT key,value FROM local_runtime_settings') if key in KEYS})
    return dict(environment=environment(), editable=environment() == 'dev', **values)


def save(store, body):
    if environment() != 'dev':
        raise ValueError('Development overrides are only available in development.')
    if set(body) != set(KEYS) or any(type(value) is not bool for value in body.values()):
        raise ValueError('Both development settings must be true or false.')
    if body['telegram_enabled'] and not (os.environ.get('TELEGRAM_BOT_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID')):
        raise ValueError('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID locally before enabling delivery.')
    if body['cloud_sync_enabled'] and not (os.environ.get('CLOUD_SYNC_URL', '').startswith('https://') and os.environ.get('CLOUD_SYNC_TOKEN')):
        raise ValueError('Configure an HTTPS CLOUD_SYNC_URL and CLOUD_SYNC_TOKEN locally before enabling sync.')
    with store.connect() as db:
        db.executemany('INSERT OR REPLACE INTO local_runtime_settings VALUES(?,?)', [(key, int(value)) for key, value in body.items()])
    return read(store)


def delivery_dry_run(store, fallback):
    return not read(store)['telegram_enabled'] if environment() == 'dev' else fallback
