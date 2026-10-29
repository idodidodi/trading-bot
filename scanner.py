"""Independent OHLC scanner; no TradingView account, webhook, or Pine required."""
from postgres_store import RETRYABLE, DatabaseError
import argparse
import calendar
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from postgres_store import DatabaseError, OperationalError
import sys
import threading
import time
from zoneinfo import ZoneInfo
import urllib.parse
import urllib.request
import urllib.error

from platform_app import ROOT, Store, database_busy, load_env, load_rules, validate_signal, rsi_divergence_matches

TIMEFRAMES = {'1h': '1h', '4h': '4h', 'daily': '1day', 'weekly': '1week', 'monthly': '1month'}


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


def valid_ohlc(candle):
    return (all(math.isfinite(v) for v in (candle.open, candle.high, candle.low, candle.close))
            and candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high)


def usable_provider_history(candles, now, required):
    """Quarantine a corrupt prefix, never stitch across a bad candle or invent prices."""
    invalid = [i for i, candle in enumerate(candles) if not valid_ohlc(candle)]
    if not invalid:
        return candles
    last = invalid[-1]
    date = datetime.fromtimestamp(candles[last].start / 1000, timezone.utc).isoformat()
    remaining = candles[last + 1:]
    closed = sum(c.end <= now for c in remaining)
    if closed < required:
        raise ValueError(f'Invalid provider OHLC at {date}; only {closed} valid closed candles '
                         f'follow it, need {required}. Provider correction or backfill required')
    # Validate time boundaries even in the quarantined prefix. This recovery is
    # specifically for bad prices, not malformed/overlapping candle intervals.
    check_candles([replace(c, open=1, high=1, low=1, close=1) for c in candles], now)
    notes = list(getattr(candles, 'notes', []))
    notes.append(f'Invalid provider OHLC at {date}: excluded {last + 1} prefix candles '
                 f'({len(invalid)} invalid); using {closed} consecutive valid closed candles')
    return ProviderCandles(remaining, notes)


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
        references = []
        prices = [c.close for c in candles]
        bands = lower if direction == 'bullish' else upper

        def is_pivot(index, end):
            neighbours = prices[index - left:index] + prices[index + 1:end]
            return all(prices[index] < v for v in neighbours) if direction == 'bullish' else all(prices[index] > v for v in neighbours)

        for i in range(left, len(candles) if provisional else len(candles) - right):
            if provisional:
                # P1 must already be confirmed at P2's close; never read ahead.
                j = i - right
                if j >= left and is_pivot(j, i + 1):
                    references.append(j)
            references = [j for j in references if i - j <= rules['max_spacing']]
            if not is_pivot(i, i + 1 if provisional else i + right + 1):
                continue
            touch = candles[i].low if direction == 'bullish' else candles[i].high
            band_touch = bands[i] is not None and (touch <= bands[i] if direction == 'bullish' else touch >= bands[i])
            event_index = i if provisional else i + right
            for first in references:
                spacing = i - first
                if not rules['min_spacing'] <= spacing <= rules['max_spacing']:
                    continue
                if any(v is None for v in (rsi[first], rsi[i], bands[first], bands[i])):
                    continue
                price_divergence = prices[i] < prices[first] if direction == 'bullish' else prices[i] > prices[first]
                divergence = price_divergence and rsi_divergence_matches(
                    rsi[first], rsi[i], direction, rules.get('rsi_tolerance', 0))
                if not divergence or not band_touch or event_index < minimum_history(rules) - 1:
                    continue
                signal = dict(symbol=symbol, timeframe=timeframe, direction=direction,
                              price1=prices[first], price2=prices[i], rsi1=rsi[first], rsi2=rsi[i],
                              band1=bands[first], band2=bands[i], band_touch_price=touch,
                              pivot1=candles[first].start, pivot2=candles[i].start,
                              pivot1_closed_at=candles[first].end, pivot2_closed_at=candles[i].end,
                              confirmed_at=candles[event_index].end, spacing=spacing, rules=rules)
                if provisional:
                    signal.update(signal_status='provisional', alert_timing='pivot_close',
                                  pivot_closed_at=candles[i].end)
                signals.append(signal)
            if not provisional:
                references.append(i)
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
    return start + {'1h': timedelta(hours=1), '4h': timedelta(hours=4), 'daily': timedelta(days=1), 'weekly': timedelta(days=7)}[timeframe]


