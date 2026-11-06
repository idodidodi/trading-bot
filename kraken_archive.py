"""Seekable, cached HTTP byte ranges for Kraken's concatenated public ZIP parts."""
import io
import hashlib
from pathlib import Path
import re
import ssl
import time
import urllib.request


class RemoteZip(io.RawIOBase):
    def __init__(self,urls,cache,block=1024*1024):
        import certifi
        self.urls=urls;self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.context=ssl.create_default_context(cafile=certifi.where());self.block=block
        self.sizes=[];self.position=0
        for url in urls:
            _,size=self.range(url,0,0);self.sizes.append(size)
        self.size=sum(self.sizes)

    def range(self,url,start,end):
        path=self.cache/(hashlib.sha256(url.encode()).hexdigest()[:16]+f'-{start}-{end}')
        meta=path.with_suffix('.size')
        if path.exists() and meta.exists():return path.read_bytes(),int(meta.read_text())
        for attempt in range(5):
            try:
                headers={'User-Agent':'Mozilla/5.0','Referer':'https://support.kraken.com/',
                         'Range':f'bytes={start}-{end}','Accept-Encoding':'identity'}
                req=urllib.request.Request(url,headers=headers)
                with urllib.request.urlopen(req,context=self.context,timeout=60) as response:
                    match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                    if response.status!=206 or not match:raise ValueError('Archive server did not honor byte range')
                    lo,hi,size=map(int,match.groups())
                    if lo!=start or hi!=min(end,size-1):raise ValueError('Unexpected archive byte range')
                    data=response.read(end-start+2)
                    if len(data)!=hi-lo+1:raise ValueError('Truncated archive byte range')
                path.write_bytes(data);meta.write_text(str(size))
                return data,size
            except (OSError,ValueError):
                if attempt==4:raise
                time.sleep(min(16,2**attempt))

    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        self.position=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        if self.position<0:raise ValueError('Negative archive position')
        return self.position
    def read(self,n=-1):
        n=min(self.size-self.position,n if n>=0 else self.size)
        out=bytearray()
        while n>0:
            base=0
            for index,size in enumerate(self.sizes):
                if self.position<base+size:break
                base+=size
            local=self.position-base;start=local//self.block*self.block
            data,_=self.range(self.urls[index],start,min(start+self.block,self.sizes[index])-1)
            offset=local-start;take=min(n,len(data)-offset)
            if take<=0:raise ValueError('Archive read did not advance')
            out.extend(data[offset:offset+take]);self.position+=take;n-=take
        return bytes(out)
