"""Read-only Binance spot weekly RSI research; never imports a runner or opens a database."""
from __future__ import annotations
import argparse, csv, hashlib, html, io, json, random, re, ssl, statistics, sys, time, urllib.request, urllib.parse, zipfile
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scanner import Candle, check_candles, detect, minimum_history
from platform_app import load_rules
from stock_rsi_watch import same_pair
UTC=timezone.utc
MINUTES={'15m':15,'30m':30,'1h':60,'4h':240}
NAMES={'XTZUSDT':'Tezos','STXUSDT':'Stacks','BNBUSDT':'BNB','AXSUSDT':'Axie Infinity',
       'FILUSDT':'Filecoin','DCRUSDT':'Decred'}
DAY=86400000
HORIZONS={'1h':3600000,'4h':14400000,'1d':DAY,'3d':3*DAY,'5d':5*DAY}
PILOT=('2025-11-30','2026-03-08','2026-03-15','2026-04-05')
def ms(dt): return int(dt.timestamp()*1000)
def stamp(t): return datetime.fromtimestamp(t/1000,UTC).isoformat(timespec='minutes')
def week_spec(sunday,after_hours=24):
    if sunday.weekday()!=6: raise ValueError('Weekly end day must be Sunday UTC')
    close=ms(datetime.combine(sunday+timedelta(days=1),datetime.min.time(),UTC))
    return dict(week=str(sunday-timedelta(days=6)),last_day=str(sunday),period_start=close-7*DAY,
                start=close-3*3600000,close=close,expires=close+after_hours*3600000)
def week_dates(start,end):
    d=start+timedelta(days=(6-start.weekday())%7)
    out=[]
    while d<end: out.append(d); d+=timedelta(days=7)
    return out
