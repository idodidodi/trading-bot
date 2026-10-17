"""Executable production and isolated source development defaults."""
import os
from pathlib import Path
import sys


def environment():
    if getattr(sys, 'frozen', False):
        return 'prod'
    value = os.environ.get('TRADING_BOT_ENV', 'dev').lower()
    if value not in ('dev', 'prod'):
        raise ValueError('TRADING_BOT_ENV must be dev or prod')
    return value


def data_directory(root):
    if environment() == 'dev':
        return Path(os.environ.get('DEV_DATA_DIR', str(Path(root) / 'data/dev')))
    return Path(os.environ.get('DATA_DIR', str(Path(root) / 'data')))


def cloud_sync_enabled():
    return environment() == 'prod' or os.environ.get('DEV_CLOUD_SYNC_ENABLED', 'false').lower() == 'true'


def configure(root):
    env = environment()
    os.environ['DATA_DIR'] = str(data_directory(root))
    if env == 'dev':
        os.environ['PORT'] = os.environ.get('DEV_PORT', '8082')
        os.environ['WEBHOOK_PORT'] = os.environ.get('DEV_WEBHOOK_PORT', '8083')
        os.environ['DRY_RUN'] = 'false' if os.environ.get('DEV_TELEGRAM_ENABLED', 'false').lower() == 'true' else 'true'
        os.environ['PAPER_TRADING_ENABLED'] = 'false'
    return env
