"""Additional portfolio copies; uses the existing per-destination delivery ledger."""
import json
from pathlib import Path

TABLES = {name: name.replace('-', '_') + '_signals' for name in
          ('rsi-pb', 'ma-cross')}
TABLES.update({'pin-bars': 'pin_bar_signals', 'bb-pin-bars': 'bb_pin_bar_signals',
               'trend-pin-bars': 'trend_pin_bar_signals'})
FRAMES = frozenset(('4h', 'daily', 'weekly', 'monthly'))


def eligible(config, signal, strategy):
    # Notification engines store canonical asset IDs in symbol; RSI uses asset.
    asset = signal.get('asset') if strategy == 'rsi' else signal.get('symbol')
    enrolled = config.get('asset_enrolled_at', {}).get(asset, config.get('enrolled_at', 0))
    confirmed = signal.get('confirmed_at', 0)
    return (config.get('enabled') is True and enrolled > 0
            and asset in config.get('assets', [])
            and signal.get('timeframe') in FRAMES
            and isinstance(confirmed, (int, float)) and confirmed >= enrolled * 1000)


def deliver_portfolio(store, token, text, event, send, silent=False,
                      payload=None, notification_key=None):
    from platform_app import ROOT
    path = Path(ROOT) / 'telegram-portfolio.json'
    if not path.exists():
        return
    config = json.loads(path.read_text())
    if config.get('enabled') is not True:
        return
    if notification_key:
        strategy, _, key = notification_key.partition(':')
        table = TABLES.get(strategy)
        if not table:
            return  # No reports, demos, summaries or connection tests.
        with store.connect() as db:
            row = db.execute(f'SELECT payload FROM {table} WHERE id=%s', (key,)).fetchone()
        if not row:
            raise RuntimeError('Portfolio signal metadata unavailable')
        payload = json.loads(row[0])
    elif event.startswith('divergence:') and payload:
        strategy = 'rsi'
    else:
        return
    if not eligible(config, payload, strategy):
        return
    if strategy == 'trend-pin-bars' and payload.get('setup') != 'trend_pullback':
        return
    from functools import partial
    from telegram_checklist import group_dismissal_enabled
    from telegram_delivery import deliver
    sender = partial(send, dismissal_user_id=0) if group_dismissal_enabled() else send
    return deliver(store, token, config['chat_id'], text, event, sender, silent=silent)
