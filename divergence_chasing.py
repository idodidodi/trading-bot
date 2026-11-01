"""Deterministic, side-effect-free personal RSI chase lifecycle.

All times are UTC milliseconds. No imports start workers, read credentials or send orders.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json

FRAMES = ('4h', '1h', '30m', '15m', '5m', '1m')
CHASE_FRAMES = FRAMES[:-1]
DURATIONS = dict(zip(FRAMES, (14400000, 3600000, 1800000, 900000, 300000, 60000)))
VERSION = 'chasing-v3-position-alerts'
PRE_ENTRY_MS = 300000
POSITION_MS = 60000


def percentage(value):
    if isinstance(value, bool):
        raise ValueError('Enter a percentage greater than zero and below 100')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Enter a valid percentage') from None
    if not number.is_finite() or not 0 < number < 100:
        raise ValueError('Enter a percentage greater than zero and below 100')
    return float(number)


def create(user, root, at, stop_pct=.5, expires_at=None, watch_policy=None, watch_timeouts=None):
    if root['direction'] not in ('bullish', 'bearish') or root['price2'] <= 0:
        raise ValueError('Invalid root signal')
    from crypto_chase_policy import ORDER, POLICY, deadline
    if root['timeframe'] not in (ORDER[:-3] if watch_policy == POLICY else ('1h','4h')):
        raise ValueError('Position-aware chasing starts from a 1h or 4h original')
    identity = [user, root['symbol'], root['timeframe'], root['direction'], root['pivot2'], VERSION]
    state = dict(id=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32],
                user=user, root=deepcopy(root), status='chasing', stage=root['timeframe'],
                started_at=at, parent_at=at, checked_at=at, next_due=at+PRE_ENTRY_MS, stop_pct=percentage(stop_pct),
                expires_at=expires_at, last_price_at=at,
                last_signed=0., last_return_at=None, chain=[], entry_at=None,
                revision=0, version=VERSION)
    if watch_policy == POLICY:
        state.update(watch_policy=POLICY, watch_timeouts=watch_timeouts,
                     expires_at=deadline(root['timeframe'], at, expires_at, watch_timeouts), next_due=at+60000)
    return state


def step(session, now, *, price=None, price_at=None, signals=(), enter=False, leave=False):
    """Return a fresh state and logical events. Caller serializes/persists atomically.

    Legacy pre-entry checks stop at their first event. Crypto watches descend to 5m
    with minute checks. Reported positions continue their existing minute policy.
    Closed minute prices are the reproducible v1 stop/return observation source.
    """
    s = deepcopy(session)
    events = []
    def event(kind, **values):
        events.append(dict(kind=kind, at=now, session=s['id'], **values))
    if leave and s.get('position') and s['position']['status']!='closed':
        s['position'].update(status='closed',closed_at=now)
        s.update(status='reported_exited',next_due=None)
        event('reported_exit')
    elif (enter and s['status'] in ('chasing', 'complete_first')
          and not (s.get('watch_policy') and s['status']=='chasing'
                   and s.get('expires_at') is not None and now>=s['expires_at'])):
        from position_monitor import start
        if s.get('lookback',{}).get('status')=='pending':s['lookback'].update(status='cancelled',reason='Entry reported')
        s.update(status='opposite', entry_at=now, next_due=now,position=start(s['root']['timeframe'],now))
        if s['chain']:s['position']['additional_divergence']=deepcopy(s['chain'][0])
        event('entered')
    elif s['status'] == 'chasing':
        from crypto_chase_policy import POLICY, advance
        if s.get('watch_policy') == POLICY:
            s, events = advance(s, now, signals)
        elif s.get('expires_at') is not None and now >= s['expires_at']:
            s.update(status='expired', next_due=None)
            event('expired')
        elif price is not None and price_at is not None and s['last_price_at'] < price_at <= now and now-price_at <= 120000:
            value = Decimal(str(price))
            if not value.is_finite() or value <= 0:
                raise ValueError('Invalid observed price')
            origin = Decimal(str(s['root']['price2']))
            direction = 1 if s['root']['direction'] == 'bullish' else -1
            signed = direction * (value/origin-1)
            previous = s['last_signed']
            s.update(last_price_at=price_at, last_signed=float(signed))
            if signed >= Decimal(str(s['stop_pct']))/100:
                s.update(status='stopped_favorable', next_due=None)
                event('favorable_stop', price=price, price_at=price_at, stop_pct=s['stop_pct'])
            else:
                if previous < 0 <= signed and (s['last_return_at'] is None or now-s['last_return_at'] >= 60000):
                    s['last_return_at'] = now
                    event('return_to_close', price=price, price_at=price_at)
                if signed < 0:
                    candidates = [c for c in signals if c['timeframe'] in CHASE_FRAMES
                                  and DURATIONS[c['timeframe']]<=DURATIONS[s['root']['timeframe']]
                                  and c['symbol'] == s['root']['symbol']
                                  and c['direction'] == s['root']['direction']
                                  and s['checked_at'] < c['confirmed_at'] <= price_at]
                    # Checks inspect the available frames from smallest to largest.
                    # Within one frame take its earliest newly available event.
                    candidates.sort(key=lambda c: (-FRAMES.index(c['timeframe']), c['confirmed_at'], c['pivot2'], c['pivot1']))
                    if candidates:
                        chosen = deepcopy(candidates[0])
                        s.update(stage=chosen['timeframe'], parent_at=now)
                        s['chain'].append(chosen)
                        event('chase', signal=chosen)
                        s.update(status='complete_first', next_due=None)
                if s['status'] == 'chasing':
                    s['next_due'] = now+PRE_ENTRY_MS
                s['checked_at'] = now
        else:
            s['next_due'] = now+PRE_ENTRY_MS
    elif s.get('position') and s['position']['status']!='closed' and now>=s['next_due']:
        from position_monitor import update
        s['position'],updates=update(s['position'],now,price,price_at,s['root']['direction'],signals,s['root']['symbol'])
        for change in updates:
            event(change['kind'],**{k:v for k,v in change.items() if k!='kind'})
        s['next_due']=now+POSITION_MS
        if s['status']=='opposite':
            candidates=[c for c in signals if c['symbol']==s['root']['symbol'] and c['timeframe']==s['root']['timeframe']
                        and c['direction']!=s['root']['direction'] and s['entry_at']<c['confirmed_at']<=now]
            if candidates:
                chosen=min(candidates,key=lambda c:(c['confirmed_at'],c['pivot2'],c['pivot1']))
                s.update(status='opposite_found',opposite=deepcopy(chosen))
                event('opposite',signal=deepcopy(chosen))
    elif s['status'] == 'opposite' and now >= s['next_due']:
        candidates = [c for c in signals if c['symbol'] == s['root']['symbol']
                      and c['timeframe'] == s['root']['timeframe']
                      and c['direction'] != s['root']['direction']
                      and s['entry_at'] < c['confirmed_at'] <= now]
        if candidates:
            chosen = min(candidates, key=lambda c: (c['confirmed_at'], c['pivot2'], c['pivot1']))
            s.update(status='opposite_found', next_due=None, opposite=deepcopy(chosen))
            event('opposite', signal=deepcopy(chosen))
        else:
            s['next_due'] = now+300000
    if s != session:
        s['revision'] += 1
    return s, events
