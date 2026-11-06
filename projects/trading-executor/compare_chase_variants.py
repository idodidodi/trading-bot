"""Compare pinned chase studies using identical roots, candles and outcome assumptions."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import statistics


def key(signal):return (signal['symbol'],signal['direction'],signal['pivot2'])


def compare(baseline,candidate,out):
    reports=[json.loads((p/'replay.json').read_text()) for p in (baseline,candidate)]
    cases=[json.loads((p/'cases.json').read_text()) for p in (baseline,candidate)]
    for field in ('symbol','cutoff','stop_pct','roundtrip_cost_bps','start_at','end_at','horizon_4h_bars','rules','entry','exit'):
        if reports[0]['config'][field]!=reports[1]['config'][field]:raise ValueError('Incomparable assumption: '+field)
    if {f:a['sha256'] for f,a in reports[0]['audit'].items()}!={f:a['sha256'] for f,a in reports[1]['audit'].items()}:
        raise ValueError('Datasets differ')
    params=[json.loads((p/'report_parameters.json').read_text()) for p in (baseline,candidate)]
    for field in ('window_hours','failure_pct'):
        if params[0][field]!=params[1][field]:raise ValueError('Incomparable detail parameter: '+field)
    roots=[{key(r['signal']):r for r in report['roots']} for report in reports]
    for report,mapping in zip(reports,roots):
        if len(mapping)!=len(report['roots']):raise ValueError('Duplicate original signals')
        canonical={k:v for k,v in report.items() if k!='report_hash'}
        if hashlib.sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest()!=report['report_hash']:
            raise ValueError('Replay hash mismatch')
    if set(roots[0])!=set(roots[1]):raise ValueError('Original signal populations differ')
    for k in roots[0]:
        if roots[0][k]['signal']!=roots[1][k]['signal']:raise ValueError('Original signals differ')
        if roots[0][k]['outcomes']['original']!=roots[1][k]['outcomes']['original']:raise ValueError('Original-entry outcomes differ')
    detail=[{key(c['original']):c for c in entries} for entries in cases]
    events=[{e['session']:e for e in r['events'] if e['kind']=='chase'} for r in reports]
    complete=lambda r:r['outcomes']['first_chase']['status']=='complete'
    common=[k for k in roots[0] if complete(roots[0][k]) and complete(roots[1][k])]
    paired={}
    for label,index in [('one_minute',0),('five_minutes_up',1)]:
        values=[roots[index][k]['outcomes']['first_chase'] for k in common]
        paired[label]=dict(n=len(values),mean_net_return_pct=statistics.mean(v['net_return_pct'] for v in values) if values else None,
                           mean_mae_pct=statistics.mean(v['mae_pct'] for v in values) if values else None)
    out.mkdir(parents=True,exist_ok=True)
    columns=['original_time_utc','direction','original_close','one_minute_status','five_minutes_status']
    metrics=['followup_time_utc','followup_frame','mae_before_pct','mae_after_pct','mfe_after_pct','recovery_time_utc','further_2pct_time_utc','net_return_pct']
    columns += [prefix+'_'+m for prefix in ('one_minute','five_minutes') for m in metrics]
    from datetime import datetime,timezone
    def stamp(t):return datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')
    with (out/'comparison.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(columns)
        for k in roots[0]:
            a,b=(root[k] for root in roots);s=a['signal']
            row=[stamp(s['confirmed_at']//1000),s['direction'],s['price2'],a['status'],b['status']]
            for index in range(2):
                r=roots[index][k];event=events[index].get(r['id']);c=detail[index][k]
                # Only actual strategy alerts belong in this comparison's follow-up columns.
                after=c['after'] if c['followup_source']=='strategy' else {};after=after or {}
                fmt=lambda t:stamp(t) if t else ''
                row += [stamp(event['at']//1000) if event else '',event['signal']['timeframe'] if event else '',
                        c['before'].get('mae_pct') if event else None,after.get('mae_pct'),after.get('mfe_pct'),fmt(after.get('recovery_at')),
                        fmt(after.get('failure_threshold_at')),r['outcomes']['first_chase'].get('net_return_pct')]
            w.writerow(row)
    fmt=lambda x:'unavailable' if x is None else f'{x:.3f}%'
    summaries=[]
    for report,entries in zip(reports,cases):
        actual=[c for c in entries if c['followup_source']=='strategy']
        completed=[c for c in actual if c['after']['status']=='complete']
        waits=[(e['at']-next(r['signal']['confirmed_at'] for r in report['roots'] if r['id']==e['session']))/60000 for e in report['events'] if e['kind']=='chase']
        s=report['summary']['first_chase']
        summaries.append(dict(roots=len(report['roots']),chases=sum(e['kind']=='chase' for e in report['events']),
            favorable_stops=sum(r['status']=='stopped_favorable' for r in report['roots']),
            expired=sum(r['status']=='expired' for r in report['roots']),
            frames=dict(Counter(e['signal']['timeframe'] for e in report['events'] if e['kind']=='chase')),
            further_2pct=sum(bool(c['after']['failure_threshold_at']) for c in actual),
            further_2pct_rate=sum(bool(c['after']['failure_threshold_at']) for c in completed)/len(completed) if completed else None,
            median_wait_minutes=statistics.median(waits) if waits else None,
            complete_post_windows=sum(c['after']['status']=='complete' for c in actual),
            censored_post_windows=sum(c['after']['status']!='complete' for c in actual),
            worst_post_mae_pct=max((c['after']['mae_pct'] for c in actual),default=None),**s))
    rows=[('Original 4h signals','roots',False),('Strategy follow-up alerts','chases',False),('Favorable-stop sessions','favorable_stops',False),
          ('Expired search sessions','expired',False),('Net-positive follow-up entries','net_win_rate','rate'),
          ('Mean net return per follow-up entry','mean_net_return_pct',True),
          ('Mean net return per original (unentered cash = 0)','per_original_root_mean_return_pct_with_unentered_cash_zero',True),
          ('Mean hypothetical entry MAE','mean_mae_pct',True),('Further 2% adverse moves after strategy alert','further_2pct',False),
          ('Further 2% adverse share (complete post-alert windows)','further_2pct_rate','rate'),
          ('Median minutes from original to strategy follow-up','median_wait_minutes',False),
          ('Worst post-alert adverse move','worst_post_mae_pct',True),('Complete post-alert windows','complete_post_windows',False),
          ('Censored post-alert windows','censored_post_windows',False)]
    cfg=reports[0]['config'];hours=cfg['horizon_4h_bars']*4
    lines=['# Same-year NEAR/USD: 1m versus 5m-and-up chasing','',
           f"Original signal interval: {stamp(cfg['start_at']//1000)} inclusive to {stamp(cfg['end_at']//1000)} exclusive.",'',
           f"Identical original signals, six candle-file hashes, RSI rules, annual interval, {cfg['stop_pct']}% favorable stop, {params[0]['window_hours']}h search/price-path windows and hypothetical entry/exit assumptions were verified. One-minute prices remain the measurement/fill grid in both runs; only one-minute divergence triggers are removed.",'',
           '| Metric | With 1m | 5m and up |','|---|---:|---:|']
    for label,field,pct in rows:
        values=[s[field] for s in summaries]
        display=[fmt(v*100 if pct=='rate' and v is not None else v) if pct else str(v) for v in values]
        lines.append(f'| {label} | {display[0]} | {display[1]} |')
    lines.extend(['',f"Follow-up timeframes with 1m: {summaries[0]['frames']}. Without 1m: {summaries[1]['frames']}.",'',
        f"Matched roots with executed follow-up entries in both variants: {len(common)}. Mean net returns: {fmt(paired['one_minute']['mean_net_return_pct'])} with 1m versus {fmt(paired['five_minutes_up']['mean_net_return_pct'])} without. Mean entry MAE: {fmt(paired['one_minute']['mean_mae_pct'])} versus {fmt(paired['five_minutes_up']['mean_mae_pct'])}.",'',
        f"Hypothetical returns use a common {hours}h exit measured from the original alert and {cfg['roundtrip_cost_bps']} bps total assumed costs. Post-alert extremes/failure flags use a separate {params[0]['window_hours']}h window from each follow-up alert. These have different anchors and are not interchangeable.",'',
        'Alert populations change when a trigger is removed. Read the common-root comparison alongside conditional entry means and all-original cash-inclusive means. A higher win rate alone does not establish greater profitability.','',
        'This is the same previously examined year, not unseen validation. Volume results for original entries are unchanged because the original population and its outcomes are identical. No live scanner, preferences or Telegram delivery was changed by this comparison.'])
    (out/'comparison.md').write_text('\n'.join(lines)+'\n')
    result=dict(verified_identical_inputs=True,baseline=str(baseline.resolve()),candidate=str(candidate.resolve()),summaries=summaries,paired=paired,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                input_hashes={str(p/f):hashlib.sha256((p/f).read_bytes()).hexdigest() for p in (baseline,candidate) for f in ('replay.json','cases.json','report_parameters.json')})
    (out/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=compare(a.baseline,a.candidate,a.output)
    print(json.dumps(dict(summaries=r['summaries'],paired=r['paired']),indent=2))
