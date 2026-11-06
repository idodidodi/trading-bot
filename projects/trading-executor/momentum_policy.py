"""Consumer-owned momentum entry, exit and OHLC simulation policy."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
RULES=dict(max_hold=10,cost_bps=10)

def outcome(signal, bars):
    rules=signal.get('rules',RULES)
    def opened_at(bar):
        if signal['market']!='stock':return bar.start
        local=datetime.fromtimestamp(bar.start/1000,ZoneInfo('America/New_York'))
        return int(local.replace(hour=9,minute=30,second=0,microsecond=0).timestamp()*1000)
    baseline=max(signal['confirmed_at'],signal.get('published_at',0))
    following=[b for b in bars if opened_at(b)>=baseline]
    if not following:return dict(status='awaiting entry')
    first=following[0];entry=first.open
    if signal.get('entry_open_at') and opened_at(first)!=signal['entry_open_at']:
        return dict(status='unavailable',reason='Entry-session candle missing; no later entry substituted')
    if opened_at(first)-baseline>(5 if signal['market']=='stock' else 2)*86400000:
        return dict(status='unavailable',reason='Entry-session history missing; no later entry substituted')
    if not signal['entry_min']<=entry<=signal['entry_max']:
        return dict(status='skipped gap',entry_at=opened_at(first))
    d=1 if signal['direction']=='buy' else -1
    # Preserve quoted stop/target; next-open entry must still offer at least 1.5R.
    risk=d*(entry-signal['stop']);reward=d*(signal['target']-entry)
    if risk<=0 or reward/risk<1.5:return dict(status='skipped reward/risk')
    adverse=favorable=0
    for n,b in enumerate(following[:rules['max_hold']],1):
        adverse=max(adverse,max(0,d*(entry-(b.low if d==1 else b.high)))/entry*100)
        favorable=max(favorable,max(0,d*((b.high if d==1 else b.low)-entry))/entry*100)
        # Stop first if both levels are touched; opening gaps execute at the open.
        stop_hit=b.low<=signal['stop'] if d==1 else b.high>=signal['stop']
        target_hit=b.high>=signal['target'] if d==1 else b.low<=signal['target']
        if stop_hit:
            exit_price=min(b.open,signal['stop']) if d==1 else max(b.open,signal['stop']);status='stop'
        elif target_hit:exit_price=signal['target'];status='target'
        elif n==rules['max_hold']:exit_price=b.close;status='time exit'
        else:continue
        return dict(status=status,entry_at=opened_at(first),entry_price=entry,exit_at=b.end,
                    exit_price=exit_price,net_pct=d*(exit_price/entry-1)*100-2*rules['cost_bps']/100,
                    adverse_pct=adverse,favorable_pct=favorable)
    return dict(status='open',entry_at=opened_at(first),entry_price=entry,adverse_pct=adverse,favorable_pct=favorable)


def entry_session(market, now):
    zone=ZoneInfo('America/New_York' if market=='stock' else 'UTC')
    local=datetime.fromtimestamp(now/1000,zone)
    opening=local.replace(hour=9 if market=='stock' else 0,minute=30 if market=='stock' else 0,second=0,microsecond=0)
    if opening<local:opening+=timedelta(days=1)
    if market=='stock':
        from market_calendar import stock_trading_day,stock_close
        while not stock_trading_day(opening.date()):opening+=timedelta(days=1)
        closing=stock_close(opening.date())
    else:closing=opening+timedelta(days=1)
    return dict(entry_open_at=int(opening.timestamp()*1000),expires_at=int(closing.timestamp()*1000))




def prepare(event):
    """Consumer policy v1; execution levels are never supplied by the producer."""
    signal=dict(event['evidence']);price=signal['price2'];atr=signal['atr']
    direction=1 if event['direction']=='bullish' else -1
    signal.update(symbol=event['instrument']['symbol'],market=event['instrument']['market'],
        direction='buy' if direction==1 else 'sell',entry=price,entry_min=price-.5*atr,entry_max=price+.5*atr,
        stop=price-direction*1.5*atr,target=price+direction*3*atr,
        rules=dict(max_hold=10,cost_bps=10),signal_id=event['signal_id'],event_id=event['event_id'])
    signal.update(entry_session(signal['market'],signal.get('published_at',event['published_at'])))
    return signal
