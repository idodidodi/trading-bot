"""NEAR group selections and conservative demand from all active consumers."""
import json
import os

FRAMES=('5m','15m','30m','1h','4h','daily','weekly')


def snapshot(store):
    with store.connect() as db:
        if not db.execute("SELECT to_regclass('bot_runtime.near_group_preferences')").fetchone()[0]:return None
        preferences=db.execute('SELECT chat,subscriptions,enrolled FROM near_group_preferences').fetchall()
        if not preferences:return None  # Opt in only after explicit migration/settings.
        subscriptions=db.execute('SELECT chat,asset,timeframes,enabled FROM telegram_asset_subscriptions').fetchall()
        services=db.execute("SELECT filters FROM signal_subscriptions WHERE enabled AND mode='live'").fetchall()
    from telegram_routing import read
    from telegram_personal import recipients
    return dict(preferences=preferences,subscriptions=subscriptions,services=[r[0] for r in services],
                routes=read(store)['routes'],people=recipients(store))


def requested(state,strategy,frame):
    if any(frame in p[1].get(strategy,[]) for p in state['preferences']):return True
    return strategy=='rsi' and any(asset=='NEARUSD' and enabled and frame in frames for _,asset,frames,enabled in state['subscriptions'])


def needed(state,strategy,frame):
    if requested(state,strategy,frame):return True
    # A live signal-service subscriber can consume signals without using Telegram.
    for filters in state['services']:
        if isinstance(filters,str):filters=json.loads(filters)
        if all(key not in filters or value in filters[key] for key,value in
               [('strategy',strategy),('symbol','NEARUSD'),('timeframe',frame),('market','crypto')]):return True
        # RSI contract instruments use their provider-qualified symbol.
        if strategy=='rsi' and all(key not in filters or value in filters[key] for key,value in
               [('strategy',strategy),('symbol','kraken:Kraken:NEAR/USD'),('timeframe',frame),('market','crypto')]):return True
    # The dedicated group's controls must not pause another group's scans.
    if frame=='5m':return False  # Existing broad/personal five-minute mute.
    route='crypto.'+('pin-bars' if strategy in ('bb-pin-bars','trend-pin-bars') else strategy)
    dedicated={str(p[0]) for p in state['preferences']}
    def other(chat):return bool(chat) and str(chat) not in dedicated
    if any(other(chat) for chat in (state['routes'].get(route),state['routes'].get('general'),os.environ.get('TELEGRAM_CHAT_ID'))):return True
    return any(other(p['routes'].get(route)) and p['enabled'].get(route,True) for p in state['people'])


def plan(store,state,asset,frame,strategies,now):
    if not state or asset.get('id')!='NEARUSD' or asset.get('symbol')!='NEAR/USD':return strategies,{}
    wanted=set(strategies)
    for strategy in ('rsi','trend-pin-bars'):
        if frame in FRAMES and (requested(state,strategy,frame) or strategy=='trend-pin-bars' and needed(state,strategy,frame)):wanted.add(strategy)
    # Higher-timeframe RSI context is a real consumer of the same detector.
    context=needed(state,'rsi','5m') and frame in ('1h','4h','daily','weekly','monthly')
    for strategy in ('rsi','pin-bars','bb-pin-bars','trend-pin-bars'):
        if strategy in wanted and not needed(state,strategy,frame) and not (strategy=='rsi' and context):wanted.remove(strategy)
    resumes={}
    with store.connect() as db:
        for strategy in ('rsi','pin-bars','bb-pin-bars','trend-pin-bars'):
            active=strategy in wanted
            old=db.execute('SELECT enabled,resume_at FROM near_scan_demand WHERE strategy=%s AND timeframe=%s',(strategy,frame)).fetchone()
            resume=now if active and (not old or not old[0]) else old[1] if old else 0
            db.execute('INSERT INTO near_scan_demand VALUES(%s,%s,%s,%s) ON CONFLICT(strategy,timeframe) DO UPDATE SET enabled=excluded.enabled,resume_at=excluded.resume_at',(strategy,frame,active,resume))
            if active:resumes[strategy]=resume
    return sorted(wanted),resumes


def annotation(asset,candles,at):
    if asset.get('id')!='NEARUSD':return {}
    from wyckoff_context import analyze
    return {'wyckoff':analyze(candles,asset.get('_volumes',{}),at)}


def allows_destination(store,chat,payload,strategy='rsi'):
    """A broad category route must not bypass this group's unchecked RSI box."""
    if not payload or payload.get('asset')!='NEARUSD':return True
    with store.connect() as db:
        if not db.execute("SELECT to_regclass('bot_runtime.near_group_preferences')").fetchone()[0]:return True
        row=db.execute('SELECT subscriptions,enrolled FROM near_group_preferences WHERE chat=%s',(str(chat),)).fetchone()
    if not row:return True
    if strategy not in ('rsi','trend-pin-bars'):return False
    if strategy=='trend-pin-bars' and payload.get('setup')!='trend_pullback':return False
    frame=payload.get('timeframe')
    return frame in row[0].get(strategy,[]) and payload.get('confirmed_at',0)/1000>=float(row[1].get(strategy+':'+frame,0))
