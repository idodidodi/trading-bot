"""Explicit local-dashboard connectivity tests; never enqueue trades or alerts."""
import hashlib
import json
import os
import time
import urllib.request
import uuid

from app_version import VERSION


class ConnectionTestError(RuntimeError):
    """An error safe to display without disclosing credentials or remote bodies."""


def run(action, rules, store=None):
    if action == 'telegram':
        token=os.environ.get('TELEGRAM_BOT_TOKEN','')
        from telegram_routing import read
        chat=(read(store)['routes']['general'] if store is not None else '') or os.environ.get('TELEGRAM_CHAT_ID','')
        if not token or not chat:
            raise ConnectionTestError('Set TELEGRAM_BOT_TOKEN locally and configure General in online Settings or TELEGRAM_CHAT_ID.')
        from platform_app import send_telegram
        from runtime_env import environment
        try:
            send_telegram(token,chat,f'Family Trading Bot {VERSION} — Telegram connection test ({environment()}). This is a test message, not a trading signal.')
        except Exception:
            raise ConnectionTestError('Telegram test failed. Check the bot token, chat ID, bot permissions and internet connection.') from None
        return dict(ok=True,message='Telegram test message sent to your configured chat.')
    if action not in ('database','cloud-demo'):
        raise ValueError('Unknown connection test')
    try:
        from postgres_store import connect
        started=time.monotonic()
        with connect() as db:
            db.execute('SELECT 1').fetchone()
        elapsed=round((time.monotonic()-started)*1000)
    except Exception:
        raise ConnectionTestError('Postgres connection unavailable; check verified TLS, credentials and network.') from None
    return dict(ok=True,message=f'Postgres read-only connection check passed ({elapsed} ms).')
