"""Repair exported Kraken candle gaps using public historical trades, with provenance.

No credentials, production database, scanner, Telegram or trading operations are used.
Only --include-empty permits labeled zero-volume carry-forward observation rows.
"""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

FRAMES={'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400}


def timestamp(text):
    dt=datetime.fromisoformat(text.replace('Z','+00:00'))
    if dt.tzinfo is None:raise ValueError('Timezone required')
    return int(dt.timestamp())


def stamp(at):return datetime.fromtimestamp(at,timezone.utc).isoformat()


def read(path,frame,cutoff):
    data={}
    with Path(path).open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            at=timestamp(row['time'])
            if at+FRAMES[frame]>cutoff:continue
            values=[Decimal(row[k]) for k in ('open','high','low','close')]
            v=Decimal(row.get('Volume',row.get('volume','')))
            o,h,l,c=values
            if not all(n.is_finite() for n in values+[v]) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('Invalid OHLCV')
            item=dict(time=at,open=o,high=h,low=l,close=c,Volume=v,source_status='tradingview')
            if at in data and data[at]!=item:raise ValueError('Conflicting duplicate candle')
            data[at]=item
    if not data:raise ValueError('No completed rows')
    return data


def aggregate(rows):
    rows=sorted(rows,key=lambda r:(r[2],r[6]))
    if not rows:return None
    prices=[Decimal(r[0]) for r in rows];volumes=[Decimal(r[1]) for r in rows]
    if not all(p.is_finite() and p>0 for p in prices) or not all(v.is_finite() and v>0 for v in volumes):raise ValueError('Invalid trade')
    return dict(open=prices[0],high=max(prices),low=min(prices),close=prices[-1],Volume=sum(volumes),n_trades=len(rows))


def matches(a,b):
    return all(math.isclose(float(a[k]),float(b[k]),rel_tol=1e-6 if k=='Volume' else 1e-8,abs_tol=1e-7)
               for k in ('open','high','low','close','Volume'))


class Client:
    def __init__(self,cache,pair,spacing=1):
        import certifi
        self.context=ssl.create_default_context(cafile=certifi.where())
        self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.pair=pair;self.spacing=max(1.,spacing);self.last_request=0

    def page(self,since):
        p=self.cache/f'{self.pair.replace("/", "_")}-{since}.json'
        if p.exists():raw=p.read_bytes()
        else:
            url='https://api.kraken.com/0/public/Trades?'+urllib.parse.urlencode(dict(pair=self.pair.replace('/',''),assetVersion=1,since=str(since),count=1000))
            for attempt in range(5):
                time.sleep(max(0,self.spacing-(time.monotonic()-self.last_request)))
                self.last_request=time.monotonic()
                try:
                    with urllib.request.urlopen(url,context=self.context,timeout=30) as response:raw=response.read(2_000_001)
                    if len(raw)>2_000_000:raise ValueError('Oversized trade page')
                    check=json.loads(raw)
                    if check.get('error'):raise ValueError('Kraken rejected trade query: '+str(check['error']))
                    break
                except (OSError,ValueError):
                    if attempt==4:raise
                    time.sleep(min(30,2**attempt))
            p.write_bytes(raw)
        result=json.loads(raw,parse_float=Decimal)
        if result.get('error') or set(result.get('result',{}))!={self.pair,'last'}:raise ValueError('Wrong pair or invalid trade response')
        data=result['result'];rows=data[self.pair];cursor=int(data['last'])
        if not rows or cursor<=since:raise ValueError('Trade cursor did not advance')
        if any(len(r)<7 or int(Decimal(str(r[2]))*10**9)<since for r in rows):raise ValueError('Trade history does not honor requested boundary')
        if any(Decimal(str(a[2]))>Decimal(str(b[2])) for a,b in zip(rows,rows[1:])):raise ValueError('Trade timestamps are unordered')
        return rows,cursor,dict(file=p.name,sha256=hashlib.sha256(raw).hexdigest(),since=str(since),cursor=str(cursor))


def repair(original,fetch,max_pages=1000,record_differences=False):
    missing=set(range(min(original),max(original)+60,60))-set(original)
    unresolved=set(missing);recovered={};empty=set();proofs=[];overlap={};conflicts={};requests=0
    while unresolved and requests<max_pages:
        first=min(unresolved)
        # Include the existing left-hand candle for feed/volume reconciliation.
        lower=first-60
        while lower not in original and lower>=min(original):lower-=60
        since=lower*10**9;trades={};upper=lower
        while upper<first+120 and requests<max_pages:
            rows,cursor,proof=fetch(since);requests+=1;proofs.append(proof)
            for r in rows:
                identity=str(r[6])
                if identity in trades and trades[identity]!=r:raise ValueError('Conflicting trade ID')
                trades[identity]=r
            upper=int(Decimal(str(rows[-1][2])))//60*60
            if cursor<=since:raise ValueError('Non-advancing cursor')
            since=cursor
        buckets={}
        for row in trades.values():
            at=int(Decimal(str(row[2])))//60*60
            if lower<=at<upper:buckets.setdefault(at,[]).append(row)
        checked=0
        for at,rows in buckets.items():
            bar=aggregate(rows)
            if at in original:
                if not matches(original[at],bar):
                    if not record_differences:raise ValueError(f'Kraken/export OHLCV mismatch at {stamp(at)}')
                    conflicts[at]=dict(time=stamp(at),original={k:str(original[at][k]) for k in ('open','high','low','close','Volume')},
                                       provider={k:str(bar[k]) for k in ('open','high','low','close','Volume')})
                overlap[at]=True;checked+=1
        if not checked:raise ValueError('No complete overlap candle to verify source equivalence')
        covered=[at for at in unresolved if lower<=at<upper]
        if not covered:raise ValueError('No requested gap covered by trade history')
        for at in covered:
            if at in buckets:recovered[at]=dict(time=at,source_status='kraken_trades',**aggregate(buckets[at]))
            else:empty.add(at)
            unresolved.remove(at)
        if requests%20==0 or not unresolved:
            print(f'pages={requests} recovered={len(recovered)} verified_empty={len(empty)} unresolved={len(unresolved)}',flush=True)
    return recovered,empty,unresolved,dict(pages=requests,overlap_candles=len(overlap),overlap_conflicts=list(conflicts.values()),proofs=proofs)


def write_csv(path,rows):
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['time','open','high','low','close','Volume','source_status','n_trades'])
        writer.writeheader()
        for at,row in sorted(rows.items()):writer.writerow(dict(row,time=stamp(at)))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for frame in FRAMES:p.add_argument('--'+frame,type=Path,required=frame=='1m')
    p.add_argument('--pair',default='NEAR/USD');p.add_argument('--as-of',required=True)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--cache',required=True,type=Path)
    p.add_argument('--include-empty',action='store_true');p.add_argument('--max-pages',type=int,default=1000)
    p.add_argument('--record-overlap-differences',action='store_true',help='Keep export values and record provider conflicts; never silently overwrite them')
    args=p.parse_args()
    if not re.fullmatch(r'[A-Z0-9]+/[A-Z0-9]+',args.pair) or args.max_pages<1:p.error('Invalid pair or page limit')
    cutoff=timestamp(args.as_of)
    if cutoff>int(time.time()):p.error('As-of must not be in the future')
    inputs={f:read(getattr(args,f),f,cutoff) for f in FRAMES if getattr(args,f)}
    original=inputs['1m'];client=Client(args.cache,args.pair)
    recovered,empty,unresolved,evidence=repair(original,client.page,args.max_pages,args.record_overlap_differences)
    merged=original|recovered
    for at in sorted(empty):
        if args.include_empty:
            previous=merged.get(at-60)
            if previous is None:unresolved.add(at);continue
            price=previous['close']
            merged[at]=dict(time=at,open=price,high=price,low=price,close=price,Volume=Decimal(0),source_status='verified_no_trades',n_trades=0)
    args.output.mkdir(parents=True,exist_ok=False)
    outputs={};outputs['1m']=dict(inserted_trade=len(recovered),verified_empty=len(empty),remaining_gaps=len(set(range(min(original),max(original)+60,60))-set(merged)))
    write_csv(args.output/'1m.csv',merged)
    for frame,source in inputs.items():
        if frame=='1m':continue
        rows=dict(source);filled=0
        for at in sorted(set(range(min(source),max(source)+FRAMES[frame],FRAMES[frame]))-set(source)):
            children=[merged.get(t) for t in range(at,at+FRAMES[frame],60)]
            if any(c is None for c in children):continue
            no_trades=all(c['source_status']=='verified_no_trades' for c in children)
            rows[at]=dict(time=at,open=children[0]['open'],high=max(c['high'] for c in children),low=min(c['low'] for c in children),close=children[-1]['close'],Volume=sum(c['Volume'] for c in children),source_status='verified_no_trades' if no_trades else 'kraken_aggregate',n_trades=0 if no_trades else '')
            filled+=1
        write_csv(args.output/f'{frame}.csv',rows)
        outputs[frame]=dict(inserted=filled,remaining_gaps=len(set(range(min(source),max(source)+FRAMES[frame],FRAMES[frame]))-set(rows)))
    manifest=dict(pair=args.pair,cutoff=cutoff,include_empty=args.include_empty,empty_observation_policy='carry last traded close; zero volume; never executable',
                  input_hashes={f:hashlib.sha256(getattr(args,f).read_bytes()).hexdigest() for f in inputs},
                  outputs=outputs,verified_empty_minutes=sorted(empty),unresolved_minutes=sorted(unresolved),evidence=evidence,
                  source_status='mixed export/provider observations; inspect overlap conflicts',
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    manifest['output_hashes']={f:hashlib.sha256((args.output/f'{f}.csv').read_bytes()).hexdigest() for f in inputs}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (args.output/'report.md').write_text('# Kraken minute repair\n\n'+f'Pair: {args.pair}. Public historical trades only.\n\n'+
        '\n'.join(f'- {frame}: {value}' for frame,value in outputs.items())+
        f"\n\nOverlap candles checked: {evidence['overlap_candles']}. Conflicting candles: {len(evidence['overlap_conflicts'])}. Original values are preserved; conflicts are recorded in manifest.json.\n"+
        '\nVerified empty intervals carry the last traded close for observation only; fills are prohibited.\n')
    print(json.dumps(outputs,indent=2))
    return 2 if unresolved else 0


if __name__=='__main__':raise SystemExit(main())
