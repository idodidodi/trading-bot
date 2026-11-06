"""Trade simulation from exported signal replay and recorded candles; no detector imports."""
import argparse
from bisect import bisect_left,bisect_right
import hashlib
import json
import math
from pathlib import Path
import statistics
from candles import Candle

def outcome(minutes, starts, at, end, direction, cost_bps, empty=()):
    i=bisect_right(starts,at)  # Strictly after alert availability.
    empty=set(empty)
    j=bisect_left(starts,end)
    while j<len(minutes) and minutes[j].start in empty:j+=1
    if j==len(minutes):return dict(status='missing_executable_exit')
    exit_bar=minutes[j]
    window=minutes[i:j]
    if not window or window[-1].end != exit_bar.start or any(a.end!=b.start for a,b in zip(window,window[1:])):
        return dict(status='incomplete_price_path')
    if window[0].start-at>120000:return dict(status='missing_entry_price')
    executable=next((i for i,b in enumerate(window) if b.start not in empty),None)
    if executable is None:return dict(status='no_entry_no_trades')
    window=window[executable:]
    if window[0].start>=end:return dict(status='no_entry_before_horizon')
    entry=window[0].open;d=1 if direction=='bullish' else -1
    exit_price=exit_bar.open
    mae=max(0,-d*(exit_price/entry-1)*100,max(-d*((b.low if d==1 else b.high)/entry-1)*100 for b in window))
    mfe=max(0,d*(exit_price/entry-1)*100,max(d*((b.high if d==1 else b.low)/entry-1)*100 for b in window))
    gross=d*(exit_price/entry-1)*100
    return dict(status='complete',entry_at=window[0].start,entry=entry,end_at=exit_bar.start,
                exit_price=exit_price,scheduled_exit_at=end,exit_delay_ms=exit_bar.start-end,
                mae_pct=mae,mfe_pct=mfe,gross_return_pct=gross,net_return_pct=gross-cost_bps/100)


def simulate(report,minutes,cost_bps=10.):
    if report.get('version')!=2 or not math.isfinite(cost_bps) or cost_bps<0:raise ValueError('Invalid replay/cost')
    source={k:v for k,v in report.items() if k!='report_hash'}
    if hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()!=report['report_hash']:
        raise ValueError('Replay integrity mismatch')
    report=json.loads(json.dumps(report));starts=[b.start for b in minutes]
    if any(a.end!=b.start for a,b in zip(minutes,minutes[1:])):raise ValueError('Candle grid is not contiguous')
    for root in report['roots']:
        at=root['signal']['confirmed_at'];end=at+report['config']['horizon_4h_bars']*14400000
        chase=next((e['at'] for e in report['events'] if e['session']==root['id'] and e['kind']=='chase'),None)
        root['outcomes']={policy:outcome(minutes,starts,t,end,root['signal']['direction'],cost_bps,report['audit']['1m']['verified_empty']) if t is not None else dict(status='censored_no_entry' if root['status']=='chasing' and report['common_end']<end else 'no_entry') for policy,t in [('original',at),('first_chase',chase)]}
    summary={}
    for policy in ('original','first_chase'):
        values=[r['outcomes'][policy] for r in report['roots'] if r['outcomes'][policy]['status']=='complete']
        no_entry=sum(r['outcomes'][policy]['status']=='no_entry' for r in report['roots'])
        incomplete=len(report['roots'])-len(values)-no_entry
        summary[policy]=dict(complete=len(values),no_entry=no_entry,incomplete=incomplete,
            mean_mae_pct=statistics.mean(v['mae_pct'] for v in values) if values else None,
            mean_net_return_pct=statistics.mean(v['net_return_pct'] for v in values) if values else None,
            net_win_rate=sum(v['net_return_pct']>0 for v in values)/len(values) if values else None,
            per_original_root_mean_return_pct_with_unentered_cash_zero=sum(v['net_return_pct'] for v in values)/len(report['roots']) if report['roots'] and not incomplete else None)
    report['summary']=summary
    paired=[(r['outcomes']['original'],r['outcomes']['first_chase']) for r in report['roots']
            if all(r['outcomes'][p]['status']=='complete' for p in ('original','first_chase'))]
    report['paired']=dict(count=len(paired),
        mean_mae_reduction_pct=statistics.mean(a['mae_pct']-b['mae_pct'] for a,b in paired) if paired else None,
        mean_original_net_return_pct=statistics.mean(a['net_return_pct'] for a,b in paired) if paired else None,
        mean_chase_net_return_pct=statistics.mean(b['net_return_pct'] for a,b in paired) if paired else None)
    report['volume_observations']=[dict(root=r['id'],direction=r['signal']['direction'],
        volume=r['signal'].get('volume'),rvol20=r['signal'].get('rvol20'),
        original=r['outcomes']['original'],chase=r['outcomes']['first_chase']) for r in report['roots']]
    report['config'].update(roundtrip_cost_bps=cost_bps,entry='first traded minute open strictly after event',exit='first traded minute open at or after common original horizon')
    report['source_replay_hash']=report.pop('report_hash');report['version']=3
    report['report_hash']=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--replay',required=True,type=Path);p.add_argument('--minutes',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--cost-bps',type=float,default=10.);a=p.parse_args()
    report=json.loads(a.replay.read_text());bars=[Candle(**b) for b in json.loads(a.minutes.read_text())]
    a.output.write_text(json.dumps(simulate(report,bars,a.cost_bps),indent=2))
