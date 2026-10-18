"""Daily Israel-time market snapshot, delivered by the existing Telegram queue."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from scanner_control import initialize

ISRAEL = ZoneInfo('Asia/Jerusalem')
# Cash indices, USD Bitcoin, and explicitly labelled commodity futures.
INSTRUMENTS = (
    ('^GSPC', 'S&P 500', 'points', 'USD'),
    ('^GDAXI', 'DAX', 'points', 'EUR'),
    ('BTC-USD', 'Bitcoin', 'USD', 'USD'),
    ('GC=F', 'Gold futures', 'USD/troy oz', 'USD'),
    ('CL=F', 'WTI oil futures', 'USD/barrel', 'USD'),
    ('EURUSD=X', 'EUR/USD', 'USD per EUR', 'USD'),
)


def session(now):
    local = datetime.fromtimestamp(now, ISRAEL)
    target = local.replace(hour=10, minute=30, second=0, microsecond=0)
    return local.date().isoformat(), target


def quote(instrument, now):
    symbol, label, unit, currency = instrument
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/'
           + urllib.parse.quote(symbol, safe='') + '?interval=1d&range=5d')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.load(response)
    meta = data['chart']['result'][0]['meta']
    if meta['symbol'] != symbol or meta['currency'] != currency:
        raise ValueError('Unexpected market-summary instrument or currency')
    price, timestamp = meta['regularMarketPrice'], meta['regularMarketTime']
    if (isinstance(price, bool) or not isinstance(price, (int, float))
            or not math.isfinite(price) or price <= 0
            or isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp <= 0 or timestamp > now + 300):
        raise ValueError('Invalid market-summary price or timestamp')
    stamp = datetime.fromtimestamp(timestamp, ISRAEL).strftime('%d %b %H:%M')
    age = ' · older quote' if now - timestamp > 3600 else ''
    precision = 5 if symbol == 'EURUSD=X' else 2
    return f'{label}: {price:,.{precision}f} {unit}\n  As of {stamp}{age}'


def snapshot(now):
    def fetch(instrument):
        try:
            return quote(instrument, now), True
        except Exception:
            # Provider exceptions/URLs must never leak into Telegram or logs.
            return f'{instrument[1]}: unavailable', False

    with ThreadPoolExecutor(max_workers=len(INSTRUMENTS)) as pool:
        rows = list(pool.map(fetch, INSTRUMENTS))
    if not any(available for _, available in rows):
        raise ValueError('All market-summary quotes unavailable; retrying')
    local = datetime.fromtimestamp(now, ISRAEL)
    stamp = local.strftime('%d %b %Y %H:%M')
    greeting = 'Good morning, traders! ☀️\n\n' if local.hour < 12 else ''
    return (greeting + f'Morning market prices · {stamp} (Israel time)\n\n'
            + '\n'.join(text for text, _ in rows)
            + '\n\nSource: Yahoo Finance · quotes may be delayed.'
            + '\nAll quote times are Israel time. Closed markets show the last available quote.'
            + '\nGold/oil use Yahoo-selected futures contracts.')


def enqueue_due(store, now):
    """One durable notification per local day, including same-day restart catch-up."""
    day, target = session(now)
    if now < target.timestamp():
        return False
    key = 'market-summary:' + day
    with store.connect() as db:
        initialize(db)
        if db.execute('SELECT 1 FROM notification_queue WHERE id=?', (key,)).fetchone():
            return False
    text = snapshot(now)
    # If a request crossed midnight, let the next iteration handle the new day.
    if session(time.time())[0] != day:
        return False
    with store.connect() as db:
        initialize(db)
        inserted = db.execute('INSERT OR IGNORE INTO notification_queue(id,text) VALUES(?,?)',
                              (key, text)).rowcount == 1
    return inserted


def worker(store, stop):
    """Independent from long candle scans; stopping uses the app's shared event."""
    while not stop.is_set():
        now = time.time()
        _, target = session(now)
        if now < target.timestamp():
            stop.wait(min(60, target.timestamp() - now))
            continue
        try:
            if enqueue_due(store, now):
                store.log('Morning market summary queued', '10:30 Israel-time market snapshot')
        except Exception:
            store.log('Morning market summary unavailable', 'Could not prepare snapshot; retrying in one minute')
        stop.wait(60)
