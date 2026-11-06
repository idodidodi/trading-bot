"""One descending crypto RSI chase, independent of prices and trade monitoring."""
from copy import deepcopy
from datetime import datetime, timezone
import calendar

POLICY = 'crypto-descending-v1'
ORDER = ('monthly', 'weekly', 'daily', '4h', '1h', '30m', '15m', '5m')
TARGETS = ('1h', '30m', '15m', '5m')
TIMEOUTS = {'weekly': (7*24*60-5)*60000,'daily': (24*60-5)*60000,
            '4h': 235*60000,'1h': 55*60000,'30m': 25*60000,'15m': 10*60000}


def is_crypto(root):
    return root.get('market') == 'crypto' or root.get('symbol', '').startswith('kraken:Kraken:')


def lower(frame, parent):
    return frame in ORDER and parent in ORDER and ORDER.index(frame) > ORDER.index(parent)


def deadline(frame, at, previous=None, timeouts=None):
    if frame=='monthly' and not timeouts:
        start=datetime.fromtimestamp(at/1000,timezone.utc)
        month=start.month%12+1;year=start.year+(start.month==12)
        end=start.replace(year=year,month=month,day=min(start.day,calendar.monthrange(year,month)[1]))
        return int(end.timestamp()*1000)-5*60000
    duration = (timeouts or TIMEOUTS).get(frame)
    return at + duration if duration is not None else previous


def advance(session, now, signals):
    """Notify each descent; equal/higher, old and opposite signals cannot replace it."""
    s = deepcopy(session)
    events = []
    def emit(kind, **values):
        events.append(dict(kind=kind, at=now, session=s['id'], **values))
    if s.get('expires_at') is not None and now >= s['expires_at']:
        s.update(status='expired', next_due=None)
        if s.get('lookback',{}).get('status')=='pending':s['lookback'].update(status='cancelled',reason='Watch expired')
        emit('expired')
        return s, events
    candidates = [c for c in signals if c['timeframe'] in TARGETS
                  and c['symbol'] == s.get('chase_symbol',s['root']['symbol'])
                  and c['direction'] == s['root']['direction']
                  and s['started_at'] < c['confirmed_at'] <= now]
    # Keep causal order; on a simultaneous close notify the higher stage first.
    candidates.sort(key=lambda c: (c['confirmed_at'], ORDER.index(c['timeframe']), c['pivot2'], c['pivot1']))
    for chosen in candidates:
        if not lower(chosen['timeframe'], s['stage']):
            continue
        s.update(stage=chosen['timeframe'], parent_at=now)
        s['chain'].append(deepcopy(chosen))
        emit('chase', signal=deepcopy(chosen))
        if s['stage'] == '5m':
            s.update(status='complete_first', next_due=None)
            if s.get('lookback',{}).get('status')=='pending':s['lookback'].update(status='cancelled',reason='5m reached')
            break
        s['expires_at'] = deadline(s['stage'], now, s.get('expires_at'), s.get('watch_timeouts'))
    s['checked_at'] = now
    if s['status'] == 'chasing':
        s['next_due'] = min(now + 60000, s['expires_at']) if s.get('expires_at') is not None else now + 60000
    return s, events
