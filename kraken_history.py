"""Read only selected CSV members from Kraken's public split ZIP using HTTP ranges."""
import bisect
import csv
import io
import json
import re
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from prepare_history import DEST, UTC, aggregate, check_candles, stamp, write
from scanner import Candle

BASE = 'https://assets.kraken.com/marketing/institutions/Kraken_OHLCVT_Full_2026Q2.zip.part'


def fetch(url, first, last):
    with tempfile.TemporaryDirectory() as tmp:
        headers, body = Path(tmp)/'headers', Path(tmp)/'body'
        subprocess.run(['curl','-L','--fail','--silent','--show-error','--retry','2','--max-time','90','--range',f'{first}-{last}','-D',str(headers),url,'-o',str(body)],check=True)
        match = re.search(r'content-range: bytes (\d+)-(\d+)/(\d+)',headers.read_text(),re.I)
        if not match or int(match[1])!=first or int(match[2])!=last:
            raise ValueError('Server did not honor requested range')
        raw = body.read_bytes()
        if len(raw)!=last-first+1:
            raise ValueError('Truncated archive range')
        return raw,int(match[3])


class RemoteZip(io.RawIOBase):
    def __init__(self):
        self.sizes=[fetch(BASE+f'{i:02}',0,0)[1] for i in range(5)]
        self.offsets=[0]
        for size in self.sizes:self.offsets.append(self.offsets[-1]+size)
        self.pos=0
        self.cache={}
    def seekable(self):return True
    def readable(self):return True
    def tell(self):return self.pos
    def seek(self,offset,whence=0):
        self.pos=offset if whence==0 else self.pos+offset if whence==1 else self.offsets[-1]+offset
        return self.pos
    def read(self,n=-1):
        n=min(n if n>=0 else self.offsets[-1],self.offsets[-1]-self.pos)
        chunks=[]
        while n>0:
            part=bisect.bisect_right(self.offsets,self.pos)-1
            local=self.pos-self.offsets[part]
            count=min(n,self.sizes[part]-local)
            key=(part,local,count)
            if key not in self.cache:self.cache[key]=fetch(BASE+f'{part:02}',local,local+count-1)[0]
            chunks.append(self.cache[key]);self.pos+=count;n-=count
        return b''.join(chunks)


def main():
    manifest=json.loads((DEST/'manifest.json').read_text())
    archive=zipfile.ZipFile(RemoteZip())
    members=archive.namelist()
    (DEST/'raw'/'kraken-members.json').write_text(json.dumps(members))
    for asset,pair in [('BTCUSD','XBTUSD'),('NEARUSD','NEARUSD')]:
        for minutes,frames in [(1440,('daily','weekly','monthly')),(240,('4h',))]:
            matches=[m for m in members if Path(m).name==f'{pair}_{minutes}.csv']
            if len(matches)!=1:raise ValueError(f'Cannot find unique {pair}_{minutes}.csv')
            member=matches[0]
            cached=DEST/'raw'/Path(member).name
            if not cached.exists():
                cached.write_bytes(archive.read(member))
            bars=[]
            first=None
            for row in csv.reader(io.StringIO(cached.read_text())):
                t=int(float(row[0]));first=t if first is None else min(first,t)
                if t>=1609459200:continue
                bars.append(Candle(t*1000,(t+minutes*60)*1000,*map(float,row[1:5])))
            bars=check_candles(bars,1609459200000)
            if not bars:
                manifest['errors'].append(dict(asset=asset,timeframes=frames,error='Kraken source has no pre-2021 candles',first_trade_utc=datetime.fromtimestamp(first,UTC).isoformat() if first else None))
                print(asset,minutes,'no pre-2021 data',flush=True)
                continue
            for tf in frames:
                manifest['files'].append(write(asset,tf,bars if tf in ('daily','4h') else aggregate(bars,tf),dict(provider='Kraken',archive=BASE+'00 through part04',member=member)))
            print(asset,minutes,len(bars),'candles prepared',flush=True)
    manifest['notes']=[n for n in manifest['notes'] if 'Coinbase' not in n and 'declined' not in n]
    manifest['notes'].append('Crypto source: Kraken official OHLCVT archive; native 240m/daily bars. Missing trade intervals are not fabricated.')
    (DEST/'manifest.json').write_text(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
