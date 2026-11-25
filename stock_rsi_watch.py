"""Pure lifecycle for stock RSI watches. No orders or Telegram side effects."""
from datetime import datetime, time, timedelta
import hashlib
from market_calendar import NY, stock_close, stock_trading_day, previous_session

FRAMES = ('15m', '30m', '1h', '4h')
MINUTES = dict(zip(FRAMES, (15, 30, 60, 240)))
DIRECTIONS = ('bullish', 'bearish')
GROUPS = {'higher': '-5596046632', 'lower': '-5462582401',
          'ready': '-5481883301', 'invalid': '-5517376122'}


def millis(dt):
    return int(dt.timestamp()*1000)


def cycles(now):
    """Holiday-aware overlapping daily/weekly cycles, including weekend recovery."""
    day = datetime.fromtimestamp(now/1000, NY).date()
    result = []
    for offset in range(10):
        session = day-timedelta(days=offset)
        if not stock_trading_day(session):
            continue
        monday = session-timedelta(days=session.weekday())
        first = monday
        while not stock_trading_day(first):
            first += timedelta(days=1)
        following = session+timedelta(days=1)
        while not stock_trading_day(following):
            following += timedelta(days=1)
        close = stock_close(session)
        opening = datetime.combine(following, time(9,30), NY)
        for frame in ('daily', 'weekly'):
            if frame == 'daily' and session == first:
                continue
            if frame == 'weekly' and session != previous_session(monday+timedelta(days=4)):
                continue
            expiry = opening if frame == 'weekly' else opening-timedelta(minutes=15)
            start = close-timedelta(hours=3)
            if millis(start) <= now < millis(expiry):
                result.append(dict(id=f'{frame}:{session}', frame=frame,
                    start=millis(start), close=millis(close), expires=millis(expiry),
                    period_start=millis(datetime.combine(monday if frame == 'weekly' else session, time.min, NY))))
    return result


def new_cycle(spec, rules):
    return dict(spec, rules=rules, setups={}, counts=dict.fromkeys(DIRECTIONS, 0),
                events=[], coverage={}, cursor=0, sweep=0, next_scan=0)


def key(*parts):
    return hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:24]


def parent_status(cycle, now):
    return 'CLOSED' if now >= cycle['close'] else 'PROVISIONAL'


def emit(cycle, setup, kind, now, *, match=None, reason=None):
    identity = key(cycle['id'], setup['id'], kind, match['id'] if match else '')
    if any(e['id'] == identity for e in cycle['events']):
        return
    cycle['events'].append(dict(id=identity, kind=kind, chat=GROUPS[kind],
        symbol=setup['symbol'], direction=setup['direction'], higher=cycle['frame'],
        higher_status=parent_status(cycle, now), observed_at=now, setup_id=setup['id'],
        signal=match['signal'] if match else setup['signal'],
        lower=match['frame'] if match else None, reason=reason,
        match_id=match['id'] if match else None, lower_end=match['end'] if match else None,
        lower_status=('CLOSED' if now >= match['end'] else 'PROVISIONAL') if match else None,
        market_cap=setup.get('market_cap'), delivered=False))


def admit(cycle, symbol, signal, now, market_cap=None):
    direction = signal['direction']
    identity = key(cycle['id'], symbol, direction)
    # One parent per stock/direction/cycle. An invalidated parent is not recycled.
    if now >= cycle['close'] or cycle['counts'][direction] >= 5 or identity in cycle['setups']:
        return None
    setup = dict(id=identity, symbol=symbol, direction=direction, signal=signal,
        admitted=now, valid=True, closed_checked=False, matches={}, counted=False, market_cap=market_cap)
    cycle['setups'][identity] = setup
    emit(cycle, setup, 'higher', now)
    return setup


def same_pair(a, b):
    return all(a[k] == b[k] for k in ('direction', 'pivot1', 'pivot2'))


def recheck_parent(cycle, setup, signals, now, reason=None):
    """Call only on available, fresh data. Missing data never invalidates."""
    if not setup['valid'] or setup['closed_checked']:
        return
    found = next((s for s in signals if same_pair(s, setup['signal'])), None)
    if found:
        setup['signal'] = found
        setup['closed_checked'] = now >= cycle['close']
        return
    setup['valid'] = False
    emit(cycle, setup, 'invalid', now,
         reason=reason or 'Higher-timeframe pivot pair no longer satisfies the RSI, price-pivot, spacing and Bollinger-touch rules.')
    for match in setup['matches'].values():
        if match['state'] == 'pending':
            match['state'] = 'invalid'


def lower_observation(cycle, setup, frame, candle, signals, now, reason=None):
    """A match is a distinct lower candle, independent of how many P1 pairs qualify."""
    if not setup['valid'] or now >= cycle['expires']:
        return
    identity = key(setup['id'], frame, candle.start)
    match = setup['matches'].get(identity)
    candidates = [s for s in signals if s['pivot2'] == candle.start and s['direction'] == setup['direction']]
    if match:
        if match['state'] != 'pending':
            return
        exact = next((s for s in candidates if same_pair(s, match['signal'])), None)
        if exact is None:
            match['state'] = 'invalid'
            emit(cycle, setup, 'invalid', now, match=match,
                 reason=reason or 'Previously reported lower-timeframe pivot pair no longer satisfies the divergence rules.')
        elif now >= candle.end:
            match.update(state='ready', signal=exact)
            emit(cycle, setup, 'ready', now, match=match)
            if not setup['counted']:
                setup['counted'] = True
                cycle['counts'][setup['direction']] += 1
        return
    if not candidates or not candle.start <= now < candle.end or candle.end <= setup['admitted']:
        return
    if cycle['frame'] == 'daily' and all(v >= 5 for v in cycle['counts'].values()):
        return
    if cycle['frame'] == 'weekly' and sum(m['frame'] == frame for m in setup['matches'].values()) >= 4:
        return
    # Most recent P1 wins deterministically; its exact pair is rechecked at close.
    signal = max(candidates, key=lambda s: s['pivot1'])
    match = dict(id=identity, frame=frame, start=candle.start, end=candle.end,
                 signal=signal, state='pending')
    setup['matches'][identity] = match
    emit(cycle, setup, 'lower', now, match=match)


def message(event):
    from zoneinfo import ZoneInfo
    def stamp(value):
        return datetime.fromtimestamp(value/1000, ZoneInfo('Asia/Jerusalem')).isoformat(timespec='minutes')
    titles = dict(higher='RSI HIGHER TF REACHED', lower='RSI LOWER TF REACHED',
                  ready='STOCK RSI SETUP READY', invalid='RSI STOCK SETUP INVALIDATED')
    s = event['signal']
    lines = [titles[event['kind']], f"{event['symbol']} | {event['direction'].upper()}",
             f"Higher: {event['higher']} — {event['higher_status']}"]
    if event['lower']:
        lines.append(f"Lower: {event['lower']} — {event['lower_status']}")
    if event.get('reason'):
        lines.append(event['reason'])
    lines += [f"P1 {stamp(s['pivot1'])}: close {s['price1']:.4f}, RSI {s['rsi1']:.2f}",
              f"P2 {stamp(s['pivot2'])}: close {s['price2']:.4f}, RSI {s['rsi2']:.2f}",
              'Alpaca SIP, split-adjusted; regular-session higher candles; extended-hours lower candles.',
              f"Observed {stamp(event['observed_at'])} | Setup {event['setup_id']}"]
    if event['kind'] == 'ready':
        lines.append('Lower candle closed with divergence intact; no right-hand pivot confirmation required.')
    return '\n'.join(lines)
