"""Offline TradingView OHLCV audit and causal RSI chase replay. No DB or network I/O."""
import argparse
from bisect import bisect_left, bisect_right
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

from divergence_chasing import FRAMES, DURATIONS, create, step, percentage


def read_series(path, frame, cutoff):
    from scanner import Candle, timestamp, check_candles
    raw = Path(path).read_bytes()
    bars, volumes, duplicates, empty = {}, {}, 0, set()
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not {'time','open','high','low','close'} <= set(reader.fieldnames or []):
            raise ValueError('TradingView CSV requires time and OHLC columns')
        for row in reader:
            at = timestamp(row['time'])
            bar = Candle(at, at+DURATIONS[frame], *(float(row[k]) for k in ('open','high','low','close')))
            text = row.get('Volume', row.get('volume','')).strip()
            volume = float(text) if text else None
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                raise ValueError('Invalid volume')
            if at in bars:
                if bars[at] != bar or volumes[at] != volume:
                    raise ValueError('Conflicting duplicate timestamp')
                duplicates += 1
            bars[at], volumes[at] = bar, volume
            if row.get('source_status')=='verified_no_trades':
                if volume!=0 or not bar.open==bar.high==bar.low==bar.close:
                    raise ValueError('Invalid no-trade observation')
                empty.add(at)
    closed = check_candles(list(bars.values()), cutoff)
    conflicts=0
    if empty:
        manifest_path=Path(path).parent/'manifest.json'
        if not manifest_path.is_file():raise ValueError('No-trade observations require a repair manifest')
        manifest=json.loads(manifest_path.read_text())
        if not manifest.get('include_empty') or manifest.get('output_hashes',{}).get(frame)!=hashlib.sha256(raw).hexdigest():
            raise ValueError('Repaired CSV does not match its provenance manifest')
        conflicts=len(manifest.get('evidence',{}).get('overlap_conflicts',[]))
    ordered=sorted(bars.values(),key=lambda b:b.start)
    for i,b in enumerate(ordered):
        seed_ok=not i and empty and manifest.get('initial_close') is not None and float(manifest['initial_close'])==b.close and manifest.get('seed_at',b.start)>=0 and manifest.get('seed_at',b.start)<b.start
        if b.start in empty and not seed_ok and (not i or ordered[i-1].end!=b.start or ordered[i-1].close!=b.close):
            raise ValueError('No-trade observation must carry an adjacent prior close')
    gaps = sum(max(0,(b.start-a.end)//DURATIONS[frame]) for a,b in zip(closed,closed[1:]))
    return closed, volumes, dict(file=Path(path).name, sha256=hashlib.sha256(raw).hexdigest(),
        candles=len(closed), excluded_open_or_future=len(bars)-len(closed), duplicates=duplicates,
        missing_intervals=gaps, start=closed[0].start if closed else None,
        end=closed[-1].end if closed else None, volume_missing=sum(volumes[b.start] is None for b in closed),
        verified_empty=sorted(b.start for b in closed if b.start in empty),provider_overlap_conflicts=conflicts)


def detect_series(bars, volumes, rules, symbol, frame, empty=()):
    from scanner import detect
    signals, segment = [], []
    def flush():
        positions={b.start:i for i,b in enumerate(segment)}
        for s in detect(segment,rules,symbol,frame,provisional=True):
            i = positions[s['pivot2']]
            previous = [volumes[b.start] for b in segment[max(0,i-20):i]]
            v = volumes[s['pivot2']]
            denominator = statistics.mean(previous) if len(previous)==20 and all(x is not None for x in previous) else None
            s.update(volume=v, rvol20=v/denominator if v is not None and denominator else None)
            signals.append(s)
    empty=set(empty);previous_end=None
    for b in bars:
        if previous_end is not None and previous_end != b.start:
            flush();segment=[]
        previous_end=b.end
        # Empty observations establish continuity, but do not create RSI/BB candles.
        if b.start not in empty:segment.append(b)
    flush()
    # Multiple P1 pairs remain in detector evidence; research events are grouped by P2.
    grouped = {}
    for s in signals:
        key = (s['direction'],s['pivot2'])
        if key not in grouped:grouped[key]=s|dict(reference_pairs=[])
        grouped[key]['reference_pairs'].append(s['pivot1'])
    return sorted(grouped.values(),key=lambda s:(s['confirmed_at'],s['direction'],s['pivot2']))


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


def run(paths, symbol, cutoff, stop_pct=.5, cost_bps=10., horizon=3, start_at=None, end_at=None):
    from platform_app import load_rules
    rules=load_rules();series={};volumes={};audit={};signals={}
    for frame in FRAMES:
        series[frame],volumes[frame],audit[frame]=read_series(paths[frame],frame,cutoff)
        signals[frame]=detect_series(series[frame],volumes[frame],rules,symbol,frame,audit[frame]['verified_empty'])
    if any(not bars for bars in series.values()):raise ValueError('Every timeframe needs completed candles')
    common_start=max(b[0].start for b in series.values());common_end=min(b[-1].end for b in series.values())
    minutes=series['1m'];starts=[b.start for b in minutes]
    minute_map={b.start:b for b in minutes}
    reconciliation={}
    for frame in FRAMES[:-1]:
        checks=dict(complete=0,incomplete=0,ohlc_mismatch=0,volume_mismatch=0)
        for b in series[frame]:
            if b.start<common_start or b.end>common_end:continue
            parts=[minute_map.get(t) for t in range(b.start,b.end,60000)]
            if any(c is None for c in parts):checks['incomplete']+=1;continue
            checks['complete']+=1
            actual=(parts[0].open,max(c.high for c in parts),min(c.low for c in parts),parts[-1].close)
            if not all(math.isclose(x,y,rel_tol=1e-7,abs_tol=1e-8) for x,y in zip(actual,(b.open,b.high,b.low,b.close))):checks['ohlc_mismatch']+=1
            values=[volumes['1m'][c.start] for c in parts]
            if all(v is not None for v in values) and volumes[frame][b.start] is not None:
                if not math.isclose(sum(values),volumes[frame][b.start],rel_tol=1e-6,abs_tol=1e-6):checks['volume_mismatch']+=1
        reconciliation[frame]=checks
    children=sorted([s for f in FRAMES for s in signals[f]],key=lambda s:s['confirmed_at'])
    times=[s['confirmed_at'] for s in children]
    roots=[];ledger=[]
    for root in signals['4h']:
        at=root['confirmed_at']
        if not max(common_start,start_at or common_start)<=at<min(common_end,end_at or common_end):continue
        end=at+horizon*DURATIONS['4h']
        session=create('simulation',root,at,stop_pct,expires_at=end)
        events=[]
        for bar in minutes[bisect_right(starts,at-1):]:
            if bar.end>min(end,common_end):break
            k=bisect_right(times,session['parent_at']);j=bisect_right(times,bar.end)
            session,new=step(session,bar.end,price=bar.close,price_at=bar.end,signals=children[k:j])
            events.extend(new)
            if session['status']!='chasing':break
        chase=[e for e in events if e['kind']=='chase']
        entries={'original':at,'first_chase':chase[0]['at'] if chase else None,
                 'final_1m':chase[-1]['at'] if chase and chase[-1]['signal']['timeframe']=='1m' else None}
        outcomes={p:outcome(minutes,starts,t,end,root['direction'],cost_bps,audit['1m']['verified_empty']) if t is not None
                  else dict(status='censored_no_entry' if session['status']=='chasing' and common_end<end else 'no_entry') for p,t in entries.items()}
        roots.append(dict(id=session['id'],signal=root,status=session['status'],stage=session['stage'],
                          observation_end=min(end,common_end),outcomes=outcomes))
        ledger.extend(events)
    summary={}
    for policy in ('original','first_chase','final_1m'):
        values=[r['outcomes'][policy] for r in roots if r['outcomes'][policy]['status']=='complete']
        no_entry=sum(r['outcomes'][policy]['status'].startswith('no_entry') for r in roots)
        incomplete=len(roots)-len(values)-no_entry
        summary[policy]=dict(complete=len(values),no_entry=no_entry,incomplete=incomplete,
            mean_mae_pct=statistics.mean(v['mae_pct'] for v in values) if values else None,
            mean_net_return_pct=statistics.mean(v['net_return_pct'] for v in values) if values else None,
            net_win_rate=sum(v['net_return_pct']>0 for v in values)/len(values) if values else None,
            per_original_root_mean_return_pct_with_unentered_cash_zero=sum(v['net_return_pct'] for v in values)/len(roots) if roots and not incomplete else None)
    config=dict(symbol=symbol,cutoff=cutoff,stop_pct=percentage(stop_pct),roundtrip_cost_bps=cost_bps,
                start_at=start_at,end_at=end_at,
                selection='first divergence, smallest timeframe first',horizon_4h_bars=horizon,rules=rules,
                gate='adverse to original close',observation='closed minute close',
                entry='first traded minute open strictly after event',exit='first traded minute open at or after common original horizon')
    config['empty_minutes']='verified no-trade carry-forward observations; excluded from indicators and entry fills'
    config['source_hashes']={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                             for name in ('chasing_research.py','divergence_chasing.py','scanner.py')}
    paired=[(r['outcomes']['original'],r['outcomes']['first_chase']) for r in roots
            if r['outcomes']['original']['status']=='complete' and r['outcomes']['first_chase']['status']=='complete']
    volume_rows=[dict(root=r['id'],direction=r['signal']['direction'],volume=r['signal']['volume'],
                      rvol20=r['signal']['rvol20'],original=r['outcomes']['original'],chase=r['outcomes']['first_chase']) for r in roots]
    report=dict(version=1,config=config,audit=audit,common_start=common_start,common_end=common_end,
                roots=roots,events=ledger,summary=summary,reconciliation=reconciliation,
                paired=dict(count=len(paired),mean_mae_reduction_pct=statistics.mean(a['mae_pct']-b['mae_pct'] for a,b in paired) if paired else None,
                            mean_original_net_return_pct=statistics.mean(a['net_return_pct'] for a,b in paired) if paired else None,
                            mean_chase_net_return_pct=statistics.mean(b['net_return_pct'] for a,b in paired) if paired else None),
                volume_observations=volume_rows,
                limitations=['Exploratory single-asset sample; no holdout profitability claim.',
                    'Roundtrip cost is an explicit hypothetical parameter, not verified Kraken execution costs.',
                    'Missing candles split detector history; missing execution paths are excluded, not interpolated.',
                    'Simulated entries are independent comparisons; no human clicks or broker fills are inferred.'])
    report['report_hash']=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for frame in FRAMES:p.add_argument('--'+frame,required=True,type=Path)
    p.add_argument('--symbol',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--stop-pct',type=percentage,default=.5)
    p.add_argument('--cost-bps',type=float,default=10.)
    p.add_argument('--horizon',type=int,default=3)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if not math.isfinite(args.cost_bps) or args.cost_bps<0 or args.horizon<1:p.error('Invalid cost or horizon')
    from scanner import timestamp
    cutoff=timestamp(args.as_of)
    if cutoff>int(datetime.now(timezone.utc).timestamp()*1000):p.error('As-of must not be in the future')
    report=run({f:getattr(args,f) for f in FRAMES},args.symbol,cutoff,args.stop_pct,args.cost_bps,args.horizon)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Offline chase research','','Policy: first eligible divergence, inspecting 1m → 5m → 15m → 30m → 1h → 4h each minute.',
           f"Stop: {args.stop_pct}%; hypothetical roundtrip cost: {args.cost_bps} bps; horizon: {args.horizon} original 4h bars.",'',
           '| Frame | Closed observations | Absent intervals | Verified empty | Missing volume |','| --- | ---: | ---: | ---: | ---: |']
    lines += [f"| {f} | {a['candles']} | {a['missing_intervals']} | {len(a['verified_empty'])} | {a['volume_missing']} |" for f,a in report['audit'].items()]
    lines += ['',f"Eligible original roots: {len(report['roots'])}. Paired complete outcomes: {report['paired']['count']}.",'']
    lines += ['| Policy | Executed entries | No entry | Incomplete | Mean MAE % | Mean simulated net return % |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    def display(value):return 'unavailable' if value is None else f'{value:.4f}'
    lines += [f"| {name} | {s['complete']} | {s['no_entry']} | {s['incomplete']} | {display(s['mean_mae_pct'])} | {display(s['mean_net_return_pct'])} |" for name,s in report['summary'].items()]
    pair=report['paired']
    lines += ['',f"Paired comparison uses only the same {pair['count']} original roots with both entries:",
              f"- Mean MAE reduction (original minus chase): {display(pair['mean_mae_reduction_pct'])} percentage points.",
              f"- Mean simulated net return: original {display(pair['mean_original_net_return_pct'])}%; chase {display(pair['mean_chase_net_return_pct'])}%.",
              '', 'Executed-entry averages above are conditional on an entry; the original and chase policies can select different populations.',
              'The JSON separately reports per-original-root return with unentered allocations held as zero-return cash; this is not a capital-constrained portfolio simulation.']
    lines += ['','No profitability or volume-success conclusion is justified when execution paths are incomplete.','']
    lines += [f"Provider/export overlap conflicts recorded: {max(a['provider_overlap_conflicts'] for a in report['audit'].values())}. Original export values were preserved.",
              'Verified empty observations do not enter RSI/BB calculations and never receive entry or exit fills.','']
    lines += ['- '+s for s in report['limitations']]
    lines += ['','This tiny, single-asset in-sample replay does not establish profitability, a future success probability or a volume-success relationship.']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(roots=len(report['roots']),summary=report['summary'],output=str(args.output)),indent=2))


if __name__=='__main__':main()
