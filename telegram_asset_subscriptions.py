"""Additional asset-specific RSI copies using the existing delivery ledger."""
from functools import partial


def eligible(subscription, payload):
    chat, asset, frames, enrolled = subscription
    # Require the canonical asset ID added by the scanner. Never infer it from
    # a display name, substring, quote currency, or an old queued finding.
    return (payload.get('asset') == asset
            and payload.get('timeframe') in frames
            and payload.get('signal_status') == 'provisional'
            and payload.get('alert_timing') == 'pivot_close'
            and payload.get('divergence_type', 'regular') in ('regular', 'hidden')
            and payload.get('confirmed_at', 0) / 1000 >= enrolled)


def deliver_subscriptions(store, token, text, event, send, silent=False, payload=None):
    if not payload or not event.startswith('divergence:'):
        return
    from telegram_delivery import deliver, DeliveryPending
    from postgres_store import WorkerOwned, RETRYABLE
    with store.connect() as db:
        # Backward-compatible rollout: install schema before enabling a route.
        if not db.execute("SELECT to_regclass('bot_runtime.telegram_asset_subscriptions')").fetchone()[0]:
            return
        subscriptions = db.execute('SELECT chat,asset,timeframes,extract(epoch FROM created_at) '
                                   'FROM telegram_asset_subscriptions WHERE enabled').fetchall()
    failed = False
    for subscription in subscriptions:
        if not eligible(subscription, payload):
            continue
        chat = subscription[0]
        # Private destinations bind Done/Undo to that Telegram user. A group
        # subscription uses the existing shared dismissal controls.
        from telegram_checklist import group_dismissal_enabled
        sender = (partial(send, dismissal_user_id=int(chat)) if int(chat) > 0
                  else partial(send, dismissal_user_id=0) if group_dismissal_enabled() else send)
        try:
            deliver(store, token, chat, text, event, sender, silent=silent)
        except (WorkerOwned, *RETRYABLE):
            raise
        except RuntimeError:
            failed = True
    if failed:
        raise DeliveryPending('An asset-specific Telegram delivery needs retry')
