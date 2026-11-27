"""Durable watch lane inside the existing, exclusively owned signal worker."""
import json
import os
import time
from postgres_store import assert_worker, DatabaseError, RETRYABLE, WorkerOwned
import stock_rsi_watch as watch


def enabled():
    return os.environ.get('STOCK_RSI_WATCH_ENABLED', '').lower() == 'true'


def save(store, cycle):
    assert_worker(store)
    with store.connect() as db:
        db.execute('INSERT INTO stock_rsi_watches(id,expires,payload) VALUES(%s,%s,%s) '
                   'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                   (cycle['id'], cycle['expires'], json.dumps(cycle, allow_nan=False)))


def signals(bars, rules, symbol, frame):
    from scanner import detect, minimum_history
    if len(bars) < max(250, minimum_history(rules)):
        raise ValueError('Insufficient history: 250 candles required')
    return detect(bars, rules, symbol, frame, provisional=True, include_hidden=False)


def evaluate_parent(cycle, symbol, bars):
    if not bars or bars[-1].start != cycle['period_start']:
        raise ValueError('Current higher candle unavailable')
    return [s for s in signals(bars, cycle['rules'], symbol, cycle['frame'])
            if s['pivot2'] == cycle['period_start']]


def invalid_reason(bars, old, rules, label):
    from scanner import indicators
    rsi, lower, upper = indicators(bars, rules)
    positions = {b.start:i for i,b in enumerate(bars)}
    if old['pivot1'] not in positions or old['pivot2'] not in positions:
        return label+': the original pivot pair is absent from available history.'
    first, second = positions[old['pivot1']], positions[old['pivot2']]
    p1,p2 = bars[first].close,bars[second].close
    bullish = old['direction'] == 'bullish'
    failed = []
    if not (p2 < p1 if bullish else p2 > p1):
        failed.append(f'price divergence failed (P1 {p1:.4f}, P2 {p2:.4f})')
    if rsi[first] is not None and rsi[second] is not None:
        tolerance = rules.get('rsi_tolerance',0)
        if not (rsi[second] >= rsi[first]-tolerance if bullish else rsi[second] <= rsi[first]+tolerance):
            failed.append(f'RSI divergence failed (P1 {rsi[first]:.2f}, P2 {rsi[second]:.2f}, tolerance {tolerance})')
    band = lower[second] if bullish else upper[second]
    touch = bars[second].low if bullish else bars[second].high
    if band is None or not (touch <= band if bullish else touch >= band):
        failed.append('P2 no longer touches its Bollinger band')
    neighbours = [b.close for b in bars[max(0,second-rules['pivot_left']):second]]
    if not all(p2 < value if bullish else p2 > value for value in neighbours):
        failed.append('P2 is no longer a strict left-side price pivot')
    return label+': '+('; '.join(failed) or 'original pivot qualification or spacing no longer holds')+'.'


def update_tracking(cycle, now, cache):
    from stock_rsi_data import parent_histories, lower_histories, fresh
    as_of = watch.data_cutoff(now)
    cycle.update(wall_now=now, data_as_of=as_of)
    active = [s for s in cycle['setups'].values() if s['valid']]
    pending_parents = sorted({s['symbol'] for s in active if not s['closed_checked']})
    available = set()
    for offset in range(0, len(pending_parents), 25):
        batch = pending_parents[offset:offset+25]
        try:
            histories = parent_histories(batch, cycle, now)
        except (OSError, ValueError) as exc:
            cycle['coverage']['parent_error'] = str(exc)
            continue
        for setup in active:
            symbol = setup['symbol']
            if symbol not in batch:
                continue
            try:
                found = evaluate_parent(cycle, symbol, histories[symbol])
            except (KeyError, ValueError) as exc:
                cycle['coverage'][symbol] = str(exc)
                continue
            reason = None if any(watch.same_pair(s,setup['signal']) for s in found) else invalid_reason(
                histories[symbol], setup['signal'], cycle['rules'], 'Higher-timeframe setup invalidated')
            watch.recheck_parent(cycle, setup, found, as_of, reason)
            available.add(setup['id'])
    for setup in active:
        if not setup['valid'] or not (setup['closed_checked'] or setup['id'] in available):
            continue
        if (cycle['frame'] == 'daily' and all(v >= 5 for v in cycle['counts'].values())
                and not any(m['state'] == 'pending' for m in setup['matches'].values())):
            continue
        try:
            histories, pieces, cutoff = lower_histories(setup['symbol'], now, cache)
            for frame, bars in histories.items():
                wanted = {m['start'] for m in setup['matches'].values()
                          if m['frame'] == frame and m['state'] == 'pending'}
                if bars:
                    wanted.add(bars[-1].start)
                for index, candle in enumerate(bars):
                    if candle.start not in wanted or not fresh(candle, pieces, cutoff):
                        continue
                    # Lifecycle closure follows available data, not wall time.
                    found = signals(bars[:index+1], cycle['rules'], setup['symbol'], frame)
                    prior = next((m for m in setup['matches'].values() if m['frame']==frame and m['start']==candle.start),None)
                    reason = None
                    if prior and not any(watch.same_pair(s,prior['signal']) for s in found):
                        reason = invalid_reason(bars[:index+1], prior['signal'], cycle['rules'], 'Lower-timeframe setup invalidated')
                    watch.lower_observation(cycle, setup, frame, candle, found, cutoff, reason)
            cycle['coverage'][setup['symbol']] = 'tracked'
        except (OSError, ValueError, KeyError) as exc:
            cycle['coverage'][setup['symbol']] = str(exc)


def discover(cycle, now):
    from stock_rsi_data import universe, parent_histories
    as_of = watch.data_cutoff(now)
    cycle.update(wall_now=now, data_as_of=as_of)
    if as_of >= cycle['close'] or all(v >= 5 for v in cycle['counts'].values()):
        return
    if 'universe' not in cycle:
        cycle['universe'] = universe()
    pool = [row for tier in cycle['universe']['tiers'] for row in tier]
    cursor = cycle['cursor']
    batch = pool[cursor:cursor+50]
    if not batch:
        cycle.update(cursor=0, sweep=cycle['sweep']+1)
        return
    histories = parent_histories([r['symbol'] for r in batch], cycle, now)
    for row in batch:
        symbol = row['symbol']
        try:
            found = evaluate_parent(cycle, symbol, histories[symbol])
            for direction in watch.DIRECTIONS:
                matches = [s for s in found if s['direction'] == direction]
                if matches:
                    watch.admit(cycle, symbol, max(matches, key=lambda s:s['pivot1']), as_of, row['market_cap'])
            cycle['coverage'][symbol] = 'higher match' if found else 'no higher match'
        except (KeyError, ValueError) as exc:
            cycle['coverage'][symbol] = str(exc)
    cycle['cursor'] += len(batch)


def flush(store, cycle, token, now):
    from telegram_delivery import deliver
    from platform_app import send_telegram
    from local_settings import delivery_dry_run
    if delivery_dry_run(store, False):
        return
    for event in cycle['events']:
        if event['delivered']:
            continue
        if now >= cycle['expires']:
            event['delivered'] = 'expired'
            continue
        delivery_clock = watch.data_cutoff(now) if event.get('data_as_of') is not None else now
        if event['kind'] == 'lower' and delivery_clock >= event['lower_end']:
            event['delivered'] = 'expired-before-provisional-delivery'
            continue
        if event['kind'] == 'ready' and not any(e['kind'] == 'lower' and
                e['match_id'] == event['match_id'] and e['delivered'] is True for e in cycle['events']):
            event['delivered'] = 'lower-not-reported'
            continue
        # Maintain stage order through failures/restarts; the ledger handles a
        # crash after Telegram acceptance but before this cycle checkpoint.
        from telegram_group_sound import silent_for_event
        result = deliver(store, token, event['chat'], watch.message(event, now),
                         'stock-rsi-watch:'+event['id'], send_telegram,
                         silent=silent_for_event(store, event))
        if result not in ('sent', 'suppressed'):
            break
        event['delivered'] = True
        save(store, cycle)
    save(store, cycle)


def tick(store, rules, token, now, cache, clock=None):
    assert_worker(store)
    for spec in watch.cycles(now):
        with store.connect() as db:
            db.require_table('stock_rsi_watches')
            row = db.execute('SELECT payload FROM stock_rsi_watches WHERE id=%s', (spec['id'],)).fetchone()
        cycle = json.loads(row[0]) if row else watch.new_cycle(spec, rules)
        if now >= cycle['next_scan']:
            update_tracking(cycle, now, cache)
            try:
                discover(cycle, now)
                cycle['coverage'].pop('discovery_error', None)
            except (OSError, ValueError) as exc:
                cycle['coverage']['discovery_error'] = str(exc)
            cycle['next_scan'] = now+30_000
            save(store, cycle)  # commit lifecycle and outbox before external effects
        try:
            flush(store, cycle, token, clock() if clock else now)
            cycle['coverage'].pop('delivery_error',None)
        except WorkerOwned:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            # One destination failure must not starve the overlapping cycle.
            cycle['coverage']['delivery_error'] = type(exc).__name__
            save(store, cycle)


def worker(store, rules, token, dry_run, stop):
    if not enabled() or dry_run:
        return
    cache = {}
    last_error = None
    while not stop.is_set():
        try:
            tick(store, rules, token, int(time.time()*1000), cache, clock=lambda:int(time.time()*1000))
            last_error = None
        except (WorkerOwned, DatabaseError, *RETRYABLE):
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            name = type(exc).__name__
            if name != last_error:
                store.log('Stock RSI watch unavailable', name+'; inspect watch coverage and feed access')
            last_error = name
        if not watch.cycles(int(time.time()*1000)):
            cache.clear()
        stop.wait(30)
