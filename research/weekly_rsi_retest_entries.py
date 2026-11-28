"""Compare causal retest entries with original entries; cache-only account-rule proxy."""
from __future__ import annotations
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone, date, timedelta
import hashlib
import html
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research.weekly_rsi_exits import configurations,load_paths,retest,simulate,metrics,best_index,split_entries,key
from research.weekly_rsi_backtest import week_spec,stamp
from market_calendar import previous_session

DAY=86400000

def retest_entry(original,bars,watch_only=False):
    """First observed band touch, never fill a gap at a price outside the band."""
    r=retest(original,bars)
    if not r['retested']:return None,dict(reason='no_retest',observation=r)
    b=next(b for b in bars if b.start==r['retest_minute'])
    if original.get('parent_invalidated_at') is not None and original['parent_invalidated_at']<=b.start:
        return None,dict(reason='weekly_invalidated_before_retest',observation=r)
    expires=week_spec(previous_session(date.fromisoformat(original['week'])+timedelta(days=4)))['expires']
    if watch_only and b.start>=expires:
        return None,dict(reason='watch_expired_before_retest',observation=r)
    e=deepcopy(original)
    e['original_entry_time']=original['entry_time'];e['original_entry']=original['entry']
    e['entry_time']=b.start
    e['entry']=min(r['band_high'],max(r['band_low'],b.open))
    e['entry_model']='First observed price inside P2 band; minute timestamp, not exact tick'
    e['after_weekly_watch_expiry']=b.start>=expires
    e['retest_observation']=r
    return e,dict(reason='filled',observation=r)

def entry_bar_exit(e,b,config,vertices):
    """Walk one OHLC ordering, activate on band touch, then test barriers in order."""
    price=e['entry'];long=e['direction']=='bullish';side=1 if long else -1
    sl=price*(1-side*config['stop_pct']/100) if config['stop_pct'] is not None else None
    tp=price*(1+side*config['target_pct']/100) if config['target_pct'] is not None else None
    active=b.open==price
    for a,z in zip(vertices,vertices[1:]):
        if not active:
            if min(a,z)<=price<=max(a,z):active=True;a=price
            else:continue
        hits=[(abs(v-a),reason,v) for reason,v in [('stop',sl),('target',tp)] if v is not None and min(a,z)<=v<=max(a,z)]
        if hits:
            _,reason,fill=min(hits)
            ret=side*(fill-price)/price*100
            return dict(status='complete',return_pct=ret,pnl_usd=ret*10,
                realized_r=ret/config['stop_pct'] if config['stop_pct'] is not None else None,
                exit_price=fill,exit_minute=b.start,reason=reason,ambiguous=False,gap_stop=False,
                stop_price=sl,target_price=tp)
    if not active:raise ValueError('Entry price was not observed in retest minute')
    return None

def simulate_retest(e,bars,config,optimistic=False):
    first=next(b for b in bars if b.start==e['entry_time'])
    after=deepcopy(e);after['entry_time']=first.end
    continuation=simulate(after,bars,config,optimistic)
    if continuation['status']!='complete' and first.end==e['outcomes']['5_sessions']['target_end']:
        # The original common deadline may be the entry minute's close.
        side=1 if e['direction']=='bullish' else -1
        ret=side*(first.close-e['entry'])/e['entry']*100
        continuation=dict(status='complete',return_pct=ret,pnl_usd=ret*10,
            realized_r=ret/config['stop_pct'] if config['stop_pct'] is not None else None,
            exit_price=first.close,exit_minute=first.start,reason='time_exit',ambiguous=False,gap_stop=False,
            stop_price=e['entry']*(1-side*config['stop_pct']/100) if config['stop_pct'] is not None else None,
            target_price=e['entry']*(1+side*config['target_pct']/100) if config['target_pct'] is not None else None)
    choices=[entry_bar_exit(e,first,config,path) or continuation for path in
             [(first.open,first.high,first.low,first.close),(first.open,first.low,first.high,first.close)]]
    if any(c['status']!='complete' for c in choices):raise ValueError('Incomplete retest outcome')
    chosen=dict((max if optimistic else min)(choices,key=lambda c:c['return_pct']))
    chosen['entry_minute_ambiguous']=abs(choices[0]['return_pct']-choices[1]['return_pct'])>1e-9
    chosen['ambiguous']=chosen['ambiguous'] or chosen['entry_minute_ambiguous']
    return chosen

