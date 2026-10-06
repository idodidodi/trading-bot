"""Independent OHLC scanner; no TradingView account, webhook, or Pine required."""
import argparse
import calendar
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
import urllib.error

from platform_app import ROOT, Store, load_env, load_rules, validate_signal

TIMEFRAMES = {'4h': '4h', 'daily': '1day', 'weekly': '1week', 'monthly': '1month'}


def minimum_history(rules):
    return max(rules['bb_period'] + rules['pivot_left'] + rules['pivot_right'],
               rules['rsi_period'] + 1, rules['max_spacing'] + rules['pivot_left'] + rules['pivot_right'] + 1)


@dataclass(frozen=True)
class Candle:
    start: int
    end: int
    open: float
    high: float
    low: float
    close: float


class ProviderCandles(list):
    """Candle data with visible provider-normalization notes."""
    def __init__(self, candles, notes):
        super().__init__(candles)
        self.notes = notes


def normalize_provider_candles(candles):
    # APIs can repeat a timestamp with a corrected close. Retain the last
    # revision in the ordered response, just as the persistent cache does.
    unique = {c.start: c for c in candles}
    ordered = sorted(unique.values(), key=lambda c: c.start)
    revisions = len(candles) - len(ordered)
    shortened = 0
    for i in range(len(ordered) - 1):
        # The next native bar start is a stronger boundary than our nominal
        # duration (provider sessions/DST can produce a short final bucket).
        if ordered[i].end > ordered[i + 1].start:
            ordered[i] = replace(ordered[i], end=ordered[i + 1].start)
            shortened += 1
    notes = []
    if revisions:
        notes.append(f'{revisions} provider candle revision(s): last returned version used')
    if shortened:
        notes.append(f'{shortened} shortened provider interval(s): closed at next native bar start')
    return ProviderCandles(ordered, notes)


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Candle timestamps must include a timezone')
    return int(dt.timestamp() * 1000)


def check_candles(candles, now):
    candles = sorted(candles, key=lambda c: c.start)
    if len({c.start for c in candles}) != len(candles):
        raise ValueError('Duplicate candle timestamps')
    previous_end = 0
    for c in candles:
        if c.start <= 0 or c.end <= c.start or c.start < previous_end:
            raise ValueError('Invalid/overlapping candle timestamps')
        if not all(math.isfinite(v) for v in (c.open, c.high, c.low, c.close)):
            raise ValueError('Invalid candle price')
        if not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high:
            raise ValueError('Invalid OHLC ordering')
        previous_end = c.end
    return [c for c in candles if c.end <= now]


def indicators(candles, rules):
    n, b = rules['rsi_period'], rules['bb_period']
    closes = [c.close for c in candles]
    rsi, lower, upper = [None] * len(closes), [None] * len(closes), [None] * len(closes)
    gain = loss = 0.0
    for i, close in enumerate(closes):
        if i > 0:
            change = close - closes[i - 1]
            g, l = max(change, 0), max(-change, 0)
            if i <= n:
                gain += g / n
                loss += l / n
            else:
                gain = (gain * (n - 1) + g) / n
                loss = (loss * (n - 1) + l) / n
            if i >= n:
                rsi[i] = 50 if gain == loss == 0 else 100 if loss == 0 else 0 if gain == 0 else 100 - 100 / (1 + gain / loss)
        if i >= b - 1:
            window = closes[i - b + 1:i + 1]
            mean = sum(window) / b
            sigma = math.sqrt(sum((v - mean) ** 2 for v in window) / b)
            lower[i] = mean - rules['bb_multiplier'] * sigma
            upper[i] = mean + rules['bb_multiplier'] * sigma
    return rsi, lower, upper


