"""Read-only, no-delay weekly RSI strategy research. Never starts a scanner."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time as timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scanner import Candle, check_candles, detect, minimum_history
from platform_app import load_rules
from market_calendar import NY, stock_close, stock_trading_day, previous_session, stock_candle_end
from stock_rsi_watch import millis, MINUTES, same_pair
from stock_rsi_data import aggregate

UTC = timezone.utc
PILOT_DAYS = ('2025-11-28', '2026-03-06', '2026-03-13', '2026-04-02')
PILOT_SYMBOLS = ('AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'JPM', 'XOM', 'JNJ', 'WMT')
NAMES = dict(zip(PILOT_SYMBOLS, ('Apple', 'Microsoft', 'NVIDIA', 'Amazon', 'Meta Platforms',
                              'Alphabet', 'JPMorgan Chase', 'Exxon Mobil', 'Johnson & Johnson', 'Walmart')))


def stamp(value, zone=NY):
    return datetime.fromtimestamp(value / 1000, zone).isoformat(timespec='minutes')


def session_after(day):
    day += timedelta(days=1)
    while not stock_trading_day(day):
        day += timedelta(days=1)
    return day


def week_spec(last):
    monday = last - timedelta(days=last.weekday())
    if last != previous_session(monday + timedelta(days=4)):
        raise ValueError('Pilot date must be the last trading session of its week')
    close = millis(stock_close(last))
    return dict(week=str(monday), last_day=str(last), start=close-3*3600_000, close=close,
                period_start=millis(datetime.combine(monday, time.min, NY)),
                expires=millis(datetime.combine(session_after(last), time(9,30), NY)))


def week_dates(start, end):
    """Select weekly closing sessions inside the half-open study interval."""
    monday = start - timedelta(days=start.weekday())
    days = []
    while monday < end:
        last = previous_session(monday + timedelta(days=4))
        if start <= last < end:
            days.append(last)
        monday += timedelta(days=7)
    return days


def eligible(bar):
    dt = datetime.fromtimestamp(bar.start/1000, NY)
    return stock_trading_day(dt.date()) and time(4) <= dt.time() < time(20)


class History:
    """Explicit SIP-only GETs with reusable, checksummed local cache files."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.receipts = []

    def get(self, symbol, frame, start, end):
        from alpaca_feed import request_page
        params = dict(symbols=symbol, timeframe=frame, feed='sip', adjustment='split',
                      # Long weekly warm-up must follow prior symbols (e.g. META/FB).
                      # Intraday pilot ranges retain explicit contemporary tickers.
                      asof=stamp(end)[:10] if frame=='1Week' else '-', sort='asc', limit=10000,
                      start=datetime.fromtimestamp(start/1000, UTC).isoformat(),
                      end=datetime.fromtimestamp(end/1000, UTC).isoformat())
        identity = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        path = self.directory / (identity+'.json')
        if path.exists():
            cached = json.loads(path.read_text())
            if cached['request'] != params:
                raise ValueError('Cached request mismatch')
            rows = cached['rows']
        else:
            rows, seen, query = [], set(), dict(params)
            for _ in range(100):
                timer.sleep(2)  # at most two download processes; leave room for live traffic
                for attempt in range(3):
                    try:
                        page = request_page(query)
                        break
                    except (OSError, ValueError):
                        if attempt == 2:
                            raise
                        timer.sleep(2 ** (attempt+1))
                mapping = page.get('bars')
                if not isinstance(mapping, dict) or set(mapping)-{symbol}:
                    raise ValueError('Malformed historical response')
                rows.extend(mapping.get(symbol, []))
                token = page.get('next_page_token')
                if not token:
                    break
                if not isinstance(token, str) or token in seen:
                    raise ValueError('Pagination repeated')
                seen.add(token); query['page_token'] = token
            else:
                raise ValueError('History pagination truncated')
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(request=params, fetched_at=datetime.now(UTC).isoformat(), rows=rows)))
            temporary.replace(path)
        self.receipts.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                  symbol=symbol, frame=frame, rows=len(rows)))
        bars = []
        for row in rows:
            dt = datetime.fromisoformat(row['t'].replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('Missing timezone')
            finish = (dt + timedelta(minutes=int(frame[:-3]))) if frame.endswith('Min') else stock_candle_end(dt, 'weekly' if frame=='1Week' else 'daily')
            bars.append(Candle(millis(dt), millis(finish), *(float(row[k]) for k in ('o','h','l','c'))))
        return check_candles(bars, end)


def fetch_case(history, symbol, spec):
    first = spec['period_start']
    last = date.fromisoformat(spec['last_day'])
    following = session_after(last)
    final = following
    for _ in range(5):
        final = session_after(final)
    outcome_end = millis(datetime.combine(final, time(20), NY))
    minutes = history.get(symbol, '1Min', millis(datetime.combine(last,time(4),NY)), outcome_end)
    minutes = [b for b in minutes if eligible(b)]
    warm = history.get(symbol, '15Min', spec['start']-130*86400_000,
                       millis(datetime.combine(last,time(4),NY)))
    weekly = history.get(symbol, '1Week', first-275*7*86400_000, first-1)
    daily = history.get(symbol, '1Day', first, millis(datetime.combine(last,time.min,NY))-1)
    return dict(symbol=symbol, minutes=minutes, warm=[b for b in warm if eligible(b)],
                weekly=weekly, daily=daily, outcome_end=outcome_end)


def download_case(job):
    directory,symbol,spec=job
    history=History(directory)
    try:
        return symbol,fetch_case(history,symbol,spec),history.receipts,None
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return symbol,None,history.receipts,type(exc).__name__


def parent_at(case, spec, at):
    """Build the current week only from completed prior days and visible minutes."""
    cutoff = min(at, spec['close'])
    last = date.fromisoformat(spec['last_day'])
    opening = millis(datetime.combine(last, time(9,30), NY))
    pieces = [b for b in case['daily'] if spec['period_start'] <= b.start and b.end < opening]
    expected = set()
    day = datetime.fromtimestamp(spec['period_start']/1000, NY).date()
    while day < last:
        if stock_trading_day(day): expected.add(day)
        day += timedelta(days=1)
    if {datetime.fromtimestamp(b.start/1000, NY).date() for b in pieces} != expected:
        raise ValueError('Missing current-week daily history')
    visible = [b for b in case['minutes'] if opening <= b.start and b.end <= cutoff]
    if not visible or visible[-1].end < cutoff-5*60_000:
        raise ValueError('Missing/stale weekly snapshot minutes')
    pieces += visible
    partial = Candle(spec['period_start'], cutoff, pieces[0].open, max(b.high for b in pieces),
                     min(b.low for b in pieces), pieces[-1].close)
    return [b for b in case['weekly'] if b.end < spec['period_start']] + [partial]


def current_signals(bars, rules, symbol, frame):
    if len(bars) < max(250, minimum_history(rules)):
        raise ValueError('Insufficient history: 250 candles required')
    return [s for s in detect(bars, rules, symbol, frame, provisional=True, include_hidden=False)
            if s['pivot2'] == bars[-1].start]


def closed_lower_signals(case, spec, rules):
    """Precompute causal prefix-equivalent closed signals; never use future pivots."""
    pieces = case['warm'] + [b for b in case['minutes'] if b.end <= spec['expires']]
    result = defaultdict(list)
    coverage = {}
    for frame in MINUTES:
        bars = [b for b in aggregate(pieces, frame, spec['expires']) if b.end <= spec['expires']]
        coverage[frame] = sum(b.end <= spec['start'] for b in bars)
        if coverage[frame] < 250:
            continue
        index = {b.start:i for i,b in enumerate(bars)}
        minute_ends = {b.end for b in case['minutes']}
        for s in detect(bars, rules, case['symbol'], frame, provisional=True, include_hidden=False):
            b = bars[index[s['pivot2']]]
            if spec['start'] <= b.end < spec['expires'] and index[b.start] >= 249:
                # Exact last minute required for a candle-close entry, not stale carry-forward.
                if b.end in minute_ends:
                    result[b.end].append((frame, b, s))
    return result, coverage


def replay(spec, cases, rules, parent_detector=current_signals, lower_provider=closed_lower_signals):
    """Chronological strategy replay; no notification prerequisite or execution delay."""
    parents, entries, audit = {}, [], []
    counts = Counter(bullish=0, bearish=0)
    schedules, coverage = {}, {}
    for symbol, case in cases.items():
        schedules[symbol], coverage[symbol] = lower_provider(case, spec, rules)
    times = set(range(spec['start'], spec['close']+1, 60_000))
    times.update(t for schedule in schedules.values() for t in schedule)
    frozen = {}
    for at in sorted(times):
        if at >= spec['expires']: continue
        for symbol in sorted(cases):
            case = cases[symbol]
            if at > spec['close'] and symbol in frozen:
                signals = frozen[symbol]
            else:
                try:
                    signals = parent_detector(parent_at(case, spec, at), rules, symbol, 'weekly')
                except ValueError as exc:
                    audit.append(dict(at=at,symbol=symbol,kind='data_unavailable',reason=str(exc)))
                    # Never trade using a stale parent; retry exact frozen close later.
                    continue
                if at >= spec['close']: frozen[symbol] = signals
            for direction in ('bullish','bearish'):
                key = (symbol, direction)
                parent = parents.get(key)
                matches = [s for s in signals if s['direction']==direction]
                if parent is None and at < spec['close'] and counts[direction] < 5 and matches:
                    chosen = max(matches, key=lambda s:s['pivot1'])
                    parent = parents[key] = dict(signal=chosen, admitted=at, valid=True, slots=Counter(), counted=False)
                    audit.append(dict(at=at,symbol=symbol,direction=direction,kind='higher_admitted'))
                if parent is None or not parent['valid']: continue
                exact = next((s for s in matches if same_pair(s,parent['signal'])), None)
                if exact is None:
                    parent['valid'] = False
                    audit.append(dict(at=at,symbol=symbol,direction=direction,kind='higher_invalidated'))
                    for entry in entries:
                        if entry['ticker']==symbol and entry['direction']==direction:
                            entry['parent_invalidated_at'] = at
                    continue
                parent['signal'] = exact
                candidates = schedules[symbol].get(at, [])
                for frame in MINUTES:
                    lower = [(b,s) for f,b,s in candidates if f==frame and s['direction']==direction]
                    if not lower or parent['slots'][frame]>=4 or at<=parent['admitted']: continue
                    candle, signal = max(lower, key=lambda item:item[1]['pivot1'])
                    parent['slots'][frame] += 1
                    if not parent['counted']:
                        counts[direction] += 1; parent['counted'] = True
                    entries.append(dict(week=spec['week'],ticker=symbol,name=NAMES.get(symbol,symbol),tf=frame,
                        direction=direction,entry=candle.close,entry_time=at,lower_start=candle.start,
                        higher_status='PROVISIONAL' if at<spec['close'] else 'CLOSED',
                        parent_admitted_at=parent['admitted'],parent_signal=exact,lower_signal=signal,
                        parent_invalidated_at=None,lower_slot=parent['slots'][frame]))
    return entries, dict(week=spec['week'],coverage=coverage,counts=dict(counts),
                        audit=audit,parents=len(parents),entries=len(entries))


def horizon_end(entry_time, label):
    dt = datetime.fromtimestamp(entry_time/1000, NY)
    if label in ('1h','4h'):
        remaining = 60 if label=='1h' else 240
        while remaining:
            if stock_trading_day(dt.date()) and time(4) <= dt.time() < time(20):
                remaining -= 1
            dt += timedelta(minutes=1)
        return millis(dt)
    day = dt.date()
    while not stock_trading_day(day) or millis(stock_close(day)) <= entry_time:
        day += timedelta(days=1)
    count = {'next_close':1,'3_sessions':3,'5_sessions':5}[label]
    for _ in range(count-1): day=session_after(day)
    return millis(stock_close(day))


def outcome(entry, minutes, end, available_end):
    bars = [b for b in minutes if entry['entry_time']<=b.start and b.end<=end and eligible(b)]
    if end > available_end or not bars:
        return dict(status='incomplete',target_end=end,bars=len(bars))
    high = max(bars,key=lambda b:b.high); low=min(bars,key=lambda b:b.low)
    e=entry['entry']; long=entry['direction']=='bullish'
    gain=max(0,((high.high-e) if long else (e-low.low))/e*100)
    dd=max(0,((e-low.low) if long else (high.high-e))/e*100)
    closing_fresh = bars[-1].end >= end-2*60_000
    return dict(status='complete' if closing_fresh else 'stale_endpoint',target_end=end,bars=len(bars),
                return_pct=((bars[-1].close-e) if long else (e-bars[-1].close))/e*100 if closing_fresh else None,
                exit_price=bars[-1].close if closing_fresh else None,last_bar_end=bars[-1].end,
                max_gain_pct=gain,max_dd_pct=dd,
                max_gain_time=(high.start if long else low.start) if gain>0 else None,
                max_dd_time=(low.start if long else high.start) if dd>0 else None,
                sparse=len(bars)<5)


def select_first(entries):
    """Executable chronological summary: first entry; lowest TF only breaks ties."""
    chosen = {}
    for e in sorted(entries,key=lambda e:(e['entry_time'],MINUTES[e['tf']],e['ticker'])):
        chosen.setdefault((e['week'],e['ticker'],e['direction']),e)
    return list(chosen.values())


def summaries(entries):
    rows=[]
    for horizon in ('1h','4h','next_close','3_sessions','5_sessions'):
        for frame in MINUTES:
            group=[e['outcomes'][horizon] for e in entries if e['tf']==frame]
            valid=[o for o in group if o['status']=='complete']
            rows.append(dict(horizon=horizon,tf=frame,signals=len(group),complete=len(valid),
                mean_return_pct=statistics.mean(o['return_pct'] for o in valid) if valid else None,
                median_return_pct=statistics.median(o['return_pct'] for o in valid) if valid else None,
                mean_max_gain_pct=statistics.mean(o['max_gain_pct'] for o in valid) if valid else None,
                mean_max_dd_pct=statistics.mean(o['max_dd_pct'] for o in valid) if valid else None))
    return rows


def report(result, destination):
    path=Path(destination);path.mkdir(parents=True,exist_ok=True)
    (path/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    def num(x): return 'N/A' if x is None else f'{x:+.2f}%'
    def table(headers, rows):
        return '<table><thead><tr>'+''.join('<th>'+html.escape(str(h))+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in r)+'</tr>' for r in rows)+'</tbody></table>'
    summary=table(['Horizon','TF','Signals','Complete outcomes','Average return','Median return','Average max gain','Average max DD'],
        [[s['horizon'],s['tf'],s['signals'],s['complete'],num(s['mean_return_pct']),num(s['median_return_pct']),num(s['mean_max_gain_pct']),num(s['mean_max_dd_pct'])] for s in result['summary']])
    detail=[]
    for e in result['entries']:
        for h,o in e['outcomes'].items():
            detail.append([e['week'],e['ticker'],e['name'],e['tf'],e['direction'],e['higher_status'],stamp(e['entry_time']),
                f"{e['entry']:.4f}",h,o['status'],num(o.get('return_pct')),num(o.get('max_gain_pct')),
                stamp(o['max_gain_time']) if o.get('max_gain_time') else 'N/A',num(o.get('max_dd_pct')),
                stamp(o['max_dd_time']) if o.get('max_dd_time') else 'N/A',o['bars']])
    details=table(['Week','Ticker','Stock name','TF','Direction','Weekly status at entry','Entry NY','Entry USD','Horizon','Coverage','Return','Max gain','Gain minute NY','Max DD','DD minute NY','Minute bars'],detail)
    checks=table(['Week','Parents','Raw entries'],[[w['week'],w['parents'],w['entries']] for w in result['weeks']])
    content='''<!doctype html><html><meta charset="utf-8"><title>Weekly RSI strategy backtest</title>
<style>body{font:15px system-ui;margin:28px;color:#182536}table{border-collapse:collapse;margin:18px 0;width:100%}td,th{padding:7px;border:1px solid #ccd5df;white-space:nowrap}th{background:#eaf0f7;position:sticky;top:0}section{overflow:auto}input{padding:10px;width:360px}</style>
<h1>Weekly RSI strategy backtest</h1>'''
    content+= '<p>'+html.escape(result['mode'])+'; '+str(len(result['entries']))+' raw validated entries; '+str(len(result['selected']))+' chronologically selected stock/week/direction entries.</p>'
    content+='<p>No delays or trading costs. Entry at the lower candle close; weekly divergence may remain provisional. No current-price metric. Fixed-horizon returns are hypothetical, not a portfolio simulation.</p>'
    content+='<ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in result['limitations'])+'</ul>'
    if result['failures']:
        content+='<h2>Excluded stock/weeks — not no-signal results</h2>'+table(['Closing session','Ticker','Error'],[[f['week'],f['symbol'],f['error']] for f in result['failures']])
    content+='<h2>Selected-entry summary</h2>'+summary
    all_summary=table(['Horizon','TF','Raw signals','Complete outcomes','Average return','Average max gain','Average max DD'], [[s['horizon'],s['tf'],s['signals'],s['complete'],num(s['mean_return_pct']),num(s['mean_max_gain_pct']),num(s['mean_max_dd_pct'])] for s in result.get('all_signal_summary',[])])
    content+='<h2>All-signal timeframe comparison</h2><p>Correlated repeated signals are retained here; this is not an independent-trade portfolio.</p>'+all_summary+'<h2>Week coverage</h2>'+checks
    grouped=[]
    for dimension in ('month','direction','higher_status'):
        buckets=defaultdict(list)
        for e in result['selected']:
            key=stamp(e['entry_time'])[:7] if dimension=='month' else e[dimension]
            buckets[key].append(e)
        for key,entries in sorted(buckets.items()):
            outcomes=[e['outcomes']['next_close'] for e in entries if e['outcomes']['next_close']['status']=='complete']
            grouped.append([dimension,key,len(entries),len(outcomes),num(statistics.mean(o['return_pct'] for o in outcomes) if outcomes else None)])
    content+='<h2>Next-regular-close breakdown</h2>'+table(['Grouping','Value','Selected entries','Complete','Mean return'],grouped)
    unavailable=Counter()
    for week in result['weeks']:
        for event in week['audit']:
            if event['kind']=='data_unavailable': unavailable[event['reason']]+=1
    content+='<h2>Unavailable parent evaluations</h2>'+table(['Reason','Minute evaluations (not distinct stocks)'],sorted(unavailable.items()))
    content+='<h2>All entries and outcomes</h2><input id="filter" placeholder="Filter ticker, week, TF or horizon"><section id="details">'+details+'</section>'
    content+='<script>document.getElementById("filter").oninput=e=>{let q=e.target.value.toLowerCase();document.querySelectorAll("#details tbody tr").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))};</script></html>'
    (path/'report.html').write_text(content)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('pilot','year'),default='pilot')
    p.add_argument('--start',type=date.fromisoformat,default=date(2025,11,1))
    p.add_argument('--end',type=date.fromisoformat,default=date(2026,11,1),help='Exclusive; completed study window')
    p.add_argument('--symbols',nargs='+',default=list(PILOT_SYMBOLS))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--download-workers',type=int,choices=(1,2),default=2,
                   help='Historical GET-only download processes; no live scanner workers')
    p.add_argument('--credentials-file',type=Path,help='Private server env; only Alpaca credentials read')
    args=p.parse_args(argv)
    if args.credentials_file:
        for line in args.credentials_file.read_text().splitlines():
            k,sep,v=line.strip().removeprefix('export ').partition('=')
            if sep and k in ('ALPACA_API_KEY','ALPACA_SECRET_KEY'): os.environ[k]=v.strip().strip('"\'')
    if args.end>datetime.now(NY).date() or args.start>=args.end:
        p.error('Use a completed historical date range')
    symbols=list(dict.fromkeys(args.symbols))
    if not symbols or any(not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',s) for s in symbols): p.error('Invalid stock symbol')
    days=[date.fromisoformat(s) for s in PILOT_DAYS] if args.mode=='pilot' else week_dates(args.start,args.end)
    days=[d for d in days if args.start<=d<args.end]
    if not days:p.error('No test weeks in range')
    history=History(args.cache);rules=load_rules()
    result=dict(mode=args.mode,start=str(args.start),end_exclusive=str(args.end),symbols=symbols,rules=rules,
        generated_at=datetime.now(UTC).isoformat(),engine_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        detector_sha256=hashlib.sha256(Path(sys.modules['scanner'].__file__).read_bytes()).hexdigest(),
        feed='Alpaca SIP',adjustment='split',delay_ms=0,costs=0,entries=[],weeks=[],failures=[],
        limitations=['Fixed selected stock cohort; not a historical S&P 500 membership backtest. Survivorship/selection bias remains.',
            'Historical corrected bars, not an as-published feed; historical corrections cannot be replayed.',
            'Weekly warm-up uses historical as-of symbol mapping (including META/FB). Intraday ranges use explicit tickers; additional cohorts require rename/delisting eligibility checks.',
            'Strategy mode admits lower matches at close without a prior provisional notification. Four validated closes per stock/TF; notification expiry and invalid provisional-slot consumption are excluded.',
            'Weekly historical bars and prior completed days are native; forming last-day contribution uses regular-session minutes, matching the watch snapshot construction.',
            'Parent identity freezes at first admission; invalid parents are not recycled. Five validated stocks per direction stop new admissions, not already admitted parents.',
            'Selected summary takes first chronological entry per stock/week/direction and lowest TF only for simultaneous entries; later signals cannot replace earlier trades.',
            '1h/4h horizons count scheduled 04:00–20:00 trading minutes, not observed bars. Session horizons end at the next, third and fifth regular closes strictly after entry.',
            'No costs, fills, borrow checks, position sizing, stop/target exits or portfolio P&L. Extrema timestamps identify minute bars, not exact trade seconds.'])
    for day in days:
        spec=week_spec(day);cases={}
        print(f'Fetching {day}: {len(symbols)} stocks, {args.download_workers} historical download processes',flush=True)
        with ProcessPoolExecutor(max_workers=args.download_workers) as pool:
            for symbol,case,receipts,error in pool.map(download_case,[(args.cache,s,spec) for s in symbols]):
                history.receipts.extend(receipts)
                if error: result['failures'].append(dict(week=str(day),symbol=symbol,error=error))
                else: cases[symbol]=case
                print(f'History {day} {symbol}: '+('excluded '+error if error else 'ready'),flush=True)
        entries,review=replay(spec,cases,rules)
        for e in entries:
            case=cases[e['ticker']]
            e['outcomes']={h:outcome(e,case['minutes'],horizon_end(e['entry_time'],h),case['outcome_end'])
                           for h in ('1h','4h','next_close','3_sessions','5_sessions')}
        result['entries']+=entries;result['weeks'].append(review)
        result['selected']=select_first(result['entries']);result['summary']=summaries(result['selected'])
        result['all_signal_summary']=summaries(result['entries'])
        result['data_receipts']=history.receipts
        report(result,args.output)
        print(f'Completed {day}: {len(entries)} validated entries',flush=True)
    print(json.dumps(dict(mode=args.mode,entries=len(result['entries']),selected=len(result['selected']),failures=len(result['failures']))))
    return 2 if result['failures'] else 0


if __name__=='__main__':
    raise SystemExit(main())