def funded_audit(entries,outcomes,cases,rules):
    """UTC marked-equity proxy; stop on failure. Minute extrema are conservative bounds."""
    initial=rules['initial_balance'];notional=rules['research_assumptions']['account_trade_notional']
    events=defaultdict(lambda:dict(opens=[],bars=[],exits=[]))
    for i,(e,o) in enumerate(zip(entries,outcomes)):
        events[e['entry_time']]['opens'].append(i)
        events[o['exit_minute']+60000]['exits'].append(i)
        for b in cases[(e['week'],e['ticker'])]:
            if e['entry_time']<=b.start<=o['exit_minute']:events[b.start]['bars'].append((i,b))
    balance=initial;active={};day=None;start_equity=initial;last_activity=None;failure=None
    daily_realized=defaultdict(float);qualified=set();warnings=dict(margin=0,notional=0)
    previous_excess=dict(margin=False,notional=False);max_open=0;max_margin=0;max_value=0
    max_daily_close=0;max_daily_bound=0;min_equity=initial;min_bound=initial;filled=0;closed=0
    def equity():return balance+sum(p['side']*p['units']*(p['mark']-p['entry']) for p in active.values())
    for at,ev in sorted(events.items()):
        if last_activity is not None and at-last_activity>rules['inactivity_days']*DAY:
            failure=dict(reason='inactivity',at=last_activity+rules['inactivity_days']*DAY);break
        current_day=datetime.fromtimestamp(at/1000,timezone.utc).date().isoformat()
        if current_day!=day:
            day=current_day;start_equity=equity()
        for i in ev['exits']:
            if i not in active:continue
            o=outcomes[i];pnl=o['return_pct']/100*notional
            credit_time=o['exit_minute']+(60000 if o['reason']=='time_exit' else 0)
            credit_day=datetime.fromtimestamp(credit_time/1000,timezone.utc).date().isoformat()
            balance+=pnl;del active[i];daily_realized[credit_day]+=pnl;closed+=1;last_activity=at
            if notional>=initial*rules['qualifying_trade_value_fraction'] and abs(o['return_pct'])>=100*rules['qualifying_trade_absolute_return_fraction']:
                qualified.add(credit_day)
        for i in ev['opens']:
            e=entries[i];active[i]=dict(entry=e['entry'],mark=e['entry'],units=notional/e['entry'],side=1 if e['direction']=='bullish' else -1)
            filled+=1;last_activity=at
        worst=balance
        seen=set()
        for i,b in ev['bars']:
            if i not in active:continue
            p=active[i];p['mark']=outcomes[i]['exit_price'] if outcomes[i]['exit_minute']==at else b.close;seen.add(i)
            worst+=p['side']*p['units']*((b.low if p['side']==1 else b.high)-p['entry'])
        worst+=sum(p['side']*p['units']*(p['mark']-p['entry']) for i,p in active.items() if i not in seen)
        eq=equity();min_equity=min(min_equity,eq);min_bound=min(min_bound,worst)
        max_daily_close=max(max_daily_close,start_equity-eq);max_daily_bound=max(max_daily_bound,start_equity-worst)
        margin=len(active)*notional/rules['research_assumptions']['assumed_leverage']
        value=sum(p['units']*p['mark'] for p in active.values())
        max_open=max(max_open,len(active));max_margin=max(max_margin,margin);max_value=max(max_value,value)
        for name,excess in [('margin',margin>initial*rules['funded_margin_fraction']),('notional',value>initial*rules['funded_open_notional_multiple'])]:
            if excess and not previous_excess[name]:warnings[name]+=1
            previous_excess[name]=excess
        if eq<initial*(1-rules['total_loss_fraction']):failure=dict(reason='total_loss_at_minute_mark',at=at);break
        if eq<start_equity-initial*rules['daily_loss_fraction']:failure=dict(reason='daily_loss_at_minute_mark',at=at);break
    end=max(e['outcomes']['5_sessions']['target_end'] for e in entries) if entries else 0
    if failure is None and last_activity is not None and end-last_activity>rules['inactivity_days']*DAY:
        failure=dict(reason='inactivity',at=last_activity+rules['inactivity_days']*DAY)
    cap=initial*rules['evaluation_target_fraction']*rules['evaluation_daily_profit_credit_cap_fraction_of_target']
    counted=sum(min(x,cap) for x in daily_realized.values())
    return dict(status='failed_proxy' if failure else 'no_certain_failure_detected_proxy',failure=failure,
        entries_before_failure=filled,closed_before_failure=closed,balance=balance,equity_at_end=equity(),
        pnl_before_failure=equity()-initial,max_daily_close_loss=max_daily_close,max_daily_adverse_bound=max_daily_bound,
        minimum_marked_equity=min_equity,minimum_conservative_equity_bound=min_bound,
        possible_intraminute_loss_breach=max_daily_bound>initial*rules['daily_loss_fraction'] or min_bound<initial*(1-rules['total_loss_fraction']),
        max_open_positions=max_open,max_margin=max_margin,max_marked_notional=max_value,
        hypothetical_funded_exposure_warnings=warnings,qualifying_days=len(qualified),counted_target_profit=counted,
        profit_target=initial*rules['evaluation_target_fraction'],
        certification='Not certified: underlying-stock proxy, zero costs/funding, minute timing and sparse bars; exposure warnings shown as separate funded-stage overlay')

