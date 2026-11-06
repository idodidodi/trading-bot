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

from divergence_chasing import FRAMES, CHASE_FRAMES, DURATIONS, create, step, percentage


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


def run(paths, symbol, cutoff, stop_pct=.5, horizon=3, start_at=None, end_at=None):
    from platform_app import load_rules
    rules=load_rules();series={};volumes={};audit={};signals={}
    for frame in FRAMES:
        series[frame],volumes[frame],audit[frame]=read_series(paths[frame],frame,cutoff)
        signals[frame]=detect_series(series[frame],volumes[frame],rules,symbol,frame,audit[frame]['verified_empty']) if frame in CHASE_FRAMES else []
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
            if bar.end<session['next_due']:continue
            k=bisect_right(times,session['parent_at']);j=bisect_right(times,bar.end)
            session,new=step(session,bar.end,price=bar.close,price_at=bar.end,signals=children[k:j])
            events.extend(new)
            if session['status']!='chasing':break
        chase=[e for e in events if e['kind']=='chase']
        roots.append(dict(id=session['id'],signal=root,status=session['status'],stage=session['stage'],observation_end=min(end,common_end)))
        ledger.extend(events)
    summary=dict(roots=len(roots),chases=sum(e['kind']=='chase' for e in ledger))
    config=dict(symbol=symbol,cutoff=cutoff,stop_pct=percentage(stop_pct),
                chase_timeframes=list(CHASE_FRAMES),
                start_at=start_at,end_at=end_at,
                selection='first divergence, smallest timeframe first',horizon_4h_bars=horizon,rules=rules,
                gate='adverse to original close',observation='closed minute close')
    config['empty_minutes']='verified no-trade carry-forward observations; excluded from indicators and entry fills'
    config['source_hashes']={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                             for name in ('chasing_research.py','divergence_chasing.py','scanner.py')}
    report=dict(version=2,config=config,audit=audit,common_start=common_start,common_end=common_end,roots=roots,events=ledger,summary=summary,reconciliation=reconciliation,limitations=['Detection replay only; no execution or returns simulated.'])
    report['report_hash']=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for frame in FRAMES:p.add_argument('--'+frame,required=True,type=Path)
    p.add_argument('--symbol',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--stop-pct',type=percentage,default=.5);p.add_argument('--horizon',type=int,default=3)
    p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    from scanner import timestamp
    cutoff=timestamp(args.as_of)
    if cutoff>int(datetime.now(timezone.utc).timestamp()*1000) or args.horizon<1:p.error('Invalid cutoff/horizon')
    report=run({f:getattr(args,f) for f in FRAMES},args.symbol,cutoff,args.stop_pct,args.horizon)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'replay.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
