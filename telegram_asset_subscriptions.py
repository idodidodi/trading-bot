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
    strategy='trend-pin-bars' if event.startswith('notification:trend-pin-bars:') else 'rsi'
    if not payload or not (event.startswith('divergence:') or strategy=='trend-pin-bars'):
        return
    # Do not deliver reversal alerts left in the queue by the retired rule.
    if strategy=='trend-pin-bars' and payload.get('setup')!='trend_pullback':
        return
    from telegram_delivery import deliver, DeliveryPending
    from postgres_store import WorkerOwned, RETRYABLE
    with store.connect() as db:
        # Backward-compatible rollout: install schema before enabling a route.
        if not db.execute("SELECT to_regclass('bot_runtime.telegram_asset_subscriptions')").fetchone()[0]:
            return
        subscriptions = db.execute('SELECT chat,asset,timeframes,extract(epoch FROM created_at) '
                                   'FROM telegram_asset_subscriptions WHERE enabled').fetchall()
        prefs={}
        if db.execute("SELECT to_regclass('bot_runtime.near_group_preferences')").fetchone()[0]:
            prefs={r[0]:(r[1],r[2]) for r in db.execute('SELECT chat,subscriptions,enrolled FROM near_group_preferences')}
        if strategy=='trend-pin-bars':
            subscriptions=[(chat,'NEARUSD',p[0].get(strategy,[]),0) for chat,p in prefs.items()]
    failed = False
    for subscription in subscriptions:
        chat = subscription[0]
        if strategy=='rsi':
            if not eligible(subscription,payload):continue
        elif payload.get('asset')!='NEARUSD' or payload.get('timeframe') not in subscription[2]:continue
        if chat in prefs:
            selections,enrolled=prefs[chat];frame=payload.get('timeframe')
            if frame not in selections.get(strategy,[]):continue
            if payload.get('confirmed_at',0)/1000<float(enrolled.get(strategy+':'+frame,subscription[3])):continue
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
