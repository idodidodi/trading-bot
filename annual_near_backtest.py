"""Cached single-pair annual download and event-level RSI study; never live trading."""
import argparse
from bisect import bisect_left, bisect_right
import csv
from datetime import datetime,timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import time
import zipfile
from zoneinfo import ZoneInfo

from kraken_archive import RemoteZip
from repair_kraken_minutes import Client, timestamp, stamp
from divergence_chasing import FRAMES,DURATIONS

BASE='https://assets.kraken.com/marketing/institutions/'


def archive_minutes(urls,cache,start,end,minutes):
    remote=RemoteZip(urls,cache)
    seed=None;read_count=0;first=last=None
    with zipfile.ZipFile(remote) as z:
        names=[n for n in z.namelist() if n.rsplit('/',1)[-1]=='NEARUSD_1.csv']
        if len(names)!=1:raise ValueError('Exact NEARUSD minute member missing or ambiguous')
        info=z.getinfo(names[0]);digest=hashlib.sha256()
        # Reading to EOF also verifies the archive member CRC.
        with z.open(info) as source:
            for raw in source:
                digest.update(raw)
                row=next(csv.reader([raw.decode('utf-8').strip()]))
                if row[0].lower() in ('time','timestamp'):continue
                at=int(row[0]);read_count+=1;first=at if first is None else first;last=at
                values=tuple(float(x) for x in row[1:6])+(int(row[6]),)
                if at<start:seed=(at,values[3])
                if start<=at and at+60<=end:
                    if at in minutes and minutes[at]!=values:raise ValueError('Conflicting archive minute')
                    minutes[at]=values
        manifest=json.loads(z.read('MANIFEST.json')) if 'MANIFEST.json' in z.namelist() else None
    print(f'archive: {read_count} traded candles, {len(minutes)} selected',flush=True)
    return seed,dict(urls=urls,member=info.filename,crc=info.CRC,member_sha256=digest.hexdigest(),
                     first=first,last=last,rows=read_count,manifest=manifest)


def require_archive_coverage(proofs,start,end):
    """Only classify omissions as empty inside the publisher's declared coverage."""
    spans=[]
    for proof in proofs:
        manifest=proof.get('manifest') or {}
        if manifest.get('columns')!=['timestamp','open','high','low','close','volume','trades']:
            raise ValueError('Unexpected archive schema')
        coverage=manifest.get('coverage',{})
        lo=timestamp(coverage['start']+'T00:00:00Z')
        hi=timestamp(coverage['end']+'T00:00:00Z')+86400
        spans.append((lo,hi))
    cursor=start
    for lo,hi in sorted(spans):
        if lo<=cursor:cursor=max(cursor,hi)
    if cursor<end:raise ValueError('Official archive coverage does not cover missing-minute classification')


def recent_minutes(client,start,end,minutes,max_pages=5000):
    since=start*10**9;last_id=-1;pages=0;proofs=[];covered=False
    while pages<max_pages:
        rows,cursor,proof=client.page(since);pages+=1;proofs.append(proof)
        for r in rows:
            identity=int(r[6])
            if identity<=last_id:continue
            last_id=identity
            at=int(r[2])//60*60
            if start<=at and at+60<=end:
                price=float(r[0]);volume=float(r[1])
                if at not in minutes:minutes[at]=(price,price,price,price,volume,1)
                else:
                    o,h,l,c,v,n=minutes[at]
                    minutes[at]=(o,max(h,price),min(l,price),price,v+volume,n+1)
        if int(rows[-1][2])>=end:covered=True;break
        since=cursor
        if pages%50==0:print(f'recent pages={pages}, through {stamp(int(rows[-1][2]))}',flush=True)
    if not covered:raise ValueError('Recent trade history did not cover cutoff; rerun with cached pages')
    return dict(start=start,end=end,pages=pages,proofs=proofs)


