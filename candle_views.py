"""Finding navigation from immutable signal evidence to exact source candles."""
import csv
import html
import json
from pathlib import Path
from urllib.parse import urlencode
from finding_feedback import finding_id, latest_report


def link(source, signal):
    return '<a href="/candle?' + html.escape(urlencode(dict(source=source,finding=finding_id(source,signal))),quote=True) + '">View candles</a>'


def lookup(store, root, source, key):
    if source not in ('live','backtest'):raise ValueError('Invalid finding source')
    with store.connect() as db:
        if source=='live':
            signals=(json.loads(r[0]) for r in db.execute('SELECT payload FROM signals'))
        else:
            report=latest_report(db,root)
            signals=(s for r in report['results'] for s in r['signals']) if report else []
        signal=next((s for s in signals if finding_id(source,s)==key),None)
    if not signal:raise LookupError('Finding not found')
    return signal


def detail(store, root, source, key, target='pivot2'):
    signal=lookup(store,root,source,key)
    if target not in ('pivot1','pivot2','confirmation'):raise ValueError('Invalid candle target')
    asset_id=signal['symbol'];tf=signal['timeframe'];center=signal['confirmed_at'] if target=='confirmation' else signal[target]
    rows=[];mapping='';note=''
    if source=='backtest' and not asset_id.startswith(('alpaca:', 'tiingo:')):
        path=Path(root)/'data'/'historical-2020'/f'{asset_id}-{tf}.csv'
        # Asset comes from stored evidence; reject path separators to keep file access bounded.
        if '/' not in asset_id and '\\' not in asset_id and path.is_file():
            from scanner import timestamp
            from collections import deque
            before=deque(maxlen=100);after=[]
            with path.open(newline='') as f:
                for r in csv.DictReader(f):
                    start,end=timestamp(r['timestamp']),timestamp(r['closed_at'])
                    c=dict(start=start,end=end,**{k:float(r[k]) for k in ('open','high','low','close')})
                    if (end if target=='confirmation' else start)<center:before.append(c)
                    elif len(after)<101:after.append(c)
                    else:break
            rows=list(before)+after
    else:
        with store.connect() as db:
            table='backtest_candle_cache' if source=='backtest' else 'candle_cache'
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone()
            if exists:
                column='end' if target=='confirmation' else 'start'
                columns='start,end,open,high,low,close'
                before=db.execute(f'SELECT {columns} FROM {table} WHERE feed=? AND timeframe=? AND {column}<? ORDER BY start DESC LIMIT 100',(asset_id,tf,center)).fetchall()[::-1]
                after=db.execute(f'SELECT {columns} FROM {table} WHERE feed=? AND timeframe=? AND {column}>=? ORDER BY start LIMIT 101',(asset_id,tf,center)).fetchall()
                rows=[dict(zip(('start','end','open','high','low','close'),r)) for r in before+after]
    # User-verified mapping only; never infer a forex/broker feed from a display ticker.
    with store.connect() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='managed_config'").fetchone()
        state=db.execute('SELECT payload FROM managed_config WHERE id=1').fetchone() if exists else None
    if state:
        for a in json.loads(state[0])['assets']:
            from managed_assets import for_timeframe
            a = for_timeframe(a, tf)
            feed=f"{a['provider']}:{a.get('exchange') or 'configured'}:{a.get('symbol') or a['id']}"
            if a['id']==asset_id or feed==asset_id:
                mapping=a.get('tradingview_symbol','');break
    interval={'monthly':'M','weekly':'W','daily':'D','4h':'240','1h':'60'}[tf]
    url='https://www.tradingview.com/chart/?'+urlencode(dict(symbol=mapping,interval=interval)) if mapping else None
    note='External TradingView is a comparison feed; exact historical date navigation is not guaranteed.'
    return dict(signal=signal,candles=rows,target=target,target_time=center,tradingview_url=url,note=note)
