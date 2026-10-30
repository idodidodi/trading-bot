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


def fetch_batch(symbols, frame, cutoff, history_bars, feed):
    """Use bounded native-bar requests; don't inherit the weekly screen's 800-day cap."""
    import math
    from datetime import timedelta
    from alpaca_feed import request_page, FRAMES as provider_frames
    from scanner import Candle, check_candles
    from market_calendar import stock_candle_end
    # Four-hour requests use a conservative two regular-session bars/day estimate.
    days_per_bar = {'monthly': 31, 'weekly': 7, 'daily': 1.45, '4h': .73}[frame]
    start = datetime.fromtimestamp(cutoff / 1000, timezone.utc) - timedelta(days=math.ceil(history_bars * days_per_bar) + 30)
    params = dict(symbols=','.join(symbols), timeframe=provider_frames[frame], feed=feed,
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
                    end = stock_candle_end(dt, frame)
                    histories[symbol].append(Candle(int(dt.timestamp()*1000), int(end.timestamp()*1000),
                                                   *(float(row[k]) for k in ('o', 'h', 'l', 'c'))))
            except (KeyError,TypeError,ValueError,OverflowError,AttributeError):
                raise ValueError('Invalid provider candle data') from None
        token = response.get('next_page_token')
        if not token:
            return {s: check_candles(sorted(bars, key=lambda b: b.start), cutoff) for s, bars in histories.items()}
        if not isinstance(token, str) or token in tokens:
            raise ValueError('Provider pagination did not advance')
        tokens.add(token)
        params['page_token'] = token
    raise ValueError('Provider exceeded 1000 pages for one batch; reduce --batch-size')
