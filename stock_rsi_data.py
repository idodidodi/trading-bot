"""Explicit SIP data and ordered US common-stock coverage for RSI watches."""
from datetime import datetime, timedelta, time, timezone
import math
import re
from market_calendar import NY, stock_trading_day
from stock_rsi_watch import millis, MINUTES, data_cutoff


def universe():
    from stock_report_data import membership
    from weekly_stock_screen import universe as indexes
    from fmp_feed import request_json
    sp400, _ = indexes()
    sp500 = membership()
    # Full response, never silently treat an API-truncated universe as complete.
    try:
        rows = request_json('company-screener', country='US', isEtf='false', isFund='false',
                            isActivelyTrading='true', limit=10000)
        if not rows or len(rows) >= 10000 or any(not isinstance(r,dict) for r in rows):
            raise ValueError('US stock universe unavailable or truncated')
    except (OSError, ValueError) as exc:
        # Index membership is independent of optional broad-universe access.
        # Record missing Nasdaq/broad coverage rather than claim zero matches.
        members = sp500['members']
        return dict(tiers=[
            [dict(symbol=s, name=v['name'], market_cap=None, tier=0) for s,v in sorted(members.items())],
            [], [dict(symbol=s, name=s, market_cap=None, tier=2) for s in sorted(set(sp400)-set(members))], []],
            sp500_as_of=sp500['as_of'], sp500_fallback=sp500['fallback'],
            unavailable={'Nasdaq':str(exc), 'broader US mid-cap+':str(exc)})
    tiers = [[], [], [], []]
    seen = set()
    for row in rows:
        symbol = row.get('symbol', '')
        cap = row.get('marketCap')
        name = row.get('companyName') or ''
        if isinstance(cap,bool) or not isinstance(cap,(float,int)) or not math.isfinite(cap):
            cap = None
        if (not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', symbol) or symbol in seen
                or row.get('isEtf') is not False or row.get('isFund') is not False
                or row.get('isActivelyTrading') is not True or row.get('country') != 'US'
                or row.get('exchangeShortName') not in ('NASDAQ', 'NYSE', 'AMEX')
                or re.search(r'\b(warrants?|preferred|depositary shares|units?|rights?|notes|bonds?)\b', name, re.I)):
            continue
        seen.add(symbol)
        index_symbol = symbol.replace('-', '.')
        tier = (0 if index_symbol in sp500['members'] else 1 if row['exchangeShortName'] == 'NASDAQ'
                else 2 if index_symbol in sp400 else 3)
        if tier in (1, 3) and (isinstance(cap, bool) or not isinstance(cap, (float, int))
                                or not math.isfinite(cap) or cap < 2_000_000_000):
            continue
        tiers[tier].append(dict(symbol=index_symbol, name=name, market_cap=cap, tier=tier))
    if len(tiers[0]) < 450:
        raise ValueError('S&P 500 common-stock coverage incomplete')
    return dict(tiers=[sorted(t, key=lambda r:r['symbol']) for t in tiers],
                sp500_as_of=sp500['as_of'], sp500_fallback=sp500['fallback'])


def parent_histories(symbols, cycle, now):
    from stock_report_data import fetch_preclose
    # Freeze at the regular close; later extended-hours data cannot alter it.
    cutoff = min(data_cutoff(now), cycle['close'])
    run = dict(frame=cycle['frame'], cutoff=cutoff,
               period={'start': cycle['period_start']}, feed='sip')
    histories = fetch_preclose(symbols, run)
    for symbol, bars in histories.items():
        if not bars or bars[-1].start != cycle['period_start'] or bars[-1].end != cutoff:
            histories[symbol] = []
    return histories


def request_bars(symbol, timeframe, start, cutoff):
    from alpaca_feed import request_page
    from scanner import Candle, check_candles
    params = dict(symbols=symbol, timeframe=f'{timeframe}Min', feed='sip', adjustment='split',
                  asof='-', sort='asc', limit=10000,
                  start=datetime.fromtimestamp(start/1000, timezone.utc).isoformat(),
                  end=datetime.fromtimestamp(cutoff/1000, timezone.utc).isoformat())
    bars, tokens = [], set()
    for _ in range(30):
        data = request_page(params)
        rows = data.get('bars')
        if not isinstance(rows, dict) or set(rows)-{symbol}:
            raise ValueError('Invalid watch candle response')
        for row in rows.get(symbol, []):
            dt = datetime.fromisoformat(row['t'].replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('Missing watch candle timezone')
            start_ms = millis(dt)
            bars.append(Candle(start_ms, start_ms+timeframe*60_000,
                               *(float(row[k]) for k in ('o', 'h', 'l', 'c'))))
        token = data.get('next_page_token')
        if not token:
            return check_candles(sorted(bars, key=lambda b:b.start), cutoff)
        if not isinstance(token, str) or token in tokens:
            raise ValueError('Watch pagination did not advance')
        tokens.add(token)
        params['page_token'] = token
    raise ValueError('Watch candle history truncated')


def aggregate(bars, frame, cutoff):
    """NY clock-aligned buckets: 4h at 04/08/12/16; no overnight/weekend bars."""
    from scanner import Candle
    groups = {}
    minutes = MINUTES[frame]
    for bar in bars:
        dt = datetime.fromtimestamp(bar.start/1000, NY)
        if not stock_trading_day(dt.date()) or not time(4) <= dt.time() < time(20):
            continue
        minute = (dt.hour*60+dt.minute)//minutes*minutes
        start = datetime.combine(dt.date(), time(minute//60, minute%60), NY)
        groups.setdefault(millis(start), []).append(bar)
    result = []
    for start, members in sorted(groups.items()):
        end = start+minutes*60_000
        # The first requested bucket may be cut off; do not use it for indicators.
        if start < bars[0].start:
            continue
        members.sort(key=lambda b:b.start)
        result.append(Candle(start, end, members[0].open, max(b.high for b in members),
                             min(b.low for b in members), members[-1].close))
    return result


def lower_histories(symbol, now, cache):
    """Native completed 15m bars plus completed minute bars in the forming 15m."""
    cutoff = data_cutoff(now)
    day = datetime.fromtimestamp(now/1000, NY).date().isoformat()
    if cache.get('_session_date') != day:
        cache.clear()  # refresh split adjustments at every New York date boundary
        cache['_session_date'] = day
    cached = cache.get(symbol)
    start = cached[-1].start if cached else cutoff-120*86400_000
    closed = request_bars(symbol, 15, start, cutoff)
    merged = {b.start:b for b in (cached or [])}
    merged.update({b.start:b for b in closed})
    base = sorted(merged.values(), key=lambda b:b.start)[-9000:]
    cache[symbol] = base
    dt = datetime.fromtimestamp(cutoff/1000, NY)
    quarter = dt.replace(minute=dt.minute//15*15, second=0, microsecond=0)
    minutes = request_bars(symbol, 1, millis(quarter), cutoff)
    pieces = [b for b in base if b.end <= millis(quarter)]+minutes
    # Freshness is evaluated per exact candle, including queued closure checks.
    return {frame:aggregate(pieces, frame, cutoff) for frame in MINUTES}, pieces, cutoff


def fresh(candle, pieces, cutoff):
    members = [b for b in pieces if candle.start <= b.start < candle.end]
    return bool(members and members[-1].end >= min(candle.end, cutoff)-2*60_000)