class History:
    def __init__(self,path):
        self.path=Path(path); self.path.mkdir(parents=True,exist_ok=True);self.receipts=[]
        import certifi
        self.context=ssl.create_default_context(cafile=certifi.where())
    def raw(self,url):
        p=self.path/hashlib.sha256(url.encode()).hexdigest()
        if not p.exists():
            for attempt in range(4):
                try:
                    req=urllib.request.Request(url,headers={'User-Agent':'RSI-readonly-research/1.0'})
                    with urllib.request.urlopen(req,timeout=60,context=self.context) as r: data=r.read()
                    tmp=p.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(p);break
                except (OSError,ValueError):
                    if attempt==3: raise
                    time.sleep(2**attempt)
        data=p.read_bytes()
        self.receipts.append(dict(url=url,file=p.name,sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)))
        return data
    def api(self,endpoint,**params):
        return json.loads(self.raw('https://api.binance.com/api/v3/'+endpoint+'?'+urllib.parse.urlencode(params)))
    def klines(self,symbol,frame,start,end):
        rows=[]
        while start<end:
            page=self.api('klines',symbol=symbol,interval=frame,startTime=start,endTime=end-1,limit=1000)
            if not isinstance(page,list):raise ValueError('Invalid kline response')
            if not page:break
            rows.extend(page); nxt=int(page[-1][6])+1
            if nxt<=start:raise ValueError('Pagination failed')
            start=nxt
            if len(page)<1000:break
        return parse_rows(rows,end)
    def month(self,symbol,frame,month):
        url=f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/{frame}/{symbol}-{frame}-{month}.zip'
        data=self.raw(url);checksum=self.raw(url+'.CHECKSUM').decode().split()[0]
        if hashlib.sha256(data).hexdigest()!=checksum:raise ValueError('Archive checksum mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=z.namelist()
            if len(names)!=1:raise ValueError('Unexpected archive layout')
            rows=list(csv.reader(io.StringIO(z.read(names[0]).decode())))
        return parse_rows(rows,ms(datetime.now(UTC)))
    def archive(self,symbol,frame,start,end):
        d=datetime.fromtimestamp(start/1000,UTC).date().replace(day=1);bars=[]
        while ms(datetime.combine(d,datetime.min.time(),UTC))<end:
            bars.extend(self.month(symbol,frame,d.strftime('%Y-%m')))
            d=(d+timedelta(days=32)).replace(day=1)
        return [b for b in bars if b.start>=start and b.end<=end]
def parse_rows(rows,end):
    out=[]
    for r in rows:
        a,z=int(r[0]),int(r[6]);div=1000 if a>100000000000000 else 1
        out.append(Candle(a//div,(z+1)//div,*(float(r[i]) for i in (1,2,3,4))))
    return check_candles(out,end)
STABLE={'USDC','USDP','TUSD','BUSD','DAI','FDUSD','USDD','USDS','USD1','USDE','USDF','USDT','EUR','EURI','AEUR','XUSD','PYUSD','RLUSD','UST','USTC','PAX','EURC'}
def candidate_symbols(history):
    info=history.api('exchangeInfo')
    symbols=sorted(s['symbol'] for s in info['symbols'] if s['status']=='TRADING' and s.get('isSpotTradingAllowed')
        and s['quoteAsset']=='USDT' and s['baseAsset'] not in STABLE
        and not re.search(r'(UP|DOWN|BULL|BEAR)$',s['baseAsset']))
    return symbols

def universe(history,seed,count,first_start):
    symbols=candidate_symbols(history)
    order=list(symbols);random.Random(seed).shuffle(order);selected=[];checks=[]
    for symbol in order:
        bars=history.klines(symbol,'1w',first_start-275*7*DAY,first_start)
        ok=len(bars)>=250 and bars[-1].end==first_start and all(b.end-b.start==7*DAY for b in bars) and all(a.end==b.start for a,b in zip(bars,bars[1:]))
        checks.append(dict(symbol=symbol,weekly_bars=len(bars),eligible=ok))
        if ok:selected.append(symbol)
        if len(selected)==count:break
    if len(selected)<count:raise ValueError('Insufficient warmup-qualified random coins')
    return dict(seed=seed,current_candidate_pool=symbols,permuted_coverage_checks=checks,selected=selected,
      selection='Seeded shuffle of current active Binance spot USDT pairs, retain first with >=250 contiguous completed weekly candles before first pilot week; eligibility uses history coverage only.',
      exclusions='Listed stablecoin bases and UP/DOWN/BULL/BEAR token suffixes excluded. Current listing survivor bias; not historical full market universe.')
def weekly_universe(history, symbols, spec):
    """Coverage eligibility is recomputed before each week's open; never outcome-ranked."""
    selected, checks, errors = [], [], []
    cutoff = spec['period_start']
    for symbol in sorted(symbols):
        try:
            bars = history.klines(symbol, '1w', cutoff-275*7*DAY, cutoff)
            tail = bars[-250:]
            ok = (len(tail)==250 and tail[-1].end==cutoff
                  and all(b.end-b.start==7*DAY for b in tail)
                  and all(a.end==b.start for a,b in zip(tail,tail[1:])))
            checks.append(dict(symbol=symbol,weekly_bars=len(bars),eligible=ok))
            if ok: selected.append(symbol)
        except (OSError, ValueError, KeyError) as exc:
            checks.append(dict(symbol=symbol,eligible=False,reason=str(exc)))
            errors.append(dict(week=spec['week'],symbol=symbol,stage='weekly_eligibility',reason=str(exc)))
    return dict(selected=selected,checks=checks,errors=errors)

def weekly_record(audit, eligibility, entries, errors):
    qualifying = [dict(symbol=a['symbol'],direction=a['direction'],timeframe='weekly',at=a['at'])
                  for a in audit['audit'] if a['kind']=='higher_admitted']
    matches = [dict(symbol=e['ticker'],direction=e['direction'],timeframe=e['tf'],at=e['entry_time'])
               for e in entries]
    incomplete = bool(errors) or any(a['kind']=='data_unavailable' for a in audit['audit'])
    return dict(audit, eligibility=eligibility, qualifying=qualifying, closed_matches=matches,
                no_match=not entries, status='incomplete' if incomplete else ('matched' if entries else 'no_match'))

def fetch_case(h,symbol,spec):
    sunday=spec['close']-DAY;finish=spec['expires']+5*DAY
    return dict(symbol=symbol,weekly=h.klines(symbol,'1w',spec['period_start']-275*7*DAY,spec['period_start']),
      daily=h.klines(symbol,'1d',spec['period_start'],sunday),
      warm=h.archive(symbol,'15m',sunday-60*DAY,sunday),
      minutes=h.archive(symbol,'1m',sunday,finish),outcome_end=finish)
def parent_at(case,spec,at):
    cutoff=min(at,spec['close']);sunday=spec['close']-DAY
    daily=[b for b in case['daily'] if spec['period_start']<=b.start and b.end<=sunday]
    if len(daily)!=6 or any(b.start!=spec['period_start']+i*DAY or b.end!=b.start+DAY for i,b in enumerate(daily)):
        raise ValueError('Missing current-week daily history')
    visible=[b for b in case['minutes'] if sunday<=b.start and b.end<=cutoff]
    if not visible or visible[0].start!=sunday or visible[-1].end!=cutoff or len(visible)!=(cutoff-sunday)//60000:
        raise ValueError('Missing current-week minute history')
    pieces=daily+visible
    partial=Candle(spec['period_start'],cutoff,pieces[0].open,max(b.high for b in pieces),min(b.low for b in pieces),pieces[-1].close)
    return [b for b in case['weekly'] if b.end<=spec['period_start']]+[partial]
def aggregate(pieces,frame):
    width=MINUTES[frame]*60000;groups=defaultdict(list);out=[]
    for b in pieces: groups[b.start//width*width].append(b)
    for start,rows in sorted(groups.items()):
        if rows[0].start!=start or rows[-1].end!=start+width or any(a.end!=b.start for a,b in zip(rows,rows[1:])):continue
        out.append(Candle(start,start+width,rows[0].open,max(b.high for b in rows),min(b.low for b in rows),rows[-1].close))
    return out
def closed_lower_signals(case,spec,rules):
    pieces=case['warm']+[b for b in case['minutes'] if b.end<=spec['expires']];result=defaultdict(list);coverage={}
    for frame in MINUTES:
        bars=aggregate(pieces,frame);coverage[frame]=sum(b.end<=spec['start'] for b in bars)
        index={b.start:i for i,b in enumerate(bars)}
        if coverage[frame]<250:continue
        for signal in detect(bars,rules,case['symbol'],frame,provisional=True,include_hidden=False):
            i=index[signal['pivot2']];b=bars[i]
            if spec['start']<=b.end<spec['expires'] and i>=249 and all(a.end==z.start for a,z in zip(bars[i-249:i],bars[i-248:i+1])):
                result[b.end].append((frame,b,signal))
    return result,coverage
def current_signals(bars, rules, symbol, frame):
    if len(bars) < max(250, minimum_history(rules)):
        raise ValueError('Insufficient history: 250 candles required')
    return [s for s in detect(bars, rules, symbol, frame, provisional=True, include_hidden=False)
            if s['pivot2'] == bars[-1].start]

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

def select_first(entries):
    """Executable chronological summary: first entry; lowest TF only breaks ties."""
    chosen = {}
    for e in sorted(entries,key=lambda e:(e['entry_time'],MINUTES[e['tf']],e['ticker'])):
        chosen.setdefault((e['week'],e['ticker'],e['direction']),e)
    return list(chosen.values())

def outcome(entry,minutes,end,available_end):
    bars=[b for b in minutes if entry['entry_time']<=b.start and b.end<=end]
    if end>available_end or not bars or bars[0].start!=entry['entry_time'] or bars[-1].end!=end or any(a.end!=b.start for a,b in zip(bars,bars[1:])):
        return dict(status='incomplete_history',target_end=end,bars=len(bars))
    high=max(bars,key=lambda b:b.high);low=min(bars,key=lambda b:b.low);e=entry['entry'];long=entry['direction']=='bullish'
    gain=max(0,(high.high-e if long else e-low.low)/e*100);dd=max(0,(e-low.low if long else high.high-e)/e*100)
    return dict(status='complete',target_end=end,bars=len(bars),exit_price=bars[-1].close,
      return_pct=(bars[-1].close-e if long else e-bars[-1].close)/e*100,max_gain_pct=gain,max_dd_pct=dd,
      max_gain_time=(high.start if long else low.start) if gain>0 else None,
      max_dd_time=(low.start if long else high.start) if dd>0 else None)
def summaries(entries):
    result=[]
    for horizon in HORIZONS:
        for frame in MINUTES:
            group=[e['outcomes'][horizon] for e in entries if e['tf']==frame];valid=[o for o in group if o['status']=='complete']
            result.append(dict(horizon=horizon,tf=frame,signals=len(group),complete=len(valid),
              mean_return_pct=statistics.mean(o['return_pct'] for o in valid) if valid else None,
              mean_max_gain_pct=statistics.mean(o['max_gain_pct'] for o in valid) if valid else None,
              mean_max_dd_pct=statistics.mean(o['max_dd_pct'] for o in valid) if valid else None))
    return result
def report(result,path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    (path/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    def fmt(n):return 'N/A' if n is None else f'{n:+.2f}%'
    def table(head,rows):return '<table><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in head)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'
    def summary(key):return table(['Horizon','TF','Signals','Complete','Avg return','Avg max gain','Avg max DD'],[[s['horizon'],s['tf'],s['signals'],s['complete'],fmt(s['mean_return_pct']),fmt(s['mean_max_gain_pct']),fmt(s['mean_max_dd_pct'])] for s in result[key]])
    details=[]
    for e in result['entries']:
        for horizon,o in e['outcomes'].items():
            details.append([e['ticker'],e['name'],e['week'],e['tf'],'Long' if e['direction']=='bullish' else 'Short',e['entry'],stamp(e['entry_time']),e['higher_status'],e['lower_slot'],horizon,o['status'],fmt(o.get('return_pct')),fmt(o.get('max_gain_pct')),stamp(o['max_gain_time']) if o.get('max_gain_time') else 'N/A',fmt(o.get('max_dd_pct')),stamp(o['max_dd_time']) if o.get('max_dd_time') else 'N/A'])
    content='<!doctype html><meta charset="utf-8"><title>Crypto weekly RSI pilot</title><style>body{font:15px system-ui;margin:32px;color:#142333}table{border-collapse:collapse;margin:20px 0}td,th{border:1px solid #ccc;padding:7px;text-align:right}th{background:#eaf2f9}p{max-width:1000px}h2{margin-top:36px}</style><h1>Crypto weekly RSI research</h1>'
    content+=f"<p><strong>{len(result['entries'])} closed lower matches; {len(result['selected_entries'])} chronological selected entries; {len(result['errors'])} download failures.</strong> {len(result['weeks'])} weeks replayed. A zero-match pilot with complete coverage supplies no performance estimate.</p>"
    content+=table(['Week beginning UTC','Coins replayed','Weekly parents','Closed lower matches','Unavailable parent evaluations'],[[w['week'],len(w['coverage']),w['parents'],w['entries'],sum(a['kind']=='data_unavailable' for a in w['audit'])] for w in result['weeks']])
    content+='<p>'+html.escape(json.dumps(result['configuration']))+'</p><p>'+html.escape(result['universe']['selection']+' '+result['universe']['exclusions'])+'</p>'
    content+='<p>Pairs: '+', '.join(result['universe']['selected'])+'. All times UTC. Prices and returns in USDT; USDT is not USD. Hypothetical short returns on spot quotes; no borrow assumptions. Zero delays and costs. Entry requires closed lower candle; weekly may still be provisional. Max gain/DD are subsequent minute-bar excursions, not executable exits or realized P&amp;L. No present-day price used. Overlapping trades are not a portfolio.</p>'
    content+='<h2>Weekly candidate scan</h2>'+table(['Week','Status','Eligible pairs','Symbol','Direction','Timeframe'],[[w['week'],w.get('status','legacy'),len(w.get('eligibility',{}).get('selected',[])),q['symbol'],q['direction'],q['timeframe']] for w in result['weeks'] for q in (w.get('qualifying',[])+w.get('closed_matches',[]) or [dict(symbol='No matches',direction='—',timeframe='—')])])
    content+='<h2>All closed matches, grouped by lower timeframe</h2>'+summary('all_signal_summary')
    content+='<h2>First chronological entry per coin/week/direction</h2><p>Lowest timeframe breaks simultaneous ties only; later lower timeframes cannot replace an earlier entry.</p>'+summary('selected_summary')
    content+='<h2>Signal and outcome details</h2>'+table(['Pair','Name','Week','TF','Side','Entry USDT','Entry UTC','Weekly','Slot','Horizon','Data','Return','Max gain','Gain time UTC','Max DD','DD time UTC'],details)
    content+='<h2>Data exclusions</h2><pre>'+html.escape(json.dumps(result['errors'],indent=2))+'</pre><p>Raw audit, universe eligibility checks and source SHA256 receipts: <a href="results.json">results.json</a></p>'
    (path/'report.html').write_text(content)
def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',default='research/data/crypto-cache');p.add_argument('--output',default='research/results/crypto-pilot');p.add_argument('--annual',action='store_true');p.add_argument('--sampled-pilot',action='store_true',help='Reproduce legacy six-coin pilot only; incompatible with --annual');p.add_argument('--seed',type=int,default=20261127);p.add_argument('--coins',type=int,default=6);p.add_argument('--after-hours',type=int,default=24,choices=(0,24,48));args=p.parse_args()
    if args.annual and args.sampled_pilot: p.error('--annual must rescan the full eligible universe; omit --sampled-pilot')
    h=History(args.cache);rules=load_rules();start=date(2025,11,1);end=date(2026,11,1)
    dates=week_dates(start,end) if args.annual else [date.fromisoformat(s) for s in PILOT]
    specs=[week_spec(d,args.after_hours) for d in dates]
    symbols=candidate_symbols(h)
    if args.sampled_pilot:
        pool=universe(h,args.seed,args.coins,week_spec(week_dates(start,end)[0])['period_start'])
    else:
        pool=dict(current_candidate_pool=symbols,selected=symbols,
          selection='Every historical week independently checks all current candidate pairs for 250 contiguous completed weekly candles, then causally replays every eligible pair. No fixed six-coin cohort.',
          exclusions='Current active Binance spot USDT universe snapshot: delisted pairs are absent (survivorship bias). Historical listing/trading status is not reconstructed; weekly history coverage is the eligibility proxy. Stablecoin bases and leveraged-token suffixes excluded.')
    print('Universe size:',len(pool['selected']),flush=True)
    result=dict(configuration=dict(study_start=str(start),study_end_exclusive=str(end),pilot=not args.annual,
      venue='Binance spot',quote='USDT',seed=args.seed if args.sampled_pilot else None,selection_mode='legacy_sampled_pilot' if args.sampled_pilot else 'weekly_full_universe',weekly_boundary='Monday 00:00 UTC',watch_before_hours=3,
      watch_after_hours=args.after_hours,watch_window_status='24h continuation explicitly user-confirmed; CLI 0h/48h are alternative research scenarios',
      horizons='Elapsed 24/7 time: 1h,4h,1d,3d,5d',delay=0,costs=0,weekly_warmup=250,
      max_closed_matches_per_pair_tf_week=4,source_commit='02d4ecb0261f3437ac186d867c7f34d365a65f71',
      rules=rules,runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),universe=pool,entries=[],weeks=[],errors=[])
    for spec in specs:
        eligibility=weekly_universe(h,pool['selected'],spec)
        week_errors=list(eligibility['errors'])
        cases={}
        for symbol in eligibility['selected']:
            try:
                cases[symbol]=fetch_case(h,symbol,spec)
                print('Downloaded',spec['week'],symbol,flush=True)
            except (OSError,ValueError,KeyError) as exc:
                week_errors.append(dict(week=spec['week'],symbol=symbol,stage='replay_data',reason=str(exc)))
        entries,audit=replay(spec,cases,rules)
        for e in entries:
            case=cases[e['ticker']];e['outcomes']={label:outcome(e,case['minutes'],e['entry_time']+duration,case['outcome_end']) for label,duration in HORIZONS.items()}
        result['errors']+=week_errors
        result['entries']+=entries;result['weeks'].append(weekly_record(audit,eligibility,entries,week_errors))
        result['all_signal_summary']=summaries(result['entries']);selected=select_first(result['entries']);result['selected_entries']=selected;result['selected_summary']=summaries(selected);result['receipts']=list({r['url']:r for r in h.receipts}.values())
        report(result,args.output)
        print('Replayed',spec['week'],'entries',len(entries),'parents',audit['parents'],flush=True)
    print(json.dumps(dict(entries=len(result['entries']),selected=len(result['selected_entries']),errors=len(result['errors']),output=str(Path(args.output).resolve()))),flush=True)
if __name__=='__main__':main()