def detect(candles, rules, symbol, timeframe, *, provisional=False):
    rsi, lower, upper = indicators(candles, rules)
    left, right = rules['pivot_left'], rules['pivot_right']
    signals = []
    for direction in ('bullish', 'bearish'):
        previous = None
        prices = [c.low if direction == 'bullish' else c.high for c in candles]
        bands = lower if direction == 'bullish' else upper
        for i in range(left, len(candles) if provisional else len(candles) - right):
            if provisional:
                # Only promote a first pivot after its right-hand candles have
                # closed. Candidate P2 never reads candles after its own close.
                j = i - right
                if j >= left:
                    neighbours = prices[j - left:j] + prices[j + 1:i + 1]
                    confirmed = all(prices[j] < v for v in neighbours) if direction == 'bullish' else all(prices[j] > v for v in neighbours)
                    if confirmed:
                        previous = j
            neighbours = prices[i - left:i] + ([] if provisional else prices[i + 1:i + right + 1])
            pivot = all(prices[i] < v for v in neighbours) if direction == 'bullish' else all(prices[i] > v for v in neighbours)
            if not pivot:
                continue
            first = previous
            if not provisional:
                previous = i
            # Remember every pivot, including those in indicator warm-up.
            if first is None or any(v is None for v in (rsi[first], rsi[i], bands[first], bands[i])):
                continue
            spacing = i - first
            if not rules['min_spacing'] <= spacing <= rules['max_spacing']:
                continue
            valid = (prices[i] < prices[first] and rsi[i] > rsi[first] and prices[i] <= bands[i]) if direction == 'bullish' else (prices[i] > prices[first] and rsi[i] < rsi[first] and prices[i] >= bands[i])
            event_index = i if provisional else i + right
            if not valid or event_index < minimum_history(rules) - 1:
                continue
            signals.append(dict(symbol=symbol, timeframe=timeframe, direction=direction,
                                price1=prices[first], price2=prices[i], rsi1=rsi[first], rsi2=rsi[i],
                                band1=bands[first], band2=bands[i], pivot1=candles[first].start,
                                pivot2=candles[i].start, confirmed_at=candles[event_index].end,
                                spacing=spacing, rules=rules))
            if provisional:
                # confirmed_at is the legacy event-time storage field. Explicit
                # timing metadata prevents presenting it as pivot confirmation.
                signals[-1].update(signal_status='provisional', alert_timing='pivot_close',
                                   pivot_closed_at=candles[i].end)
    return sorted(signals, key=lambda s: s['confirmed_at'])


def read_csv(asset, timeframe):
    path = Path(asset['path'].format(asset=asset['id'], timeframe=timeframe))
    if not path.is_absolute():
        path = ROOT / path
    with path.open(newline='') as source:
        rows = csv.DictReader(source)
        return [Candle(timestamp(r['timestamp']), timestamp(r['closed_at']),
                       *(float(r[k]) for k in ('open', 'high', 'low', 'close'))) for r in rows]


def interval_end(start, timeframe):
    if timeframe == 'monthly':
        month = start.month % 12 + 1
        year = start.year + (start.month == 12)
        return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))
    return start + {'4h': timedelta(hours=4), 'daily': timedelta(days=1), 'weekly': timedelta(days=7)}[timeframe]


def fetch_twelve_data(asset, timeframe):
    key = os.environ.get('TWELVE_DATA_API_KEY')
    if not key:
        raise ValueError('TWELVE_DATA_API_KEY is missing')
    if not asset.get('symbol') or not asset.get('feed_confirmed'):
        raise ValueError('Confirm exact provider symbol/feed in scanner.json first')
    params = dict(symbol=asset['symbol'], interval=TIMEFRAMES[timeframe], outputsize=500,
                  timezone='UTC', order='ASC', apikey=key)
    if asset.get('exchange'):
        params['exchange'] = asset['exchange']
    # Errors are sanitized by scan_once; never print the URL containing the API key.
    url = 'https://api.twelvedata.com/time_series?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise ValueError(provider_error(exc.code)) from None
    if data.get('status') == 'error' or not isinstance(data.get('values'), list):
        raise ValueError(provider_error(data.get('code')))
    meta = data.get('meta', {})
    if meta.get('interval') != TIMEFRAMES[timeframe] or meta.get('symbol') != asset['symbol']:
        raise ValueError('Provider returned a different symbol or interval')
    if asset.get('exchange') and meta.get('exchange') != asset['exchange']:
        raise ValueError('Provider returned a different exchange')
    candles = []
    for row in data['values']:
        start = datetime.fromisoformat(row['datetime']).replace(tzinfo=timezone.utc)
        # Conservative calendar cutoff, NOT a claim about the exchange's closing bell.
        end = interval_end(start, timeframe)
        candles.append(Candle(int(start.timestamp() * 1000), int(end.timestamp() * 1000),
                              *(float(row[k]) for k in ('open', 'high', 'low', 'close'))))
    return normalize_provider_candles(candles)