def dataset(out,minutes,start,end,seed,evidence):
    if seed is None:raise ValueError('Missing prior traded close for initial observations')
    previous=seed[1];grid=[]
    for at in range(start,end,60):
        values=minutes.get(at)
        if values is None:values=(previous,previous,previous,previous,0.,0)
        previous=values[3];grid.append((at,)+values)
    hashes={}
    for frame in FRAMES:
        width=DURATIONS[frame]//1000
        path=out/f'{frame}.csv'
        with path.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['time','open','high','low','close','Volume','source_status','n_trades'])
            for at in range(start,end,width):
                if at+width>end:break
                rows=grid[(at-start)//60:(at-start+width)//60]
                count=sum(r[6] for r in rows)
                w.writerow([stamp(at),rows[0][1],max(r[2] for r in rows),min(r[3] for r in rows),rows[-1][4],sum(r[5] for r in rows),
                            'kraken_native' if count else 'verified_no_trades',count])
        hashes[frame]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest=dict(pair='NEAR/USD',include_empty=True,initial_close=seed[1],seed_at=seed[0]*1000,
                  output_hashes=hashes,evidence=evidence,start=start,end=end)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return grid


def excursion(grid,starts,at,end,baseline,direction,failure_pct=2):
    d=1 if direction=='bullish' else -1
    rows=grid[bisect_left(starts,at):bisect_left(starts,end)]
    if not rows:return dict(status='unavailable')
    adverse=max(rows,key=lambda r:-d*((r[3] if d==1 else r[2])/baseline-1))
    favorable=max(rows,key=lambda r:d*((r[2] if d==1 else r[3])/baseline-1))
    first_adverse=None;recovery=None;failure=None;first_favorable=None
    for r in rows:
        bad=-d*((r[3] if d==1 else r[2])/baseline-1)*100
        if bad>0 and first_adverse is None:first_adverse=r[0]+60
        if bad>=failure_pct and failure is None:failure=r[0]+60
        if d*(r[4]/baseline-1)>0 and first_favorable is None:first_favorable=r[0]+60
        # Later close only: no inferred wick ordering inside a minute.
        if first_adverse is not None and r[0]+60>first_adverse and d*(r[4]/baseline-1)>=0 and recovery is None:recovery=r[0]+60
    resumed=[r for r in rows if recovery is not None and r[0]>=recovery]
    best_after=max(resumed,key=lambda r:d*((r[2] if d==1 else r[3])/baseline-1)) if resumed else None
    mae=max(0,-d*((adverse[3] if d==1 else adverse[2])/baseline-1)*100)
    mfe=max(0,d*((favorable[2] if d==1 else favorable[3])/baseline-1)*100)
    post_mfe=max(0,d*((best_after[2] if d==1 else best_after[3])/baseline-1)*100) if best_after else None
    return dict(status='complete' if rows[-1][0]+60>=end else 'censored',
                mae_pct=mae,mae_at=adverse[0]+60 if mae>0 else None,
                mfe_pct=mfe,mfe_at=favorable[0]+60 if mfe>0 else None,
                first_adverse_at=first_adverse,first_favorable_close_at=first_favorable,
                recovery_at=recovery,failure_threshold_at=failure,failure_pct=failure_pct,
                mfe_after_recovery_pct=post_mfe,
                mfe_after_recovery_at=best_after[0]+60 if post_mfe else None,through_at=rows[-1][0]+60)


def ranks(values):
    """Average ranks for tied values, for descriptive Spearman correlations."""
    order=sorted(range(len(values)),key=lambda i:values[i]);result=[0.]*len(values);i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:j+=1
        for k in order[i:j]:result[k]=(i+j+1)/2
        i=j
    return result


def descriptive_research(out,report,details):
    config=report.get('config',{})
    lines=['# Exploratory return and volume comparison','',
           f"These are separate hypothetical entries, not a portfolio or realized trades. Success below means positive net return at the fixed original {config.get('horizon_4h_bars',3)*4}-hour horizon, with {config.get('roundtrip_cost_bps',10)} bps total assumed costs. It is different from touching a favorable price or avoiding an adverse threshold.",'',
           '| Policy | Complete entries | No entry | Incomplete | Net-positive entries | Mean net return per entry | Mean return per original, unentered cash = 0 |',
           '|---|---:|---:|---:|---:|---:|---:|']
    fmt=lambda v:'unavailable' if v is None else f'{v:.3f}%'
    for policy in ('original','first_chase'):
        v=report.get('summary',{}).get(policy)
        if not v:continue
        lines.append(f"| {policy} | {v['complete']} | {v['no_entry']} | {v['incomplete']} | {fmt(v['net_win_rate']*100 if v['net_win_rate'] is not None else None)} | {fmt(v['mean_net_return_pct'])} | {fmt(v['per_original_root_mean_return_pct_with_unentered_cash_zero'])} |")
    paired=report.get('paired',{})
    lines.extend(['',f"Matched original/chase pairs: {paired.get('count',0)}. Mean original net return: {fmt(paired.get('mean_original_net_return_pct'))}; mean chase net return: {fmt(paired.get('mean_chase_net_return_pct'))}; mean reduction in adverse excursion: {fmt(paired.get('mean_mae_reduction_pct'))} (percentage points).",'',
        'A higher conditional chase mean can reflect selecting a smaller set of roots. Use the paired comparison and the all-root denominator together. Censored observations remain incomplete.','',
        '| Original candle feature vs subsequent original-entry net return | Complete pairs | Spearman rank correlation |',
        '|---|---:|---:|'])
    correlations={}
    for feature in ('volume','rvol20'):
        pairs=[(v[feature],v['original']['net_return_pct']) for v in report.get('volume_observations',[])
               if v.get(feature) is not None and v['original'].get('status')=='complete']
        x=ranks([p[0] for p in pairs]);y=ranks([p[1] for p in pairs])
        rho=statistics.correlation(x,y) if len(pairs)>2 and len(set(x))>1 and len(set(y))>1 else None
        correlations[feature]=dict(n=len(pairs),spearman=rho)
        lines.append(f"| {feature} | {len(pairs)} | {rho:.3f} |" if rho is not None else f'| {feature} | {len(pairs)} | unavailable |')
    lines.extend(['','Raw closing-candle volume and relative volume (versus its preceding 20 traded candles) are recorded separately. Correlation describes this sample and does not establish that a volume filter improves success. Signals may overlap and share a price path, so ordinary independent-observation confidence claims would be misleading.','',
        'No strategy parameters were optimized on these outcomes. Any proposed volume, stop, timing or timeframe change needs a frozen rule and unseen-period/other-asset evaluation before claiming an improvement. Maximum favorable excursion assumes hindsight and is not a realizable exit rule.'])
    bins={}
    lines.extend(['','| Original relative-volume group | Complete entries | Net-positive share | Mean net return |',
                  '|---|---:|---:|---:|'])
    for label,predicate in [('Below preceding-20 average',lambda x:x<1),('At/above preceding-20 average',lambda x:x>=1)]:
        values=[v['original']['net_return_pct'] for v in report.get('volume_observations',[])
                if v.get('rvol20') is not None and predicate(v['rvol20']) and v['original'].get('status')=='complete']
        win=sum(v>0 for v in values)/len(values)*100 if values else None
        mean=statistics.mean(values) if values else None
        bins[label]=dict(n=len(values),net_positive_pct=win,mean_net_return_pct=mean)
        lines.append(f'| {label} | {len(values)} | {fmt(win)} | {fmt(mean)} |')
    lines.extend(['','The relative-volume split uses 1.0 (the preceding average), not a threshold fitted to maximize these outcomes. These descriptive subgroup percentages are not forecasts or a validated success-rate improvement.'])
    correlations['relative_volume_groups']=bins
    (out/'research.md').write_text('\n'.join(lines)+'\n')
    (out/'volume_summary.json').write_text(json.dumps(correlations,indent=2)+'\n')


def detail_report(out,report,paths,grid,cutoff,hours=12,failure_pct=2):
    from chasing_research import read_series,detect_series
    from platform_app import load_rules
    children=[]
    for frame in FRAMES[1:]:
        bars,volumes,audit=read_series(paths[frame],frame,cutoff*1000)
        children.extend(detect_series(bars,volumes,load_rules(),'KRAKEN:NEARUSD',frame,audit['verified_empty']))
    children.sort(key=lambda s:(s['confirmed_at'],-FRAMES.index(s['timeframe'])))
    times=[s['confirmed_at']//1000 for s in children];starts=[r[0] for r in grid];details=[]
    for root in report['roots']:
        s=root['signal'];at=s['confirmed_at']//1000;deadline=at+hours*3600
        follow=None;observed=None
        for event in report['events']:
            if event['session']==root['id'] and event['kind']=='chase':follow=event;break
        # Observation-only lower-frame event after a strategy stop remains visible.
        for child in children[bisect_right(times,at):bisect_right(times,deadline)]:
            if child['direction']!=s['direction']:continue
            when=child['confirmed_at']//1000;idx=bisect_right(starts,when-60)-1
            if idx>=0 and (1 if s['direction']=='bullish' else -1)*(grid[idx][4]/s['price2']-1)<0:
                observed=child;break
        if follow and follow['signal']['timeframe']=='4h':follow=None
        second=follow['signal'] if follow else observed
        second_at=(follow['at']//1000 if follow else second['confirmed_at']//1000) if second else None
        before=excursion(grid,starts,at,second_at or deadline,s['price2'],s['direction'],failure_pct)
        # Baseline is the closed minute price available at the follow-up alert.
        ref_index=bisect_left(starts,second_at)-1 if second_at else None
        ref=grid[ref_index][4] if ref_index is not None and ref_index>=0 else None
        after=excursion(grid,starts,second_at,second_at+hours*3600,ref,s['direction'],failure_pct) if ref else None
        details.append(dict(original=s,original_at=at,search_status=root['status'],
                            followup=second,followup_at=second_at,followup_source='strategy' if follow else 'observation_only_after_stop' if second else 'none',
                            followup_observed_price=ref,before=before,after=after,
                            original_baseline_recovery=excursion(grid,starts,at,(second_at or at)+hours*3600,s['price2'],s['direction'],failure_pct).get('recovery_at')))
    (out/'cases.json').write_text(json.dumps(details,indent=2)+'\n')
    columns=['original_time_utc','direction','original_close','search_status','followup_time_utc','followup_frame','followup_source',
             'mae_before_pct','mae_before_time_utc','followup_reference_price','mae_after_pct','mae_after_time_utc','recovery_time_utc','first_favorable_close_time_utc',
             'mfe_after_pct','mfe_after_time_utc','mfe_after_recovery_pct','mfe_after_recovery_time_utc','failure_threshold_pct','failure_time_utc','post_window_status',
             'original_time_israel','followup_time_israel','recovery_time_israel']
    with (out/'cases.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(columns)
        for r in details:
            after=r['after'] or {};before=r['before'];fmt=lambda t:stamp(t) if t else ''
            local=lambda t:datetime.fromtimestamp(t,ZoneInfo('Asia/Jerusalem')).strftime('%Y-%m-%d %H:%M %z') if t else ''
            w.writerow([fmt(r['original_at']),r['original']['direction'],r['original']['price2'],r['search_status'],fmt(r['followup_at']),
                        r['followup']['timeframe'] if r['followup'] else '',r['followup_source'],before.get('mae_pct'),fmt(before.get('mae_at')),
                        r['followup_observed_price'],after.get('mae_pct'),fmt(after.get('mae_at')),fmt(after.get('recovery_at')),fmt(after.get('first_favorable_close_at')),
                        after.get('mfe_pct'),fmt(after.get('mfe_at')),after.get('mfe_after_recovery_pct'),fmt(after.get('mfe_after_recovery_at')),
                        failure_pct,fmt(after.get('failure_threshold_at')),after.get('status','no_followup'),
                        local(r['original_at']),local(r['followup_at']),local(after.get('recovery_at'))])
    lines=['# One-year NEAR/USD RSI divergence report','',
           f"Original signal interval: {stamp(report['config']['start_at']//1000) if report.get('config',{}).get('start_at') else 'see replay.json'} (inclusive) to {stamp(report['config']['end_at']//1000) if report.get('config',{}).get('end_at') else 'see replay.json'} (exclusive). Data cutoff: {stamp(cutoff)}.",
           'Every original 4h divergence is listed in cases.csv. Follow-ups are same-direction lower-frame divergences while adverse to the original close.',
           'Strategy follow-ups stop at the first eligible event; observation-only follow-ups after a favorable stop are marked separately.',
           f'Search window: {hours} hours. Post-follow-up window: {hours} hours. Late windows are censored, never counted as successes.',
           f'Before MAE is measured from original P2 close; after MAE/MFE and the {failure_pct}% failure flag use the first observed minute close at the follow-up alert.',
           'If no follow-up exists, the before-phase measures the original-price path for the stated search window, even if causal chasing stopped earlier. It is not a live position or an extended alert session.',
           'Recovery requires a later minute close back to that follow-up baseline after an adverse excursion. This is price-path research, not an actual entry/fill.',
           'Times are UTC; extreme times identify the containing minute close, not an inferred intraminute trade order.','',
           f"Original 4h signals: {len(details)}.",f"Strategy follow-ups: {sum(r['followup_source']=='strategy' for r in details)}.",
           f"Observation-only follow-ups: {sum(r['followup_source']=='observation_only_after_stop' for r in details)}.",
           f"No eligible lower-frame follow-up: {sum(r['followup'] is None for r in details)}.",
           f"Complete post-follow-up windows: {sum((r['after'] or {}).get('status')=='complete' for r in details)}; censored post-follow-up windows: {sum((r['after'] or {}).get('status')=='censored' for r in details)}.",
           f"Post-follow-up adverse moves reaching {failure_pct}%: {sum(bool((r['after'] or {}).get('failure_threshold_at')) for r in details)}.",'',
           'The failure flag is a configurable research example, not a broker stop or proof of permanent strategy failure; a later recovery can still occur.',
           'Maximum favorable moves and drawdowns are bounded by the stated window; a future move outside it is not included.',
           'No trades or alerts were sent. No-trade minutes are labeled observations and cannot receive hypothetical fills.']
    followup_table=['| Follow-up population | Cases | Further adverse threshold reached | Recovery observed at least once |',
                    '|---|---:|---:|---:|']
    for source in ('strategy','observation_only_after_stop'):
        group=[r for r in details if r['followup_source']==source]
        followup_table.append(f"| {source} | {len(group)} | {sum(bool((r['after'] or {}).get('failure_threshold_at')) for r in group)} | {sum(bool((r['after'] or {}).get('recovery_at')) for r in group)} |")
    lines.append('\n'.join(followup_table))
    lines.extend(['','A recovery can occur before a later failure-threshold crossing. Recovery counts are not profitable-trade counts. See research.md for the separate fixed-horizon net-return comparison.'])
    (out/'report.md').write_text('\n\n'.join(lines)+'\n')
    cases=['# Every four-hour divergence','', 'Times below are Israel time; full UTC values and exact prices are in cases.csv.','']
    pct=lambda v:'unavailable' if v is None else f'{v:.3f}%'
    local=lambda t:datetime.fromtimestamp(t,ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y %H:%M %z') if t else 'not observed'
    for i,r in enumerate(details,1):
        a=r['after'] or {};b=r['before']
        cases.extend([f"## {i}. {local(r['original_at'])} — {r['original']['direction']}",'',
            f"Original close: {r['original']['price2']}. Search ended as: {r['search_status']}.",
            f"Follow-up: {local(r['followup_at'])}, {r['followup']['timeframe'] if r['followup'] else 'none'}, {r['followup_source']}.",
            f"Before follow-up: maximum adverse move {pct(b.get('mae_pct'))}, at {local(b.get('mae_at'))}.",
            f"After follow-up: maximum adverse move {pct(a.get('mae_pct'))}, at {local(a.get('mae_at'))}.",
            f"First favorable close: {local(a.get('first_favorable_close_at'))}. Recovery after drawdown: {local(a.get('recovery_at'))}.",
            f"Maximum favorable move: {pct(a.get('mfe_pct'))}, at {local(a.get('mfe_at'))}.",
            f"Maximum favorable move after recovery: {pct(a.get('mfe_after_recovery_pct'))}, at {local(a.get('mfe_after_recovery_at'))}.",
            f"Further {failure_pct}% adverse threshold: {local(a.get('failure_threshold_at'))}. Observation status: {a.get('status','no follow-up')}.",''])
    (out/'cases.md').write_text('\n\n'.join(cases)+'\n')
    (out/'report_parameters.json').write_text(json.dumps(dict(window_hours=hours,failure_pct=failure_pct,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),replay_hash=report['report_hash']),indent=2)+'\n')
    descriptive_research(out,report,details)
    return details


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--start',default='2025-10-31T00:00:00Z');p.add_argument('--end',default='2026-10-31T00:00:00Z')
    p.add_argument('--cutoff',default='2026-10-31T13:45:00Z')
    p.add_argument('--refresh-report',action='store_true');p.add_argument('--window-hours',type=int,default=12)
    p.add_argument('--failure-pct',type=float,default=2)
    args=p.parse_args();start=timestamp(args.start);end=timestamp(args.end);cutoff=timestamp(args.cutoff)
    if not start<end<=cutoff<=int(time.time()):p.error('Invalid annual bounds')
    if args.window_hours<1 or not math.isfinite(args.failure_pct) or args.failure_pct<=0:p.error('Invalid report window or adverse threshold')
    args.output.mkdir(parents=True,exist_ok=True);data=args.output/'candles';data.mkdir(exist_ok=True)
    if args.refresh_report:
        report=json.loads((args.output/'replay.json').read_text());grid=[]
        with (data/'1m.csv').open() as f:
            for r in csv.DictReader(f):grid.append((timestamp(r['time']),*(float(r[k]) for k in ('open','high','low','close','Volume')),int(r['n_trades'])))
        detail_report(args.output,report,{f:data/f'{f}.csv' for f in FRAMES},grid,cutoff,args.window_hours,args.failure_pct)
        return
    minutes={};evidence=[];seed=None;warmup=(start-60*86400)//14400*14400
    for urls in [[BASE+f'Kraken_OHLCVT_Full.zip.part{i:02}' for i in range(5)],[BASE+'Kraken_OHLCVT_2026Q3.zip']]:
        prior,proof=archive_minutes(urls,args.cache/'archives',warmup,cutoff,minutes)
        if prior and (seed is None or prior[0]>seed[0]):seed=prior
        evidence.append(proof)
    require_archive_coverage(evidence,warmup,timestamp('2026-10-01T00:00:00Z'))
    recent=recent_minutes(Client(args.cache/'trades','NEAR/USD'),timestamp('2026-10-01T00:00:00Z'),cutoff,minutes)
    evidence.append(recent)
    grid=dataset(data,minutes,warmup,cutoff,seed,dict(sources=evidence))
    print('Dataset complete; running causal replay.',flush=True)
    from chasing_research import run
    paths={f:data/f'{f}.csv' for f in FRAMES}
    report=run(paths,'KRAKEN:NEARUSD',cutoff*1000,start_at=start*1000,end_at=end*1000)
    (args.output/'replay.json').write_text(json.dumps(report,indent=2)+'\n')
    details=detail_report(args.output,report,paths,grid,cutoff,args.window_hours,args.failure_pct)
    print(json.dumps(dict(originals=len(details),summary=report['summary'],output=str(args.output)),indent=2),flush=True)


if __name__=='__main__':main()