class TwelveDataRateLimit(ValueError):
    """Only a provider HTTP/payload 429 may trigger a fallback."""


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
        raise (TwelveDataRateLimit if exc.code == 429 else ValueError)(provider_error(exc.code)) from None
    if data.get('status') == 'error' or not isinstance(data.get('values'), list):
        raise (TwelveDataRateLimit if str(data.get('code')) == '429' else ValueError)(provider_error(int(data['code']) if str(data.get('code', '')).isdigit() else None))
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


def scan_once(store, config, rules, now=None, stop=None, catchup=False, run_momentum=False):
    from managed_assets import effective, cache_candles, for_timeframe
    import pin_bars
    import bb_pin_bars
    import rsi_pb
    pin_rules = pin_bars.load_rules()
    bb_rules = bb_pin_bars.load_rules()
    combined_rules = rsi_pb.load_rules(rules)
    from kraken_feed import fetch_kraken
    from oanda_feed import fetch_oanda
    from fmp_feed import fetch_fmp
    from yahoo_feed import fetch_yahoo
    from tiingo_feed import fetch_tiingo
    from alpaca_feed import fetch_alpaca
    config, config_revision = effective(store, config)
    configured = config
    now = int(time.time() * 1000) if now is None else now
    from weekly_stock_screen import resolve as resolve_weekly_stocks
    config, stock_selection = resolve_weekly_stocks(store, config, rules, now)
    from daily_universe import resolve
    config, selection = resolve(store, config, now)
    config = rsi_pb.retain_pending(store, config, configured,combined_rules['version'])
    # Independent daily strategy: RSI rules and alert state remain separate.
    from momentum import scan as scan_momentum
    if run_momentum:
        try:
            scan_momentum(store, config, now, stop=stop)
        except (DatabaseError, *RETRYABLE):
            raise
        except Exception:
            store.log('Momentum scan failed', 'Daily strategy unavailable; check provider access and diagnostics')
    if catchup:
        # Recover the last monitored rotating feeds as well as today's picks.
        # Offline days have no recorded selection; never invent those picks.
        with store.connect() as db:
            prior=db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
        known={a['id'] for a in config['assets']}
        for old in json.loads(prior[0]).get('coverage',[]) if prior else []:
            if old['asset'] in known or old['asset'] in ('USDCNY','DAILY_SELECTION','WEEKLY_STOCK_SCREEN'):continue
            if old.get('provider') not in ('twelvedata','kraken','oanda','tiingo','alpaca','fmp','yahoo'):continue
            config['assets'].append(dict(id=old['asset'],provider=old['provider'],symbol=old.get('symbol',old['asset']),
                exchange=old.get('exchange',''),enabled=True,feed_confirmed=True,timeframes=[old['timeframe']],
                catchup_only=True,tradingview_symbol='',market=old.get('market') or ('stock' if old['asset'].startswith('STOCK-') else None)))
            # Another timeframe from the same old feed must be appended too.
        merged={}
        for a in config['assets']:
            if a['id'] in merged and a.get('catchup_only'):
                merged[a['id']]['timeframes']=list(dict.fromkeys(merged[a['id']]['timeframes']+a['timeframes']))
            else:merged[a['id']]=a
        config['assets']=list(merged.values())
    enabled=[a for a in config['assets'] if a.get('enabled',True) and a['id'].replace('/','').upper()!='USDCNY']; configured=len(config['assets']); permanent=sum(not a.get('daily_selected') and not a.get('weekly_screen_selected') for a in enabled); rotating_stocks=sum(bool(a.get('weekly_screen_selected')) for a in enabled); rotating_pairs=sum(bool(a.get('daily_selected')) for a in enabled)
    store.log('Scan cycle started', f'{len(enabled)} enabled assets ({permanent} permanent, {rotating_stocks} rotating stocks, {rotating_pairs} rotating forex/crypto); {configured-len(enabled)} disabled/excluded entries')
    coverage = []
    if selection and selection['status'] == 'unavailable':
        coverage.append(dict(asset='DAILY_SELECTION', timeframe='daily', provider='Kraken', status='unavailable', reason=selection['reason']))
    if stock_selection and stock_selection['status'] in ('unavailable','partial'):
        coverage.append(dict(asset='WEEKLY_STOCK_SCREEN', timeframe='weekly', provider='Alpaca',
                             status=stock_selection['status'],reason=stock_selection.get('reason','')))
    last_requests = {}
    for configured_asset in config['assets']:
        if not configured_asset.get('enabled', True) or configured_asset['id'].replace('/','').upper()=='USDCNY':
            continue
        for timeframe in configured_asset.get('timeframes', config['timeframes']):
            asset = for_timeframe(configured_asset, timeframe)
            from managed_assets import SCAN_STRATEGIES
            strategies = configured_asset.get('scan_strategies', SCAN_STRATEGIES)
            provider = asset['provider']
            row = dict(asset=asset['id'], timeframe=timeframe, provider=provider,
                       exchange=asset.get('exchange', ''), symbol=asset.get('symbol', asset['id']),
                       market=asset.get('market'), scan_strategies=strategies)
            from market_calendar import is_stock, is_forex, forex_market_open, next_forex_open
            if is_forex(asset) and not forex_market_open(now):
                coverage.append(row | dict(status='market closed', check_state='waiting for forex market open',
                                          reason='Forex scans paused until Sunday 17:00 New York time',
                                          checked_at=now, next_close_at=next_forex_open(now), new_signals=0))
                continue
            from stock_policy import scan_day, next_scan, MIN_PRICE
            if is_stock(asset) and not scan_day(now):
                coverage.append(row|dict(status='weekday only',reason='Stock scans paused Saturday/Sunday',checked_at=now,next_close_at=next_scan(now)))
                continue
            from scan_schedule import identity, saved, save, next_due, provider_lag
            schedule_key=identity(asset,timeframe,rules | dict(skill_hash=rules['skill_hash'] + pin_rules['version'] + bb_rules['version'] + combined_rules['version']))
            previous=saved(store,schedule_key,now)
            if previous:
                coverage.append(previous)
                continue
            raw=[];candles=[]
            try:
                if provider in ('twelvedata', 'kraken', 'oanda', 'tiingo', 'fmp', 'yahoo'):
                    spacing = (max(8, float(config.get('request_spacing_seconds', 8))) if provider == 'twelvedata'
                               else max(1, float(config.get(provider + '_request_spacing_seconds', 1))))
                    if provider in last_requests:
                        delay = max(0, spacing - (time.monotonic() - last_requests[provider]))
                        if stop is not None:
                            if stop.wait(delay):
                                return coverage
                        else:
                            time.sleep(delay)
                    if stop is not None and stop.is_set():
                        return coverage
                    last_requests[provider] = time.monotonic()
                try:
                    raw = {'csv': read_csv, 'twelvedata': fetch_twelve_data, 'kraken': fetch_kraken, 'oanda': fetch_oanda, 'tiingo': fetch_tiingo, 'alpaca': fetch_alpaca, 'fmp': fetch_fmp, 'yahoo': fetch_yahoo}[provider](asset, timeframe)
                except TwelveDataRateLimit:
                    from alpaca_feed import supported, fallback_asset, fetch_alpaca
                    if not config.get('alpaca_rate_limit_fallback', True) or not supported(asset):
                        raise
                    asset = fallback_asset(asset)
                    row.update(provider='alpaca', exchange=asset['exchange'], fallback_from='twelvedata',
                               fallback_reason='Twelve Data quota reached')
                    raw = fetch_alpaca(asset, timeframe)
                if asset['provider'] == 'twelvedata':
                    raw = usable_provider_history(raw, now, minimum_history(rules))
                if getattr(raw, 'notes', None):
                    row['data_notes'] = '; '.join(raw.notes)
                candles = check_candles(raw, now)
                if is_forex(asset):
                    # Ignore provider bars wholly inside the weekend closure,
                    # including when they are returned after Sunday reopening.
                    # Keep partial Friday bars and daily/weekly buckets that
                    # contain any part of a trading session.
                    trading = [c for c in candles if forex_market_open(c.start) or next_forex_open(c.start) < c.end]
                    if len(trading) != len(candles):
                        row['data_notes'] = '; '.join(filter(None, [row.get('data_notes'),
                            f'{len(candles) - len(trading)} closed-session forex candle(s) excluded']))
                    candles = trading
                if getattr(raw, 'next_close_at', None) and raw.next_close_at > now:
                    row['next_close_at'] = raw.next_close_at
                future_closes = [c.end for c in raw if c.end > now]
                if future_closes:
                    row['next_close_at'] = min(future_closes)
                elif candles and timeframe in ('1h', '4h'):
                    # Preserve the feed's native intraday alignment.
                    period = (1 if timeframe == '1h' else 4) * 60 * 60 * 1000
                    row['next_close_at'] = candles[-1].end + ((now - candles[-1].end) // period + 1) * period
                cache_candles(store, asset, timeframe, candles)
                if candles:row['last_closed_at']=candles[-1].end
                if 'rsi-pb' not in strategies:
                    row['rsi_pb'] = dict(status='disabled',reason='RSI + PB scanning disabled for this asset')
                elif is_stock(asset) and candles and candles[-1].close<MIN_PRICE:
                    row['rsi_pb'] = dict(status='excluded',reason='Stock below $5 minimum')
                else:
                    selection_start = selection['starts_at'] if asset.get('daily_selected') and selection and not catchup else None
                    if asset.get('weekly_screen_selected'):
                        selection_start = asset.get('weekly_screen_since',asset['weekly_screen_week_start'])
                    combined_asset = asset | dict(_rsi_pb_sessions=getattr(raw,'sessions',[]))
                    row['rsi_pb'] = rsi_pb.scan(store,combined_asset,timeframe,candles,combined_rules,now,selection_start,catchup=bool(catchup))
                if 'pin-bars' not in strategies:
                    row['pin_bars'] = dict(status='disabled', reason='Pin-Bars scanning disabled for this asset')
                elif is_stock(asset) and candles and candles[-1].close<MIN_PRICE:
                    row['pin_bars'] = dict(status='excluded', reason='Stock below $5 minimum')
                else:
                    selection_start = selection['starts_at'] if asset.get('daily_selected') and selection and not catchup else None
                    if asset.get('weekly_screen_selected'):
                        selection_start = asset.get('weekly_screen_since',asset['weekly_screen_week_start'])
                    row['pin_bars'] = pin_bars.scan(store, asset, timeframe, candles, pin_rules, now, selection_start, catchup=bool(catchup))
                if 'bb-pin-bars' not in strategies:
                    row['bb_pin_bars'] = dict(status='disabled', reason='BB Pin-Bars scanning disabled for this asset')
                elif is_stock(asset) and candles and candles[-1].close<MIN_PRICE:
                    row['bb_pin_bars'] = dict(status='excluded', reason='Stock below $5 minimum')
                else:
                    selection_start = selection['starts_at'] if asset.get('daily_selected') and selection and not catchup else None
                    if asset.get('weekly_screen_selected'):
                        selection_start = asset.get('weekly_screen_since',asset['weekly_screen_week_start'])
                    row['bb_pin_bars'] = bb_pin_bars.scan(store, asset, timeframe, candles, bb_rules, now, selection_start, catchup=bool(catchup))
                if is_stock(asset) and candles and candles[-1].close<MIN_PRICE:
                    row.update(status='excluded',candles=len(candles),reason='Stock below $5 minimum; no signals generated')
                elif 'rsi' not in strategies:
                    # Top-level counts belong to RSI; Pin-Bars counts stay nested.
                    primary = row['pin_bars'] if 'pin-bars' in strategies else row['bb_pin_bars'] if 'bb-pin-bars' in strategies else row['rsi_pb']
                    row.update({k: v for k, v in primary.items() if k not in ('new_signals', 'historical_matches')})
                    row.update(candles=len(candles), new_signals=0, historical_matches=0,
                               rsi_status='disabled')
                elif len(candles) < minimum_history(rules):
                    row.update(status='insufficient history', candles=len(candles),
                               reason=f'Only {len(candles)} closed candles available; '
                                      f'need {minimum_history(rules)} for this strategy. '
                                      'Use a feed with longer history or wait for more closed candles')
                else:
                    # Optional continuous-market validation (e.g. a verified 24/7 crypto feed).
                    if asset.get('continuous') and any(a.end != b.start for a, b in zip(candles, candles[1:])):
                        raise ValueError('Missing candles in continuous-market feed')
                    source_id = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
                    state_id = f"{asset['id']}@{source_id}@{rules['skill_hash']}@all-stages-v2"
                    matches = detect(candles, rules, source_id, timeframe) + detect(candles, rules, source_id, timeframe, provisional=True)
                    with store.connect() as db:
                        state = db.execute('SELECT last_end FROM scan_state WHERE asset=%s AND timeframe=%s', (state_id, timeframe)).fetchone()
                    # First run establishes a baseline without flooding Telegram with history.
                    cutoff = state[0] if state else candles[-1].end
                    if asset.get('weekly_screen_selected') and not state:
                        cutoff = candles[-1].end-1
                    if asset.get('daily_selected') and selection and not catchup:
                        cutoff = max(cutoff, selection['starts_at'])
                    if asset.get('weekly_screen_selected'):
                        cutoff = max(cutoff, asset.get('weekly_screen_since',asset['weekly_screen_week_start']))
                    if state and cutoff < candles[0].start:
                        raise ValueError('Polling gap exceeds available history; supply candle backfill before resuming')
                    age_seconds = max(0, (now - candles[-1].end) // 1000)
                    if asset.get('max_data_age_seconds') and age_seconds > asset['max_data_age_seconds']:
                        raise ValueError('Latest closed candle is older than the configured freshness limit')
                    pending = sorted((s for s in matches if s['confirmed_at'] > cutoff), key=lambda s:(s['confirmed_at'],s.get('signal_status')!='provisional'))
                    new = sum(store.insert(validate_signal(s, rules) | {'market': asset.get('market') or 'unknown', 'detected_at': now}, delivery_status='summarized' if catchup else 'pending',catchup_id=catchup if isinstance(catchup,str) else None) for s in pending)
                    with store.connect() as db:
                        db.execute('INSERT INTO scan_state AS saved(asset,timeframe,last_end) VALUES(%s,%s,%s) ON CONFLICT(asset,timeframe) DO UPDATE SET last_end=GREATEST(saved.last_end,excluded.last_end)',
                                   (state_id, timeframe, candles[-1].end))
                    row.update(status='scanned' if state else 'baseline set', candles=len(candles), last_closed_at=candles[-1].end,
                               new_signals=new, historical_matches=len(matches), limited_history=len(candles) < 250,
                               last_closed_age_seconds=age_seconds)
            except (ValueError, FileNotFoundError) as exc:
                # Only local, known ValueErrors are shown; URLs and provider payloads are never logged.
                reason = asset.get('unavailable_reason', 'Candle CSV missing; configure a live provider in Assets') if isinstance(exc, FileNotFoundError) else str(exc)
                row.update(status='unavailable', reason=reason)
            except (DatabaseError, *RETRYABLE):
                raise
            except Exception:
                row.update(status='unavailable', reason='Data request or parsing failed; check provider access and format')
            row['checked_at']=now
            row['next_close_at']=provider_lag(store,schedule_key,row,now,next_due(asset,timeframe,raw,candles,now,row),asset)
            save(store,schedule_key,row['next_close_at'],row)
            coverage.append(row)
            store.log('Market check', json.dumps(row))
    from version_info import local_versions
    from runtime_env import environment
    status = dict(scanned_at=now,coverage=coverage,versions=local_versions(),environment=environment())
    with store.connect() as db:
        db.execute('INSERT INTO scanner_status(id,payload) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(status),))
    if config_revision is not None:
        with store.connect() as db:
            db.execute('UPDATE managed_config SET applied_revision=%s WHERE id=1', (config_revision,))
    store.log('Scan cycle completed', f'{sum(not r.get("check_state") for r in coverage)} market checks; {sum(bool(r.get("check_state")) for r in coverage)} waiting for another closed candle')
    return coverage


def prepare_store(store):
    with store.connect() as db:
        db.require_table('scan_state')
        db.require_table('scanner_status')


def load_config():
    path = Path(os.environ.get('SCANNER_CONFIG', str(ROOT / 'scanner.json')))
    config = json.loads(path.read_text(encoding='utf-8'))
    if not config.get('assets') or not config.get('timeframes') or not set(config['timeframes']) <= TIMEFRAMES.keys():
        raise ValueError('Set assets and supported timeframes in scanner.json')
    ids = [a['id'] for a in config['assets']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate asset IDs')
    from managed_assets import validate_asset
    for asset in config['assets']:
        validate_asset(dict(asset, enabled=asset.get('enabled', True),
                            timeframes=asset.get('timeframes', config['timeframes'])))
    return config


def next_heartbeat_at(now):
    """Next HH:15 boundary (Israel's UTC offsets are whole hours)."""
    target = math.floor(now / 3600) * 3600 + 15 * 60
    return target if target > now else target + 3600


def scanner_heartbeat(store, finished):
    target = next_heartbeat_at(time.time())
    while not finished.is_set():
        now = time.time()
        if now >= target:
            try:
                with store.connect() as db:
                    saved = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
                phase = json.loads(saved[0]).get('phase', 'starting') if saved else 'starting'
                stamp = datetime.fromtimestamp(now, ZoneInfo('Asia/Jerusalem')).strftime('%Y-%m-%d %H:%M:%S')
                store.log('Scanner heartbeat', f'Scanner is running · {phase} · {stamp} (Israel time)' + memory_status())
            except DatabaseError as exc:
                # A long sync transaction must not permanently kill monitoring.
                print(f'Scanner heartbeat could not access database ({type(exc).__name__}); retrying in 30 seconds', file=sys.stderr, flush=True)
                finished.wait(30)
                continue
            target = next_heartbeat_at(now)
        finished.wait(min(1, max(0, target - now)))


def memory_status():
    """Current Linux RSS, rather than the process's all-time high-water mark."""
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return f' · RSS {int(line.split()[1]) / 1024:.1f} MiB · {threading.active_count()} threads'
    except (OSError, ValueError, IndexError):
        pass
    return ''


def scanner_worker(store, config, rules, stop):
    finished = threading.Event()
    heartbeat = threading.Thread(target=scanner_heartbeat, args=(store, finished), daemon=True)
    heartbeat.start()
    try:
        while True:
            try:
                prepare_store(store)
                _scanner_worker(store, config, rules, stop)
                break
            except OperationalError as exc:
                if not database_busy(exc):
                    raise
                print('Scanner database busy; retrying in 5 seconds', file=sys.stderr, flush=True)
                # Persisted candle/signal state and catch-up commands are reused.
                # Keep the same heartbeat thread alive throughout contention.
                if stop.wait(5):
                    break
    except (DatabaseError, *RETRYABLE):
        raise
    except Exception as exc:
        # Preserve failure visibility even when SQLite itself is unavailable.
        reason = f'Scanner stopped after {type(exc).__name__}; restart required'
        print(reason, file=sys.stderr, flush=True)
        try:
            with store.connect() as db:
                saved = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
                status = json.loads(saved[0]) if saved else {}
                status.update(phase='failed', next_scan_at=None, error=reason)
                db.execute('INSERT INTO scanner_status(id,payload) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(status),))
            store.log('Scanner failed', reason)
        except DatabaseError:
            pass
        raise
    finally:
        finished.set()
        heartbeat.join(timeout=2)


def _scanner_worker(store, config, rules, stop):
    prepare_store(store)
    from managed_assets import ensure
    ensure(store, config)
    from scanner_control import initialize as control_initialize, claim, finish
    with store.connect() as db:
        control_initialize(db)
        db.execute("UPDATE scanner_commands SET status='pending' WHERE status='running'")
    command=None
    with store.connect() as db:
        old=db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
    while not stop.is_set():
        with store.connect() as db:
            previous = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
            if previous:
                progress = json.loads(previous[0])
                progress.update(phase='scanning', next_scan_at=None)
                db.execute('UPDATE scanner_status SET payload=%s WHERE id=1', (json.dumps(progress),))
        command=command or claim(store)
        coverage = scan_once(store, config, rules, stop=stop,catchup=command or False,run_momentum=True)
        if command and not stop.is_set():
            finish(store,command,coverage,sum(r.get('new_signals',0) for r in coverage))
            command=None
        if stop.is_set():
            break
        from managed_assets import effective
        current, _ = effective(store, config)
        delay = max(30, current.get('poll_seconds', 300))
        from momentum import seconds_to_scan
        delay = min(delay, seconds_to_scan(store))
        closes = [r['next_close_at'] / 1000 for r in (coverage or []) if r.get('next_close_at')]
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
        stock_policy=current.get('weekly_stock_screen',{})
        if stock_policy.get('enabled'):
            from weekly_stock_screen import seconds_to_refresh as stocks_seconds_to_refresh, status as stock_status, local_day as stock_day
            clock_now=time.time()
            delay=min(delay,stocks_seconds_to_refresh(stock_policy,clock_now))
            selected=stock_status(store)
            if selected and selected['day']!=stock_day(stock_policy,clock_now*1000)[0]:
                delay=1
        with store.connect() as db:
            saved = db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
            schedule = json.loads(saved[0]) if saved else dict(scanned_at=int(time.time() * 1000), coverage=coverage or [])
            schedule.update(phase='waiting', next_scan_at=int((time.time() + delay) * 1000))
            db.execute('INSERT INTO scanner_status(id,payload) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(schedule),))
        command=wait_for_command(store,stop,delay)


def wait_for_command(store,stop,delay):
    from scanner_control import claim
    deadline=time.monotonic()+delay
    while not stop.is_set() and time.monotonic()<deadline:
        command=claim(store)
        if command:return command
        stop.wait(min(1,max(0,deadline-time.monotonic())))
    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Scan once without starting Telegram delivery')
    parser.parse_args()
    load_env()
    from runtime_env import configure
    configure(ROOT)
    data = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    data.mkdir(parents=True, exist_ok=True)
    store = Store()
    store.acquire_worker()
    prepare_store(store)
    print(json.dumps(scan_once(store, load_config(), load_rules()), indent=2))
