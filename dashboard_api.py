"""Small bounded read/write API shared by local and hosted dashboard bundles."""
import json
from urllib.parse import urlsplit, parse_qs
from finding_feedback import finding_id, latest_report
from platform_app import ROOT
import managed_assets


def ensure(store):
    from scanner import load_config
    return managed_assets.ensure(store,load_config())


def read(store, path):
    url=urlsplit(path);query=parse_qs(url.query)
    param=lambda key,default='':query.get(key,[default])[0]
    if url.path=='/api/backtests':
        from backtest_jobs import options
        return options(store,param('job') or None)
    if url.path=='/api/assets':
        from daily_universe import status
        return ensure(store) | dict(daily_selection=status(store))
    if url.path=='/api/candles':
        from candle_views import detail
        return detail(store,ROOT,param('source'),param('finding'),param('target','pivot2'))
    if url.path=='/api/logs':
        with store.connect() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='activity_log'").fetchone()
            mode=param('filter','all')
            where="WHERE lower(event || ' ' || details) LIKE '%error%' OR lower(event || ' ' || details) LIKE '%failed%' OR lower(details) LIKE '%unavailable%' OR lower(details) LIKE '%insufficient%' OR lower(details) LIKE '%retry%'" if mode!='all' else ''
            rows=db.execute(f'SELECT occurred,event,details FROM activity_log {where} ORDER BY id DESC LIMIT 200').fetchall() if exists else []
        items=[dict(zip(('occurred','event','details'),r)) for r in rows]
        mode=param('filter','all')
        if mode!='all':
            items=[r for r in items if any(w in (r['event']+' '+r['details']).lower() for w in ('error','failed','unavailable','insufficient','retry'))]
        return dict(rows=items)
    if url.path!='/api/findings':raise ValueError('Unknown API')
    source=param('source','live');page=int(param('page','0'))
    if source not in ('live','backtest') or not 0<=page<=10000:raise ValueError('Invalid page')
    with store.connect() as db:
        summary=[]
        if source=='live':
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='scanner_status'").fetchone()
            current=db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone() if exists else None
            summary=json.loads(current[0]).get('coverage',[]) if current else []
            signals=[json.loads(r[0]) for r in db.execute('SELECT payload FROM signals ORDER BY received DESC LIMIT 51 OFFSET ?',(page*50,))]
        else:
            report=latest_report(db,ROOT)
            all_signals=sorted([s for r in report['results'] for s in r['signals']],key=lambda s:s['confirmed_at'],reverse=True) if report else []
            signals=all_signals[page*50:page*50+51]
            summary=[dict(asset=r['asset'],timeframe=r['timeframe'],status=r['status'],signals=len(r['signals']),note=r.get('reason',r.get('note',''))) for r in report['results']] if report else []
        result=[]
        for s in signals[:50]:
            key=finding_id(source,s)
            fb=db.execute('SELECT rating,comment,revision FROM finding_feedback WHERE finding_id=?',(key,)).fetchone()
            result.append(dict(id=key,signal=s,feedback=dict(zip(('rating','comment','revision'),fb)) if fb else None))
    return dict(findings=result,has_more=len(signals)>50,summary=summary,scan=json.loads(current[0]) if source=='live' and current else None)