def analyze(source,cases,rules):
    original=source['selected'];ret=[];records=[]
    for i,e in enumerate(original):
        r,observation=retest_entry(e,cases[(e['week'],e['ticker'])])
        if r is not None:ret.append(r)
        records.append(dict(original_index=i,original_key=key(e),ticker=e['ticker'],original_entry=e,entry=r,**observation))
    configs=configurations();all_results={};winners={};baselines={};audit={};by_stock=[];grids={}
    for label,entries,runner in [('original',original,simulate),('retest',ret,simulate_retest)]:
        groups=[[runner(e,cases[(e['week'],e['ticker'])],c) for e in entries] for c in configs]
        grids[label]=groups
        best=best_index(configs,groups,list(range(len(entries))))
        baseline=[runner(e,cases[(e['week'],e['ticker'])],dict(stop_pct=None,target_pct=None)) for e in entries]
        baselines[label]=metrics(baseline);winners[label]=dict(config=configs[best],**metrics(groups[best]))
        all_results[label]=dict(entries=entries,outcomes=groups[best],ranking=[dict(config=c,**metrics(g)) for c,g in zip(configs,groups)])
        audit[label+'_fitted']=funded_audit(entries,groups[best],cases,rules)
        audit[label+'_baseline']=funded_audit(entries,baseline,cases,rules)
    fixed={}
    for label,entries,runner in [('original',original,simulate),('retest',ret,simulate_retest)]:
        fixed[label]=metrics([runner(e,cases[(e['week'],e['ticker'])],winners['original']['config']) for e in entries])
    for ticker in sorted(source['symbols']):
        row=dict(ticker=ticker)
        for label in all_results:
            group=all_results[label];indices=[i for i,e in enumerate(group['entries']) if e['ticker']==ticker]
            row[label]=metrics([group['outcomes'][i] for i in indices])
        by_stock.append(row)
    boundary=week_spec(previous_session(date.fromisoformat(source['weeks'][26]['week'])+timedelta(days=4)))['start']
    temporal={}
    # Partition by original signal time, so waiting for a retest cannot move a signal into the test set.
    for label in all_results:
        entries=all_results[label]['entries'];runner=simulate if label=='original' else simulate_retest
        groups=grids[label]
        training=[i for i,e in enumerate(entries) if e.get('original_entry_time',e['entry_time'])<boundary and e['outcomes']['5_sessions']['target_end']<=boundary]
        test=[i for i,e in enumerate(entries) if e.get('original_entry_time',e['entry_time'])>=boundary]
        chosen=best_index(configs,groups,training)
        temporal[label]=dict(config=configs[chosen],training=len(training),test=len(test),
            result=metrics([groups[chosen][i] for i in test]))
    strict=[retest_entry(e,cases[(e['week'],e['ticker'])],True)[1] for e in original]
    feasibility={}
    for label in all_results:
        entries=sorted(all_results[label]['entries'],key=lambda e:e['entry_time'])
        latest_possible_activity=None;gaps=[];perfect_exit_upper_bound=0
        for e in entries:
            if latest_possible_activity is not None and e['entry_time']>latest_possible_activity+rules['inactivity_days']*DAY:
                gaps.append(dict(latest_possible_prior_close=latest_possible_activity,
                    inactivity_deadline=latest_possible_activity+rules['inactivity_days']*DAY,next_entry=e['entry_time']))
            latest_possible_activity=max(latest_possible_activity or 0,e['outcomes']['5_sessions']['target_end'])
            bars=[b for b in cases[(e['week'],e['ticker'])] if e['entry_time']<=b.start and b.end<=e['outcomes']['5_sessions']['target_end']]
            favorable=(max(b.high for b in bars)-e['entry']) if e['direction']=='bullish' else (e['entry']-min(b.low for b in bars))
            perfect_exit_upper_bound+=max(0,favorable/e['entry'])*rules['research_assumptions']['account_trade_notional']
        feasibility[label]=dict(unavoidable_inactivity_gaps=gaps,perfect_hindsight_exit_profit_upper_bound=perfect_exit_upper_bound,
            evaluation_target=rules['initial_balance']*rules['evaluation_target_fraction'],
            full_year_surviving_brackets=0 if gaps else None,
            caveat='Fixed sizing and no extra trades, payouts, reactivations or account resets; upper bound is not a tradable strategy')
    return dict(rules=rules,configuration=dict(comparison_notional=1000,opportunities=len(original),
        common_deadline='Original fifth regular close, not five extra closes after the retest',
        retest_order_window='Same five-close observation window as previous retest report; watch-expiry sensitivity separately',
        entry='First observed price in P2 +/-0.1% band after favorable close departure; gaps outside band not filled',
        entry_minute='O-H-L-C and O-L-H-C paths; choose lower full-trade return; these are modeled bounds, not tick certainty',
        sample='Original selected cohort frozen; missing retests stay cash, never replaced by a later signal',
        no_costs=True,candidates=len(configs),funded_sizing='Fixed $5,000 per entry, hypothetical 1x margin, no payouts/reactivations'),
        baselines=baselines,winners=winners,same_original_bracket=fixed,by_stock=by_stock,entries=all_results,
        retest_attempts=records,watch_expiry_sensitivity=dict(filled=sum(x['reason']=='filled' for x in strict),attempts=strict),
        temporal=temporal,account_audits=audit,funded_feasibility=feasibility)