def provider_error(code):
    return {
        401: 'Twelve Data API key is invalid',
        403: 'Twelve Data plan does not include this feed',
        404: 'Twelve Data symbol is unavailable; configure a supported exact feed in Assets',
        429: 'Twelve Data quota reached; waiting for the next scheduled scan',
    }.get(code, 'Provider rejected request; check symbol, entitlement, quota, and API key')


def scan_once(store, config, rules, now=None, stop=None):
    from managed_assets import effective, cache_candles
    config, config_revision = effective(store, config)
    now = int(time.time() * 1000) if now is None else now
    from daily_universe import resolve
    config, selection = resolve(store, config, now)
    store.log('Scan cycle started', f'{len(config["assets"])} assets')
    coverage = []
    if selection and selection['status'] == 'unavailable':
        coverage.append(dict(asset='DAILY_SELECTION', timeframe='daily', provider='Kraken', status='unavailable', reason=selection['reason']))
    last_request = None
    for asset in config['assets']:
        if not asset.get('enabled', True):
            continue
        for timeframe in asset.get('timeframes', config['timeframes']):
            row = dict(asset=asset['id'], timeframe=timeframe, provider=asset['provider'])
            try:
                if asset['provider'] == 'twelvedata':
                    if last_request is not None:
                        delay = max(0, float(config.get('request_spacing_seconds', 8)) - (time.monotonic() - last_request))
                        if stop is not None:
                            if stop.wait(delay):
                                return coverage
                        else:
                            time.sleep(delay)
                    if stop is not None and stop.is_set():
                        return coverage
                    last_request = time.monotonic()
                raw = read_csv(asset, timeframe) if asset['provider'] == 'csv' else fetch_twelve_data(asset, timeframe)
                if getattr(raw, 'notes', None):
                    row['data_notes'] = '; '.join(raw.notes)
                candles = check_candles(raw, now)
                future_closes = [c.end for c in raw if c.end > now]
                if future_closes:
                    row['next_close_at'] = min(future_closes)
                elif candles and timeframe == '4h':
                    # Preserve the feed's native four-hour alignment.
                    period = 4 * 60 * 60 * 1000
                    row['next_close_at'] = candles[-1].end + ((now - candles[-1].end) // period + 1) * period
                cache_candles(store, asset, timeframe, candles)
                if len(candles) < minimum_history(rules):
                    row.update(status='insufficient history', candles=len(candles))
                else:
                    # Optional continuous-market validation (e.g. a verified 24/7 crypto feed).
                    if asset.get('continuous') and any(a.end != b.start for a, b in zip(candles, candles[1:])):
                        raise ValueError('Missing candles in continuous-market feed')
                    source_id = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
                    state_id = f"{asset['id']}@{source_id}@{rules['skill_hash']}@all-stages-v2"
                    matches = detect(candles, rules, source_id, timeframe) + detect(candles, rules, source_id, timeframe, provisional=True)
                    with store.connect() as db:
                        state = db.execute('SELECT last_end FROM scan_state WHERE asset=? AND timeframe=?', (state_id, timeframe)).fetchone()
                    # First run establishes a baseline without flooding Telegram with history.
                    cutoff = state[0] if state else candles[-1].end
                    if asset.get('daily_selected') and selection:
                        cutoff = max(cutoff, selection['starts_at'])
                    if state and cutoff < candles[0].start:
                        raise ValueError('Polling gap exceeds available history; supply candle backfill before resuming')
                    age_seconds = max(0, (now - candles[-1].end) // 1000)
                    if asset.get('max_data_age_seconds') and age_seconds > asset['max_data_age_seconds']:
                        raise ValueError('Latest closed candle is older than the configured freshness limit')
                    pending = sorted((s for s in matches if s['confirmed_at'] > cutoff), key=lambda s:(s['confirmed_at'],s.get('signal_status')!='provisional'))
                    new = sum(store.insert(validate_signal(s, rules)) for s in pending)
                    with store.connect() as db:
                        db.execute('INSERT INTO scan_state(asset,timeframe,last_end) VALUES(?,?,?) ON CONFLICT(asset,timeframe) DO UPDATE SET last_end=MAX(last_end,excluded.last_end)',
                                   (state_id, timeframe, candles[-1].end))
                    row.update(status='scanned' if state else 'baseline set', candles=len(candles), last_closed_at=candles[-1].end,
                               new_signals=new, historical_matches=len(matches), limited_history=len(candles) < 250,
                               last_closed_age_seconds=age_seconds)
            except (ValueError, FileNotFoundError) as exc:
                # Only local, known ValueErrors are shown; URLs and provider payloads are never logged.
                reason = asset.get('unavailable_reason', 'Candle CSV missing; configure a live provider in Assets') if isinstance(exc, FileNotFoundError) else str(exc)
                row.update(status='unavailable', reason=reason)
            except Exception:
                row.update(status='unavailable', reason='Data request or parsing failed; check provider access and format')
            coverage.append(row)
            store.log('Market check', json.dumps(row))
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO scanner_status(id,payload) VALUES(1,?)', (json.dumps(dict(scanned_at=now, coverage=coverage)),))
    if config_revision is not None:
        with store.connect() as db:
            db.execute('UPDATE managed_config SET applied_revision=? WHERE id=1', (config_revision,))
    store.log('Scan cycle completed', f'{len(coverage)} market checks')
    return coverage


def prepare_store(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS scan_state(asset TEXT,timeframe TEXT,last_end INTEGER,PRIMARY KEY(asset,timeframe))')
        db.execute('CREATE TABLE IF NOT EXISTS scanner_status(id INTEGER PRIMARY KEY,payload TEXT)')


def load_config():
    path = Path(os.environ.get('SCANNER_CONFIG', str(ROOT / 'scanner.json')))
    config = json.loads(path.read_text())
    if not config.get('assets') or not config.get('timeframes') or not set(config['timeframes']) <= TIMEFRAMES.keys():
        raise ValueError('Set assets and supported timeframes in scanner.json')
    ids = [a['id'] for a in config['assets']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate asset IDs')
    if any(a.get('provider') not in ('csv', 'twelvedata') for a in config['assets']):
        raise ValueError('Provider must be csv or twelvedata')
    return config


def scanner_worker(store, config, rules, stop):
    prepare_store(store)
    from managed_assets import ensure
    ensure(store, config)
    while not stop.is_set():
        with store.connect() as db:
            previous = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
            if previous:
                progress = json.loads(previous[0])
                progress.update(phase='scanning', next_scan_at=None)
                db.execute('UPDATE scanner_status SET payload=? WHERE id=1', (json.dumps(progress),))
        coverage = scan_once(store, config, rules, stop=stop)
        if stop.is_set():
            break
        from managed_assets import effective
        current, _ = effective(store, config)
        delay = max(30, current.get('poll_seconds', 300))
        closes = [r['next_close_at'] / 1000 for r in (coverage or [])
                  if r.get('next_close_at') and r.get('status') in ('scanned', 'baseline set')]
        if closes:
            delay = min(delay, max(30, min(closes) + 15 - time.time()))
        policy = current.get('daily_universe', {})
        if policy.get('enabled'):
            from daily_universe import seconds_to_refresh, status, session
            clock_now = time.time()
            delay = min(delay, seconds_to_refresh(policy, clock_now))
            selected = status(store)
            if selected and selected['session'] != session(policy, clock_now * 1000)[0]:
                delay = 1  # A long scan crossed the morning boundary.
        with store.connect() as db:
            saved = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
            schedule = json.loads(saved[0]) if saved else dict(scanned_at=int(time.time() * 1000), coverage=coverage or [])
            schedule.update(phase='waiting', next_scan_at=int((time.time() + delay) * 1000))
            db.execute('INSERT OR REPLACE INTO scanner_status(id,payload) VALUES(1,?)', (json.dumps(schedule),))
        stop.wait(delay)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Scan once without starting Telegram delivery')
    parser.parse_args()
    load_env()
    data = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    data.mkdir(parents=True, exist_ok=True)
    store = Store(data / 'signals.sqlite3')
    prepare_store(store)
    print(json.dumps(scan_once(store, load_config(), load_rules()), indent=2))
