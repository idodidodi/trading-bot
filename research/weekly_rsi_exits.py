"""Cache-only retest and exit research. No network, worker, orders or notifications."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scanner import Candle, check_candles
from market_calendar import previous_session
from research.weekly_rsi_backtest import fetch_case, week_spec, stamp, eligible

STOPS = (.1, .25, .5, .75, 1, 1.5, 2, 3, 4, 5, 7.5, 10, 15, 20)
TARGETS = (.25, .5, .75, 1, 1.5, 2, 3, 4, 5, 7.5, 10, 15, 20)
REWARDS = (.5, 1, 1.5, 2, 3, 4, 5, 6, 8)
NOTIONAL = 1000

def key(e):
    return '|'.join(str(e[k]) for k in ('week','ticker','direction','tf','entry_time'))

def configurations():
    targets=set(TARGETS)
    targets.update(round(s*r,8) for s in STOPS for r in REWARDS if s*r<=20)
    # Every target is tested with every stop, including those found in R-based grids.
    pairs = {(s,t) for s in STOPS for t in targets}
    return [dict(stop_pct=s,target_pct=t,reward_risk=t/s) for s,t in sorted(pairs)]

def retest(e, bars, tolerance=.001):
    """Require favorable departure at a minute close; only later bars can retest."""
    level=e['lower_signal']['price2'];lo=level*(1-tolerance);hi=level*(1+tolerance)
    departure=None;end=e['outcomes']['5_sessions']['target_end']
    for b in bars:
        if b.start < e['entry_time'] or b.end > end:continue
        if departure is not None and b.start >= departure and b.low <= hi and b.high >= lo:
            return dict(level=level,band_low=lo,band_high=hi,departed_at=departure,
                retested=True,retest_minute=b.start,minutes_after_entry=(b.start-e['entry_time'])/60000)
        if departure is None and (b.close>hi if e['direction']=='bullish' else b.close<lo):
            departure=b.end
    return dict(level=level,band_low=lo,band_high=hi,departed_at=departure,
                retested=False,retest_minute=None,minutes_after_entry=None)

def simulate(e, bars, config, optimistic=False):
    """Observed-minute first-touch brackets, adverse gap opens, terminal time exit."""
    entry=e['entry'];long=e['direction']=='bullish';side=1 if long else -1
    stop=entry*(1-side*config['stop_pct']/100) if config['stop_pct'] is not None else None
    target=entry*(1+side*config['target_pct']/100) if config['target_pct'] is not None else None
    end=e['outcomes']['5_sessions']['target_end'];last=None
    def finish(price, at, reason, ambiguous=False, gap=False):
        ret=side*(price-entry)/entry*100
        return dict(status='complete',return_pct=ret,pnl_usd=ret*NOTIONAL/100,
            realized_r=ret/config['stop_pct'] if config['stop_pct'] is not None else None,exit_price=price,exit_minute=at,reason=reason,
            ambiguous=ambiguous,gap_stop=gap,stop_price=stop,target_price=target)
    for b in bars:
        if b.start<e['entry_time'] or b.end>end:continue
        last=b
        if stop is not None and (b.open<=stop if long else b.open>=stop):
            return finish(b.open,b.start,'stop',gap=(b.open<stop if long else b.open>stop))
        if target is not None and (b.open>=target if long else b.open<=target):
            return finish(target,b.start,'target')
        hit_stop=stop is not None and (b.low<=stop if long else b.high>=stop)
        hit_target=target is not None and (b.high>=target if long else b.low<=target)
        if hit_stop and hit_target:
            return finish(target if optimistic else stop,b.start,'target' if optimistic else 'stop',True)
        if hit_stop:return finish(stop,b.start,'stop')
        if hit_target:return finish(target,b.start,'target')
    if last is None or last.end<end-120000:
        return dict(status='incomplete',reason='missing_or_stale_terminal_price')
    return finish(last.close,last.start,'time_exit')

def metrics(outcomes):
    valid=[o for o in outcomes if o['status']=='complete']
    values=[o['return_pct'] for o in valid]
    gains=sum(v for v in values if v>0);losses=-sum(v for v in values if v<0)
    return dict(entries=len(outcomes),complete=len(valid),pnl_usd=sum(o['pnl_usd'] for o in valid),
        mean_return_pct=statistics.mean(values) if values else None,
        win_rate_pct=100*sum(v>0 for v in values)/len(values) if values else None,
        mean_r=statistics.mean(o['realized_r'] for o in valid if o['realized_r'] is not None) if any(o['realized_r'] is not None for o in valid) else None,
        profit_factor=gains/losses if losses else None,
        no_losses=bool(values) and not losses,
        ambiguous=sum(o['ambiguous'] for o in valid),gap_stops=sum(o['gap_stop'] for o in valid),
        exits=dict(Counter(o['reason'] for o in valid)))

def best_index(configs, outcomes, indices):
    if not indices:raise ValueError('No training entries')
    # Tie-break is predetermined: lower stop, then lower target, never holdout outcome.
    return max(range(len(configs)), key=lambda i:(sum(outcomes[i][j]['pnl_usd'] for j in indices),
                   -configs[i]['stop_pct'],-configs[i]['target_pct']))

def split_entries(entries, boundary):
    train=[i for i,e in enumerate(entries) if e['entry_time']<boundary and e['outcomes']['5_sessions']['target_end']<=boundary]
    test=[i for i,e in enumerate(entries) if e['entry_time']>=boundary]
    purged=[i for i,e in enumerate(entries) if e['entry_time']<boundary and e['outcomes']['5_sessions']['target_end']>boundary]
    return train,test,purged

class Capture:
    def __init__(self):self.calls=[]
    def get(self,*args):self.calls.append(args);return []

def load_paths(source, cache):
    """Only read exact input files recorded by the annual run; never fetch new prices."""
    receipts={r['file']:r for r in source['data_receipts']}
    cases={};used=[]
    for e in source['entries']:
        case=(e['week'],e['ticker'])
        if case in cases:continue
        last=previous_session(date.fromisoformat(e['week'])+timedelta(days=4))
        capture=Capture();fetch_case(capture,e['ticker'],week_spec(last))
        symbol,frame,start,end=next(c for c in capture.calls if c[1]=='1Min')
        params=dict(symbols=symbol,timeframe=frame,feed='sip',adjustment='split',asof='-',sort='asc',limit=10000,
            start=datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),end=datetime.fromtimestamp(end/1000,timezone.utc).isoformat())
        name=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()+'.json'
        raw=(Path(cache)/name).read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if name not in receipts or digest!=receipts[name]['sha256']:
            raise ValueError('Historical input checksum mismatch: '+name)
        payload=json.loads(raw)
        if payload['request']!=params:raise ValueError('Historical request mismatch')
        bars=[]
        for row in payload['rows']:
            t=int(datetime.fromisoformat(row['t'].replace('Z','+00:00')).timestamp()*1000)
            bars.append(Candle(t,t+60000,*(float(row[k]) for k in ('o','h','l','c'))))
        cases[case]=[b for b in check_candles(bars,end) if eligible(b)]
        used.append(dict(file=name,sha256=digest,symbol=symbol,week=e['week']))
    return cases,used

def analyze(source, cases):
    entries=source['selected'];configs=configurations()
    paths=[[b for b in cases[(e['week'],e['ticker'])]
            if e['entry_time']<=b.start and b.end<=e['outcomes']['5_sessions']['target_end']] for e in entries]
    if any(not b or b[-1].end<e['outcomes']['5_sessions']['target_end']-120000 for e,b in zip(entries,paths)):
        raise ValueError('Optimization requires the same complete terminal-price cohort for every setting')
    outcomes=[[simulate(e,b,c) for e,b in zip(entries,paths)] for c in configs]
    assert all(o['status']=='complete' for group in outcomes for o in group)
    all_indices=list(range(len(entries)));winner=best_index(configs,outcomes,all_indices)
    middle=source['weeks'][len(source['weeks'])//2]['week']
    boundary=week_spec(previous_session(date.fromisoformat(middle)+timedelta(days=4)))['start']
    train,test,purged=split_entries(entries,boundary)
    learned=best_index(configs,outcomes,train)
    baseline=[dict(status='complete',return_pct=e['outcomes']['5_sessions']['return_pct'],
        pnl_usd=e['outcomes']['5_sessions']['return_pct']*NOTIONAL/100,realized_r=0,
        reason='time_exit',ambiguous=False,gap_stop=False) for e in entries]
    baseline_summary=metrics(baseline);baseline_summary['mean_r']=None
    controls=[dict(stop_pct=None,target_pct=None,reward_risk=None)]
    controls.extend(dict(stop_pct=None,target_pct=t,reward_risk=None) for t in sorted({c['target_pct'] for c in configs}))
    controls.extend(dict(stop_pct=s,target_pct=None,reward_risk=None) for s in STOPS)
    control_outcomes=[[simulate(e,b,c) for e,b in zip(entries,paths)] for c in controls]
    assert all(abs(a['return_pct']-b['return_pct'])<1e-9 for a,b in zip(baseline,control_outcomes[0]))
    control_ranking=[dict(config=c,**metrics(group)) for c,group in zip(controls,control_outcomes)]
    control_ranking.sort(key=lambda r:-r['pnl_usd'])
    ranking=[dict(config=c,**metrics(outcomes[i])) for i,c in enumerate(configs)]
    ranking.sort(key=lambda r:(-r['pnl_usd'],r['config']['stop_pct'],r['config']['target_pct']))
    per_entry=[]
    for j,e in enumerate(entries):
        per_entry.append(dict(entry=e,retest=retest(e,paths[j]),optimized_exit=outcomes[winner][j],
            chronological_exit=outcomes[learned][j] if j in test else None,
            partition='training' if j in train else 'holdout' if j in test else 'purged'))
    by_stock=[]
    for ticker in sorted(source['symbols']):
        indices=[i for i,e in enumerate(entries) if e['ticker']==ticker]
        fitted=best_index(configs,outcomes,indices) if indices else None
        by_stock.append(dict(ticker=ticker,entries=len(indices),retests=sum(per_entry[i]['retest']['retested'] for i in indices),
            baseline_pnl_usd=sum(baseline[i]['pnl_usd'] for i in indices),
            global_fit=metrics([outcomes[winner][i] for i in indices]),
            descriptive_stock_fit=None if fitted is None else dict(config=configs[fitted],**metrics([outcomes[fitted][i] for i in indices]))))
    optimistic=[simulate(e,b,configs[winner],True) for e,b in zip(entries,paths)]
    return dict(configuration=dict(notional_usd=NOTIONAL,entry='Original lower candle close; no retest-entry filter',
        retest='P2 close price +/-0.1%; favorable minute-close departure then a later observed minute range touches the band',
        holding_limit='Fifth regular market close after original entry',fees=0,delay=0,compounding=False,
        stop_grid=list(STOPS),target_grid=list(TARGETS),reward_risk_grid=list(REWARDS),maximum_target_pct=20,
        candidates=len(configs),tie_break='Lower stop, then lower target',gap_model='Stop at next observed open beyond stop; target at limit',
        intraminute_model='Stop first if both barriers touch; optimistic sensitivity separately'),
        search_history='Exploratory: initial 176 brackets with stops 0.25–10%; after inspecting the boundary winner, expanded to 0.1–20% and added one-sided controls. A 223-pair intermediate grid omitted some target/stop combinations; the final grid crosses every percentage/R-derived target with every stop for a fair comparison. No untouched validation is claimed.',
        best_full_sample=dict(config=configs[winner],**metrics(outcomes[winner])),
        tied_best_brackets=[configs[i] for i,group in enumerate(outcomes) if all(abs(a['return_pct']-b['return_pct'])<1e-9 for a,b in zip(group,outcomes[winner]))],
        controls=control_ranking,
        best_full_sample_optimistic=metrics(optimistic),baseline=baseline_summary,
        temporal_validation=dict(boundary=boundary,training_entries=len(train),holdout_entries=len(test),purged_entries=len(purged),
            learned_config=configs[learned],training=metrics([outcomes[learned][i] for i in train]),
            holdout=metrics([outcomes[learned][i] for i in test]),
            holdout_baseline_pnl_usd=sum(baseline[i]['pnl_usd'] for i in test),
            limitation='Retrospective chronological sensitivity check: the year and initial boundary result informed grid expansion; not independent external validation'),
        by_stock=by_stock,selected_entries=per_entry,ranked_settings=ranking,
        raw_retests=[dict(entry_key=key(e),ticker=e['ticker'],tf=e['tf'],**retest(e,cases[(e['week'],e['ticker'])])) for e in source['entries']])

def write_report(result,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'retest-exits.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    def num(x):return '—' if x is None else f'{x:,.2f}'
    def table(headers,rows):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(str(h))+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    best=result['best_full_sample'];c=best['config'];v=result['temporal_validation'];learned=v['learned_config']
    text='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Weekly RSI — retests and exits</title><style>body{font:15px system-ui;margin:28px;color:#172b43;background:#f7f9fc}p{max-width:1100px;line-height:1.5}.scroll{overflow:auto;margin:20px 0}table{border-collapse:collapse;background:white;width:100%}th,td{padding:9px;border:1px solid #d8e0ec;white-space:nowrap;text-align:right}th{background:#eaf0f8}summary{padding:14px;cursor:pointer;font-weight:bold}details{background:white;margin:12px 0}li{margin:7px 0}</style><h1>Weekly RSI: retests and stop/target research</h1>'''
    text+=f'<p>52 weeks, 10 stocks, {len(result["selected_entries"])} chronologically selected entries. {sum(e["retest"]["retested"] for e in result["selected_entries"])} retested P2 within the five-close observation window. Entry stays at the original lower candle close; retest is a descriptive outcome, not a future-aware entry filter.</p>'
    text+='<p>Retest: the latest lower-TF P2 close-price level ±0.1%. A minute must first close outside the band in the favorable direction, then a later minute must trade into the band. The departure bar cannot itself count as a retest. Times below are New York time, with offsets.</p>'
    text+=f'<h2>Best fit to this year — not a future optimum</h2><p>Among {result["configuration"]["candidates"]} tested settings: stop <b>{num(c["stop_pct"])}%</b>, take profit <b>{num(c["target_pct"])}%</b>, reward/risk <b>{num(c["reward_risk"])}:1</b>. Gross P&amp;L <b>${num(best["pnl_usd"])}</b> versus baseline ${num(result["baseline"]["pnl_usd"])}. Win rate {num(best["win_rate_pct"])}%; mean return {num(best["mean_return_pct"])}%.</p>'
    text+='<p>P&amp;L assumes $1,000 per entry, no compounding, no costs or feed delay, and unconstrained overlapping positions. It is a sum of hypothetical trade P&amp;L, not a return on a $1,000 account. Stops fill at the next observed open when price gaps beyond them; targets receive no favorable-gap improvement. If neither barrier hits, exit at the fifth regular market close.</p>'
    text+=f'<p>{best["ambiguous"]} winner trades touched stop and target in the same minute; stop-first is used. Target-first sensitivity for these ambiguous bars: ${num(result["best_full_sample_optimistic"]["pnl_usd"])}. {best["gap_stops"]} stop exits occurred at an observed open beyond the stop. Missing intraminute sequencing and sparse/missing trade bars remain limitations.</p>'
    text+=f'<p>{len(result["tied_best_brackets"])} tested brackets produced the same per-entry returns as the best fit. Stop exits: {best["exits"].get("stop",0)}; target exits: {best["exits"].get("target",0)}; time exits: {best["exits"].get("time_exit",0)}. A barrier that never triggers is not identified as an optimal level by this sample. Boundary optima are not evidence that the search found a continuous optimum.</p>'
    text+='<h2>One-sided and no-bracket controls</h2><p>These exploratory controls help identify whether a fitted stop/target is doing useful work. “—” means no barrier of that type; all retain the five-close time exit.</p>'+table(['SL %','TP %','P&L $','Mean return %','Win rate %','Stop exits','Target exits','Time exits'],[[num(r['config']['stop_pct']),num(r['config']['target_pct']),num(r['pnl_usd']),num(r['mean_return_pct']),num(r['win_rate_pct']),r['exits'].get('stop',0),r['exits'].get('target',0),r['exits'].get('time_exit',0)] for r in result['controls'][:12]])
    text+=f'<h2>Chronological validation</h2><p>Boundary: {stamp(v["boundary"])}. Train on {v["training_entries"]} earlier entries; purge {v["purged_entries"]} whose five-close observation window overlaps the boundary; freeze the settings before evaluating {v["holdout_entries"]} later entries. Training selected stop {num(learned["stop_pct"])}%, target {num(learned["target_pct"])}%, reward/risk {num(learned["reward_risk"])}:1. Later-period P&amp;L: <b>${num(v["holdout"]["pnl_usd"])}</b>, versus baseline ${num(v["holdout_baseline_pnl_usd"])}. Later-period win rate: {num(v["holdout"]["win_rate_pct"])}%.</p>'
    text+='<p>'+html.escape(result['search_history'])+'</p><p>This is a retrospective temporal check, not untouched external evidence. Only 37 selected entries and 1–6 per stock were available; stock-specific fits are especially fragile. The fixed ten-stock cohort has survivor/selection bias. Searching many settings can overfit: <a href="https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf">Bailey et al., The Probability of Backtest Overfitting</a>.</p>'
    stock_rows=[[r['ticker'],r['entries'],r['retests'],num(r['baseline_pnl_usd']),num(r['global_fit']['pnl_usd']),num(r['global_fit']['mean_return_pct']),num(r['global_fit']['win_rate_pct']),r['global_fit']['exits'].get('stop',0),r['global_fit']['exits'].get('target',0),r['global_fit']['exits'].get('time_exit',0)] for r in result['by_stock']]
    stock_rows.append(['TOTAL',len(result['selected_entries']),sum(e['retest']['retested'] for e in result['selected_entries']),num(result['baseline']['pnl_usd']),num(best['pnl_usd']),num(best['mean_return_pct']),num(best['win_rate_pct']),best['exits'].get('stop',0),best['exits'].get('target',0),best['exits'].get('time_exit',0)])
    text+='<h2>By stock — same full-year fitted settings applied to every entry</h2>'+table(['Stock','Entries','Retests','Baseline P&L $','Fitted P&L $','Mean return %','Win rate %','Stop exits','Target exits','Time exits'],stock_rows)
    text+='<h2>Leading full-sample settings</h2>'+table(['SL %','TP %','Reward/risk','P&L $','Mean return %','Win rate %','Mean realized R','Ambiguous trades'],[[num(r['config']['stop_pct']),num(r['config']['target_pct']),num(r['config']['reward_risk']),num(r['pnl_usd']),num(r['mean_return_pct']),num(r['win_rate_pct']),num(r['mean_r']),r['ambiguous']] for r in result['ranked_settings'][:12]])
    text+='<details><summary>Stock-specific best fits — descriptive only, tiny samples</summary>'+table(['Stock','Entries','SL %','TP %','Reward/risk','Fitted P&L $'],[[r['ticker'],r['entries'],num(r['descriptive_stock_fit']['config']['stop_pct']),num(r['descriptive_stock_fit']['config']['target_pct']),num(r['descriptive_stock_fit']['config']['reward_risk']),num(r['descriptive_stock_fit']['pnl_usd'])] for r in result['by_stock'] if r['descriptive_stock_fit']])+'</details>'
    for ticker in sorted({r['entry']['ticker'] for r in result['selected_entries']}):
        rows=[]
        for r in result['selected_entries']:
            e=r['entry'];ret=r['retest'];o=r['optimized_exit']
            if e['ticker']!=ticker:continue
            rows.append([e['week'],e['tf'],'Long' if e['direction']=='bullish' else 'Short',stamp(e['entry_time']),num(e['entry']),e['higher_status'],num(ret['level']),'Yes' if ret['retested'] else 'No',stamp(ret['departed_at']) if ret['departed_at'] else '—',stamp(ret['retest_minute']) if ret['retest_minute'] is not None else '—','Yes' if ret['retest_minute'] is not None and ret['retest_minute']<o['exit_minute'] else 'Same minute / unknown' if ret['retest_minute']==o['exit_minute'] else 'No',num(o['stop_price']),num(o['target_price']),o['reason'],stamp(o['exit_minute']),num(o['exit_price']),num(o['pnl_usd']),r['partition']])
        text+=f'<details><summary>{ticker}: entry and retest details</summary>'+table(['Week','TF','Side','Entry time','Entry $','Weekly','P2 level $','Retest in window','Departure close','Retest minute','Retest before fitted exit','Stop $','Target $','Exit reason','Exit minute','Exit $','P&L $','Partition'],rows)+'</details>'
    text+='<h2>Audit and reproducibility</h2><p>All 148 raw matches also have retest annotations in JSON. Minute input SHA256 checksums were checked against the original annual run. Existing annual report and original entries remain unchanged. The complete tested grid, per-entry exits and cache receipts are in <a href="retest-exits.json">retest-exits.json</a>.</p><pre>'+html.escape(json.dumps(result['configuration'],indent=2))+'</pre></html>'
    (output/'retest-exits.html').write_text(text)
    existing=output/'grouped-by-stock.html'
    if existing.exists():
        old=existing.read_text();start='<!-- EXIT_RESEARCH_START -->';end='<!-- EXIT_RESEARCH_END -->'
        if start in old:
            before,_,rest=old.partition(start);_,_,after=rest.partition(end);old=before+after
        banner=start+f'<section><h2>Added: retests and exit research</h2><p>{sum(e["retest"]["retested"] for e in result["selected_entries"])} of 37 selected entries retested the P2 ±0.1% band after a favorable departure. <a href="retest-exits.html">Open retest times, stop/target tests and P&amp;L by stock</a>. The original tables below remain the five-close baseline; fitted results are exploratory.</p></section>'+end
        old=old.replace('<h2>Selected entries, grouped by stock</h2>',banner+'<h2>Selected entries, grouped by stock</h2>')
        existing.write_text(old)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    raw=args.source.read_bytes();source=json.loads(raw)
    if source['mode']!='year' or len(source['weeks'])!=52 or len(source['symbols'])!=10 or source['failures'] or not source['selected']:
        raise ValueError('Expected the completed 52-week, ten-stock annual study with no download failures')
    cases,receipts=load_paths(source,args.cache)
    print(f'Loaded {len(cases)} checksum-verified stock/week histories; evaluating {len(configurations())} settings',flush=True)
    result=analyze(source,cases)
    result.update(source_sha256=hashlib.sha256(raw).hexdigest(),runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_engine_sha256=source['engine_sha256'],data_receipts=receipts,
        generated_at=datetime.now(timezone.utc).isoformat())
    write_report(result,args.output)
    print(json.dumps(dict(best=result['best_full_sample'],validation=result['temporal_validation'])),flush=True)

if __name__=='__main__':main()
