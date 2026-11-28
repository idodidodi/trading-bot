"""Membership metadata and sufficiently deep native candles for stock reports."""
from datetime import datetime, timezone
import html
import json
import re
import ssl
import urllib.request
from platform_app import ROOT

UNIVERSE_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'


def parse_members(page):
    table = re.search(r'<table\b[^>]*\bid=[\"\']constituents[\"\'][^>]*>.*?</table>', page, re.S)
    if not table:
        raise ValueError('Constituents table unavailable')
    members = {}
    for row in re.findall(r'<tr\b.*?</tr>', table.group(), re.S):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 3:
            continue
        values = [' '.join(html.unescape(re.sub('<[^>]+>', '', cell)).split()) for cell in cells[:3]]
        symbol, name, sector = values
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', symbol) or not name or not sector:
            raise ValueError('Invalid constituent metadata')
        members[symbol] = dict(name=name, sector=sector)
    if not 490 <= len(members) <= 520:
        raise ValueError('Unexpected constituent count')
    return members


def membership(previous=None):
    import certifi
    try:
        request = urllib.request.Request(UNIVERSE_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=certifi.where()), timeout=30) as response:
            members = parse_members(response.read().decode())
        return dict(members=members, source=UNIVERSE_URL, as_of=datetime.now(timezone.utc).isoformat(), fallback=False)
    except (OSError, ValueError, AttributeError):
        if previous and previous.get('members'):
            return previous | dict(fallback=True)
        data = json.loads((ROOT/'stock_universe.json').read_text())
        return dict(members={s:dict(name='Unavailable', sector='Unavailable') for s in data['indexes']['sp500']},
                    source=data['source'], as_of=data['as_of'], fallback=True)


def fetch_batch(symbols, frame, cutoff, history_bars, feed, *, start_ms=None):
    """Use bounded native-bar requests; don't inherit the weekly screen's 800-day cap."""
    import math
    from datetime import timedelta
    from alpaca_feed import request_page, FRAMES as provider_frames
    from scanner import Candle, check_candles
    from market_calendar import stock_candle_end
    # Four-hour requests use a conservative two regular-session bars/day estimate.
    days_per_bar = {'monthly': 31, 'weekly': 7, 'daily': 1.45, '4h': .73, '1min': 1/390}[frame]
    start = datetime.fromtimestamp(cutoff / 1000, timezone.utc) - timedelta(days=math.ceil(history_bars * days_per_bar) + 30)
    if start_ms is not None:
        start = datetime.fromtimestamp(start_ms / 1000, timezone.utc)
    params = dict(symbols=','.join(symbols), timeframe='1Min' if frame == '1min' else '30Min' if frame == '4h' else provider_frames[frame], feed=feed,
                  adjustment='split', asof='-', sort='asc', limit=10000,
                  start=start.isoformat(), end=datetime.fromtimestamp(cutoff / 1000, timezone.utc).isoformat())
    histories = {s: [] for s in symbols}
    tokens = set()
    for _ in range(1000):
        response = request_page(params)
        bars = response.get('bars') if isinstance(response, dict) else None
        if not isinstance(bars, dict) or set(bars) - set(symbols):
            raise ValueError('Provider returned invalid or unexpected symbols')
        for symbol, rows in bars.items():
            if not isinstance(rows, list):
                raise ValueError('Invalid provider candle response')
            try:
                for row in rows:
                    dt = datetime.fromisoformat(row['t'].replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        raise ValueError('Provider timestamp has no timezone')
                    end = dt+timedelta(minutes=1 if frame == '1min' else 30) if frame in ('1min', '4h') else stock_candle_end(dt, frame)
                    histories[symbol].append(Candle(int(dt.timestamp()*1000), int(end.timestamp()*1000),
                                                   *(float(row[k]) for k in ('o', 'h', 'l', 'c'))))
            except (KeyError,TypeError,ValueError,OverflowError,AttributeError):
                raise ValueError('Invalid provider candle data') from None
        token = response.get('next_page_token')
        if not token:
            if frame == '4h':
                from stock_rth import aggregate
                return {s: aggregate(bars, frame, cutoff) for s, bars in histories.items()}
            return {s: check_candles(sorted(bars, key=lambda b: b.start), cutoff) for s, bars in histories.items()}
        if not isinstance(token, str) or token in tokens:
            raise ValueError('Provider pagination did not advance')
        tokens.add(token)
        params['page_token'] = token
    raise ValueError('Provider exceeded 1000 pages for one batch; reduce --batch-size')


def fetch_preclose(symbols, run):
    """Freeze the forming candle at the scheduled instant, even on later retries.

    Never use a provider's still-forming daily/weekly/monthly aggregate: a retry
    could return trades after the snapshot. Build today's portion from minutes.
    """
    from datetime import timedelta, time
    from market_calendar import NY, stock_trading_day
    from scanner import Candle, check_candles
    cutoff = run['cutoff']
    start = run['period']['start']
    day = datetime.fromtimestamp(cutoff/1000, NY).date()
    opening = int(datetime.combine(day, time(9,30), NY).timestamp()*1000)
    histories = fetch_batch(symbols, run['frame'], cutoff, 250, run['feed'])
    daily = fetch_batch(symbols, 'daily', cutoff, 35, run['feed'], start_ms=start)
    minutes = fetch_batch(symbols, '1min', cutoff, 390, run['feed'], start_ms=opening)
    required = set()
    cursor = datetime.fromtimestamp(start/1000, NY).date()
    while cursor < day:
        if stock_trading_day(cursor):
            required.add(cursor)
        cursor += timedelta(days=1)
    for symbol in symbols:
        past = [b for b in daily[symbol] if start <= b.start < opening and b.end < opening]
        today = [b for b in minutes[symbol] if opening <= b.start and b.end <= cutoff]
        dates = {datetime.fromtimestamp(b.start/1000, NY).date() for b in past}
        # Never present stale or truncated current-period data as a no-match.
        if dates != required or not today or today[-1].end < cutoff-5*60_000:
            continue
        pieces = past+today
        partial = Candle(start, cutoff, pieces[0].open, max(b.high for b in pieces),
                         min(b.low for b in pieces), pieces[-1].close)
        histories[symbol] = check_candles([b for b in histories[symbol] if b.end < start]+[partial], cutoff)
    return histories


def market_caps(symbols):
    """Best-effort USD metadata for PDFs only; never a signal eligibility filter."""
    import math
    import os
    from fmp_feed import request_json
    snapshot = dict(source='FMP', retrieved_at=datetime.now(timezone.utc).isoformat(), values={})
    symbols = sorted(set(symbols))
    if not symbols or not os.environ.get('FMP_API_KEY', '').strip():
        return snapshot
    # FMP uses hyphens for share classes; report tickers retain their spelling.
    aliases = {symbol.replace('.', '-'): symbol for symbol in symbols}
    requested = sorted(aliases)
    for offset in range(0, len(requested), 100):
        batch = requested[offset:offset+100]
        try:
            rows = request_json('market-capitalization-batch', symbols=','.join(batch))
        except (OSError, ValueError):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get('symbol') not in batch:
                continue
            value = row.get('marketCap')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                continue
            provider_date = row.get('date')
            try:
                provider_date = datetime.strptime(provider_date, '%Y-%m-%d').date().isoformat()
            except (TypeError, ValueError):
                provider_date = None
            snapshot['values'][aliases[row['symbol']]] = dict(usd=value, as_of=provider_date)
    return snapshot
