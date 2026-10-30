#!/usr/bin/env python3
"""Read-only, on-demand S&P 500 RSI + PB screen; no worker, database or alerts."""
import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import html
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NY = ZoneInfo('America/New_York')
FRAMES = ('monthly', 'weekly', 'daily', '4h')
UNIVERSE_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, NY).isoformat() if ms else ''


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def universe(path=None):
    if path:
        data = json.loads(Path(path).read_text())
        symbols = data if isinstance(data, list) else data.get('symbols', data.get('indexes', {}).get('sp500'))
        source, dated = str(Path(path).resolve()), data.get('as_of', data.get('asof', 'unspecified')) if isinstance(data, dict) else 'unspecified'
    else:
        try:
            import certifi
            request = urllib.request.Request(UNIVERSE_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=certifi.where()), timeout=30) as response:
                page = response.read().decode()
            table = re.search(r'<table\b[^>]*\bid=[\"\']constituents[\"\'][^>]*>.*?</table>', page, re.S).group()
            symbols = []
            for row in re.findall(r'<tr\b.*?</tr>', table, re.S):
                cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)
                if cells:
                    symbols.append(html.unescape(re.sub('<[^>]+>', '', cells[0])).strip())
            if not 490 <= len(symbols) <= 520:
                raise ValueError('Unexpected constituent count')
            source, dated = UNIVERSE_URL, datetime.now(timezone.utc).isoformat()
        except (OSError, ValueError, AttributeError):
            data = json.loads((ROOT / 'stock_universe.json').read_text())
            symbols, source, dated = data['indexes']['sp500'], data['source'], data['as_of']
            print(f'Warning: live membership unavailable; using repository membership dated {dated}.', file=sys.stderr)
    if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', s) for s in symbols):
        raise ValueError('Invalid universe symbols')
    return dict(symbols=sorted(set(symbols)), source=source, as_of=dated)