def report(result,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'retest-entry-comparison.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    def n(v):return '—' if v is None else f'{v:,.2f}'
    def table(headers,rows):return '<div class="scroll"><table><tr>'+''.join('<th>'+html.escape(str(v))+'</th>' for v in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</table></div>'
    text='''<!doctype html><html><meta charset="utf-8"><title>P2 retest entry comparison</title><style>body{font:15px system-ui;margin:28px;color:#172b43}p{max-width:1100px;line-height:1.5}.scroll{overflow:auto}td,th{border:1px solid #ccc;padding:8px;white-space:nowrap}table{border-collapse:collapse;margin:18px 0}th{background:#eaf0f8}summary{padding:15px;cursor:pointer}</style><h1>P2 retest entries versus original lower-close entries</h1>'''
    cfg=result['configuration'];original=result['winners']['original'];retested=result['winners']['retest']
    text+=f'<p>{cfg["opportunities"]} original opportunities; {retested["entries"]} retest fills. Unfilled/invalidated orders stay cash. Same original fifth-close deadline; same $1,000 notional, no delays or costs. P2 band ±0.1%, with prior favorable minute-close departure. Retest prices are conditional touches, not guaranteed exchange fills.</p>'
    text+='<p>The retest-entry minute uses both O-H-L-C and O-L-H-C sequences and selects the worse full-trade outcome. It does not use extrema before entry as certain post-entry prices. Actual tick sequencing is unknown. Later bars retain stop-first ordering for dual hits. Settings are retrospective best fits over the same tested grid, not validated future optima.</p>'
    rows=[]
    for label in ['original','retest']:
        b=result['baselines'][label];w=result['winners'][label];c=w['config']
        rows.append([label,w['entries'],n(b['pnl_usd']),n(result['same_original_bracket'][label]['pnl_usd']),n(c['stop_pct']),n(c['target_pct']),n(c['reward_risk']),n(w['pnl_usd']),n(w['win_rate_pct']),w['ambiguous']])
    text+=table(['Entry method','Fills','Five-close P&L $','Original fitted bracket P&L $','Best SL %','Best TP %','Reward/risk','Best fitted P&L $','Win %','Ambiguous trades'],rows)
    text+='<h2>By stock — each method’s fitted bracket</h2>'+table(['Stock','Original fills','Retest fills','Original P&L $','Retest P&L $'],[[r['ticker'],r['original']['entries'],r['retest']['entries'],n(r['original']['pnl_usd']),n(r['retest']['pnl_usd'])] for r in result['by_stock']]+[['TOTAL',original['entries'],retested['entries'],n(original['pnl_usd']),n(retested['pnl_usd'])]])
    text+='<h2>Chronological sensitivity</h2><p>Partitioned by original signal time with overlapping five-close training outcomes purged; the already-inspected year/grid is not independent external validation.</p>'+table(['Method','Training fills','Later fills','Training SL %','Training TP %','Later P&L $'],[[label,r['training'],r['test'],n(r['config']['stop_pct']),n(r['config']['target_pct']),n(r['result']['pnl_usd'])] for label,r in result['temporal'].items()])
    text+=f'<p>If retest orders instead expire at the original weekly watch end (next session open), {result["watch_expiry_sensitivity"]["filled"]} fill. Primary results above retain the previous report’s five-close retest window, so this timing difference is explicit.</p>'
    text+='<h2>HyroTrader One-Step — $100,000 hypothetical account</h2><p>Current rules verified 2026-11-28: 4% initial-balance daily allowance from 00:00 UTC equity; 8% static total allowance; 10% evaluation target; 3 qualifying days; qualifying trade value ≥5% of initial balance and absolute trade return ≥1%; each positive day credits at most 40% of the evaluation target. Stops are not mandatory and One-Step has no prescribed per-trade SL percentage. Overnight/weekend holding is allowed. No 30-day inactivity gap is allowed.</p>'
    text+='<p>Account overlay assumes $5,000 per trade at 1x, no payouts/reactivations, and activation at first entry. Both opens and closes reset activity, a lenient interpretation. Funded-stage margin (25%) and open notional (2x) checks are separate from evaluation rules. This is not a HyroTrader certification: tokenized USDT perpetual prices, fees, funding, instrument eligibility and intraminute equity are not supplied by Alpaca stock candles. Account fees cannot be assumed zero for actual compliance.</p>'
    rows=[]
    for label,a in result['account_audits'].items():
        f=a['failure'];rows.append([label,a['status'],f['reason'] if f else '—',stamp(f['at']) if f else '—',a['entries_before_failure'],n(a['pnl_before_failure']),n(a['max_daily_close_loss']),n(a['max_daily_adverse_bound']),n(a['minimum_conservative_equity_bound']),a['qualifying_days'],n(a['counted_target_profit'])])
    text+=table(['Scenario','Status','Failure','Failure time NY','Entries before failure','P&L until failure $','Max daily marked loss $','Daily adverse bound $','Equity lower bound $','Qualifying days','Counted target profit $'],rows)
    text+='<p>Full-year strategy P&L is not account P&L after a failure. The audit stops at the first certain loss/inactivity failure. Conservative minute-extrema bounds can flag possible intraminute breaches; they are not synchronized tick observations. The same SL/TP cannot be called compliant merely because its price-distance percentage is below an account drawdown limit.</p>'
    text+='<h2>Can any tested bracket meet the full-year account constraints?</h2><p>The following check allows every prior trade to stay open to its maximum deadline and treats closes as activity resets. If the next entry is still more than 30 days later, no SL/TP choice can bridge that gap under this research policy. No additional trades, payouts or reactivations are invented.</p>'+table(['Method','Unavoidable inactivity gaps','Full-year surviving bracket settings','Perfect-exit profit upper bound $','Evaluation target $'],[[label,len(f['unavoidable_inactivity_gaps']),f['full_year_surviving_brackets'],n(f['perfect_hindsight_exit_profit_upper_bound']),n(f['evaluation_target'])] for label,f in result['funded_feasibility'].items()])
    text+='<h2>Retest entry details</h2>'+table(['Stock','TF','Original entry time','Retest entry minute','Original $','Retest $','After weekly watch','Status'],[[r['ticker'],r['entry']['tf'] if r['entry'] else '—',stamp(r['entry']['original_entry_time']) if r['entry'] else '—',stamp(r['entry']['entry_time']) if r['entry'] else '—',n(r['entry']['original_entry']) if r['entry'] else '—',n(r['entry']['entry']) if r['entry'] else '—',r['entry']['after_weekly_watch_expiry'] if r['entry'] else '—',r['reason']] for r in result['retest_attempts']])
    text+='<h2>Rule sources</h2><ul>'+''.join('<li><a href="'+html.escape(url)+'">'+html.escape(name)+'</a></li>' for name,url in result['rules']['sources'].items())+'</ul><p><a href="retest-entry-comparison.json">Complete parameters, outcomes and audit JSON</a></p></html>'
    (output/'retest-entry-comparison.html').write_text(text)

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();raw=args.source.read_bytes();source=json.loads(raw)
    rules=json.loads(Path(__file__).with_name('hyro_one_step_2026_11_28.json').read_text())
    cases,receipts=load_paths(source,args.cache)
    print('Loaded checksum-verified paths; comparing entry methods',flush=True)
    result=analyze(source,cases,rules)
    result.update(source_sha256=hashlib.sha256(raw).hexdigest(),runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),data_receipts=receipts)
    report(result,args.output)
    print(json.dumps(dict(winners=result['winners'],baselines=result['baselines'],account_audits=result['account_audits'])),flush=True)

if __name__=='__main__':main()