def fetch_batch(symbols, frame, cutoff, history_bars, feed):
    """Use bounded native-bar requests; don't inherit the weekly screen's 800-day cap."""
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
            for row in rows:
                dt = datetime.fromisoformat(row['t'].replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    raise ValueError('Provider timestamp has no timezone')
                end = stock_candle_end(dt, frame)
                histories[symbol].append(Candle(int(dt.timestamp()*1000), int(end.timestamp()*1000),
                                               *(float(row[k]) for k in ('o', 'h', 'l', 'c'))))
        token = response.get('next_page_token')
        if not token:
            return {s: check_candles(sorted(bars, key=lambda b: b.start), cutoff) for s, bars in histories.items()}
        if not isinstance(token, str) or token in tokens:
            raise ValueError('Provider pagination did not advance')
        tokens.add(token)
        params['page_token'] = token
    raise ValueError('Provider exceeded 1000 pages for one batch; reduce --batch-size')


def evaluate(symbol, frame, bars, rules, recent_bars, feed):
    import rsi_pb
    minimum = rsi_pb.minimum_history(rules)
    coverage = dict(symbol=symbol, timeframe=frame, candles=len(bars),
                    latest_closed_ny=iso(bars[-1].end) if bars else '', limited_history=len(bars)<250)
    if len(bars) < minimum:
        return coverage | dict(status='insufficient history', reason=f'Need {minimum} closed candles'), []
    signals = rsi_pb.detect(bars, rules, f'alpaca:{feed}:{symbol}', symbol, frame,
                            dict(provider='alpaca', market='stock', symbol=symbol))
    cutoff = bars[-min(recent_bars, len(bars))].end
    recent = []
    for signal in signals:
        if signal['confirmed_at'] < cutoff:
            continue
        after = [b for b in bars if b.end > signal['confirmed_at']]
        pivot = signal['pivot_candle']
        breached = any(b.low < pivot['low'] if signal['direction']=='bullish' else b.high > pivot['high'] for b in after)
        recent.append(signal | dict(confirmation_ny=iso(signal['confirmed_at']), age_closed_candles=len(after), structural_breach=breached))
    return coverage | dict(status='qualified' if recent else 'no qualifying signal', signals=len(recent)), recent


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def report(out, manifest, coverage, signals):
    symbols = manifest['universe']['symbols']
    present = {(r['symbol'], r['timeframe']) for r in coverage}
    coverage = coverage + [dict(symbol=s, timeframe=f, status='not scanned') for f in manifest['timeframes'] for s in symbols if (s,f) not in present]
    ordered = sorted(signals, key=lambda s: (s['symbol'], s['timeframe'], -s['confirmed_at']))
    stock_rows = []
    for symbol in sorted({s['symbol'] for s in signals}):
        matches = [s for s in signals if s['symbol']==symbol]
        stock_rows.append(dict(symbol=symbol, timeframes=', '.join(f for f in manifest['timeframes'] if any(s['timeframe']==f for s in matches)),
                               directions=', '.join(sorted({s['direction'] for s in matches})),
                               latest_confirmation_ny=max(s['confirmation_ny'] for s in matches),
                               has_unbreached_pattern=any(not s['structural_breach'] for s in matches),
                               has_latest_candle_pattern=any(s['age_closed_candles']==0 for s in matches)))
    counts = {frame: {status: sum(r['status']==status and r['timeframe']==frame for r in coverage)
                     for status in ('qualified','no qualifying signal','insufficient history','unavailable','not scanned')}
              for frame in manifest['timeframes']}
    save_json(out/'signals.json', ordered)
    save_json(out/'coverage.json', coverage)
    save_json(out/'summary.json', dict(qualified_stocks=len(stock_rows), patterns=len(signals), counts=counts, scan=manifest))
    write_csv(out/'qualified-stocks.csv', stock_rows, ['symbol','timeframes','directions','latest_confirmation_ny','has_unbreached_pattern','has_latest_candle_pattern'])
    write_csv(out/'patterns.csv', ordered, ['symbol','timeframe','direction','pinbar_direction','confirmation_ny','age_closed_candles','structural_breach','close'])
    write_csv(out/'coverage.csv', coverage, ['symbol','timeframe','status','reason','candles','latest_closed_ny','limited_history','signals'])
    (out/'qualified-stocks.txt').write_text('\n'.join(s['symbol'] for s in stock_rows)+'\n')
    lines = ['# S&P 500 RSI + PB screen', '', f'As of {iso(manifest["cutoff_ms"])} (New York). {len(symbols)} securities; {len(stock_rows)} qualified stocks; {len(signals)} patterns.',
             f'Latest {manifest["recent_bars"]} completed candles per timeframe. Alpaca {manifest["feed"]}, split-adjusted OHLC, 16-minute publication margin.',
             f'Membership: {manifest["universe"]["source"]}, dated {manifest["universe"]["as_of"]}.', '',
             '| Timeframe | Qualified | No signal | Insufficient history | Unavailable | Not scanned |', '|---|---|---|---|---|---|']
    for frame, counts_for_frame in counts.items():
        lines.append('| '+frame+' | '+' | '.join(str(n) for n in counts_for_frame.values())+' |')
    lines += ['', '| Symbol | Timeframes | Direction | Latest confirmation (NY) | Unbreached pattern |', '|---|---|---|---|---|']
    for row in stock_rows:
        lines.append(f'| {row["symbol"]} | {row["timeframes"]} | {row["directions"]} | {row["latest_confirmation_ny"]} | {row["has_unbreached_pattern"]} |')
    lines += ['', 'Uses the existing rsi_pb.detect engine and locally configured strategy rules. Exactly the next candle must be an opposite shape-only pinbar; no EMA filter. Native Alpaca 4-hour buckets can include extended-session bars; provider alignment is preserved. All unfinished candles are excluded.',
              'Structural breach means a subsequent closed candle crossed the divergence pivot extreme; it is reported, not used as an extra qualification filter. Monthly patterns in a 10-candle window can be many months old. Histories below 250 candles are flagged as limited; those below the engine minimum cannot qualify. coverage.csv records actual last candle dates; a historical match does not establish current actionability.',
              'patterns.csv contains individual matches; signals.json includes full pivot, RSI, band and OHLC evidence. qualified-stocks.csv contains one row per stock. No alerts, orders, database writes or scanner configuration changes are made.']
    (out/'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return len(stock_rows)


def emit_event(enabled, event, **payload):
    if enabled:
        print("RSIPB_EVENT " + json.dumps(dict(event=event, **payload)), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeframes', nargs='+', choices=FRAMES, default=list(FRAMES))
    parser.add_argument('--recent-bars', type=int, default=10, help='Signal recency in completed candles (default: 10; use 1 for latest candle only)')
    parser.add_argument('--history-bars', type=int, default=250, help='History target, not a guarantee; 4h history can be slow (default: 250)')
    parser.add_argument('--batch-size', type=int, default=10, help='Symbols per provider request (1–20, default: 10)')
    parser.add_argument('--feed', choices=('sip','iex'), help='Default: ALPACA_DATA_FEED or sip; no automatic feed substitution')
    parser.add_argument('--universe-file', type=Path, help='Use a supplied JSON symbol list or repository stock_universe.json')
    parser.add_argument('--output', type=Path, help='New report directory; default: data/ad-hoc/rsi-pb-sp500-TIMESTAMP')
    parser.add_argument('--resume', type=Path, help='Resume an interrupted run at its ORIGINAL cutoff and rules using saved batches')
    parser.add_argument('--events', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.recent_bars < 1 or args.history_bars < max(100, args.recent_bars+84) or not 1 <= args.batch_size <= 20:
        parser.error('--recent-bars must be positive; --history-bars must be at least max(100, recent-bars + 84); --batch-size must be 1–20')
    if args.resume and args.output:
        parser.error('--resume and --output cannot be combined')
    from platform_app import load_env
    import rsi_pb
    import certifi
    # urllib's default CA store varies between local Python installations.
    import alpaca_feed
    load_env()
    for key in ('ALPACA_API_KEY','ALPACA_SECRET_KEY'):
        if not os.environ.get(key) or os.environ[key].lower().startswith('your_'):
            parser.error(f'Configure {key} locally in .env; credentials must not be entered in chat')
    feed = args.feed or os.environ.get('ALPACA_DATA_FEED', 'sip')
    if feed not in ('sip', 'iex'):
        parser.error('ALPACA_DATA_FEED must be sip or iex')
    # Configure TLS on this script's requests only, without changing other processes.
    original_urlopen = alpaca_feed.urllib.request.urlopen
    def verified_urlopen(request, *positional, **kwargs):
        kwargs.setdefault('context', ssl.create_default_context(cafile=certifi.where()))
        return original_urlopen(request, *positional, **kwargs)
    alpaca_feed.urllib.request.urlopen = verified_urlopen
    if args.resume:
        out = args.resume.resolve()
        manifest = json.loads((out/'manifest.json').read_text())
        if manifest['rules'] != rsi_pb.load_rules():
            parser.error('Strategy rules changed; start a fresh run instead of resuming')
        print(f'Resuming original cutoff {iso(manifest["cutoff_ms"])}; saved timeframe/feed/history settings apply.', flush=True)
    else:
        out = (args.output or ROOT/'data'/'ad-hoc'/('rsi-pb-sp500-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))).resolve()
        if out.exists() and any(out.iterdir()):
            parser.error('Output directory is not empty; use --resume or a new directory')
        out.mkdir(parents=True, exist_ok=True)
        manifest = dict(cutoff_ms=int((time.time()-16*60)*1000), universe=universe(args.universe_file),
                        feed=feed, timeframes=list(dict.fromkeys(args.timeframes)), recent_bars=args.recent_bars,
                        history_bars=args.history_bars, batch_size=args.batch_size, rules=rsi_pb.load_rules())
        save_json(out/'manifest.json', manifest)
    cache = out/'candles'; cache.mkdir(exist_ok=True)
    coverage, signals = [], []
    symbols = manifest['universe']['symbols']
    interrupted, failed = False, False
    fatal = None
    emit_event(args.events, 'started', output=str(out), manifest=manifest)
    try:
        for frame in manifest['timeframes']:
            for offset in range(0,len(symbols),manifest['batch_size']):
                batch = symbols[offset:offset+manifest['batch_size']]
                file = cache/f'{frame}-{offset:04d}.json'
                batch_coverage, batch_signals = [], []
                try:
                    from scanner import Candle, check_candles
                    if file.exists():
                        stored = json.loads(file.read_text())
                        if set(stored) != set(batch):
                            raise ValueError('Cache does not match this batch')
                        histories = {s:check_candles([Candle(**b) for b in bars],manifest['cutoff_ms']) for s,bars in stored.items()}
                    else:
                        if fatal:
                            raise ValueError(fatal)
                        histories = fetch_batch(batch, frame, manifest['cutoff_ms'], manifest['history_bars'], manifest['feed'])
                        save_json(file, {s:[asdict(b) for b in bars] for s,bars in histories.items()})
                    for symbol in batch:
                        row, matches = evaluate(symbol, frame, histories[symbol], manifest['rules'], manifest['recent_bars'], manifest['feed'])
                        batch_coverage.append(row); batch_signals.extend(matches)
                    coverage.extend(batch_coverage); signals.extend(batch_signals)
                except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
                    failed = True
                    # Don't print arbitrary exception text: provider errors can contain request details.
                    reason = 'Batch data unavailable or invalid; rerun with --resume to retry.'
                    batch_coverage = [dict(symbol=s,timeframe=frame,status='unavailable',reason=reason) for s in batch]
                    coverage.extend(batch_coverage)
                    print(f'{frame}: batch {offset} unavailable; see coverage report.', file=sys.stderr)
                    if any(word in str(exc).lower() for word in ('credentials','feed access','quota')):
                        fatal = 'Provider authentication, entitlement or quota unavailable'
                        raise RuntimeError(fatal) from None
                emit_event(args.events, 'batch', timeframe=frame, completed=len(coverage), total=len(symbols)*len(manifest['timeframes']), coverage=batch_coverage, signals=batch_signals)
                print(f'{frame}: {min(offset+len(batch),len(symbols))}/{len(symbols)} securities processed', flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print('\nInterrupted; saving partial report. Resume with --resume.', file=sys.stderr)
    except RuntimeError as exc:
        failed = True
        print(str(exc), file=sys.stderr)
    finally:
        qualified = report(out, manifest, coverage, signals)
        alpaca_feed.urllib.request.urlopen = original_urlopen
    print(f'{qualified} qualified stocks, {len(signals)} patterns. Report: {out / "report.md"}', flush=True)
    code = 130 if interrupted else 2 if failed else 0
    emit_event(args.events, 'finished', output=str(out), exit_code=code)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
