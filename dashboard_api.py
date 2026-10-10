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
    if url.path=='/api/momentum':
        from momentum import view
        source=param('source','live')
        if source not in ('live','backtest'):raise ValueError('Invalid source')
        return view(store,source)
    if url.path=='/api/catchup':
        from scanner_control import status
        return status(store)
    if url.path=='/api/updates':
        import windows_update
        return windows_update.status()
    if url.path=='/api/backtests':
        from backtest_jobs import options
        return options(store,param('job') or None)
    if url.path=='/api/assets':
        from daily_universe import status
        from weekly_stock_screen import status as stock_status
        with store.connect() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='scanner_status'").fetchone()
            row=db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone() if exists else None
        return ensure(store) | dict(daily_selection=status(store),weekly_stock_selection=stock_status(store),coverage=json.loads(row[0]).get('coverage',[]) if row else [])
    if url.path=='/api/followup':
        from finding_followup import run
        return run(store,ROOT,param('source'),param('finding'),param('mode','recovery'))
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
            states={key: (status, revision) for key,status,revision in db.execute('SELECT finding_id,status,revision FROM finding_state')}
            visible=[]
            for row in db.execute('SELECT payload FROM signals ORDER BY received DESC,id'):
                signal=json.loads(row[0]);status=states.get(finding_id(source,signal),('active',0))[0]
                if status!='deleted' and (status!='archived' or param('archived')=='true'):visible.append(signal)
                if len(visible)>=page*50+51:break
            signals=visible[page*50:page*50+51]
        else:
            report=latest_report(db,ROOT)
            sort = param('sort', 'confirmed_at'); order = param('order', 'asc')
            if sort not in ('asset', 'timeframe', 'evidence', 'confirmed_at', 'rating') or order not in ('asc', 'desc'):
                raise ValueError('Invalid finding sort')
            ratings = dict(db.execute('SELECT finding_id,rating FROM finding_feedback'))
            def sort_value(s):
                return {'asset': s['symbol'],
                        'timeframe': {'1h': 60, '60': 60, '4h': 240, '240': 240, 'daily': 1440, 'D': 1440, 'weekly': 10080, 'W': 10080, 'monthly': 43200, 'M': 43200}.get(s['timeframe'], 0),
                        'evidence': (s['direction'], s.get('signal_status', 'confirmed')),
                        'confirmed_at': s['confirmed_at'],
                        'rating': ratings.get(finding_id(source, s))}[sort]
            all_signals = [s for r in report['results'] for s in r['signals']] if report else []
            all_signals.sort(key=lambda s: (s['confirmed_at'], finding_id(source, s)))
            rated = [s for s in all_signals if sort_value(s) is not None]
            missing = [s for s in all_signals if sort_value(s) is None]
            all_signals = sorted(rated, key=sort_value, reverse=order == 'desc') + missing
            signals=all_signals[page*50:page*50+51]
            summary=[dict(asset=r['asset'],timeframe=r['timeframe'],status=r['status'],signals=len(r['signals']),note=r.get('reason',r.get('note',''))) for r in report['results']] if report else []
        from finding_followup import stored
        precomputed = {k: v for r in report['results'] for k,v in r.get('followups',{}).items()} if source=='backtest' and report else {}
        result=[]
        for s in signals[:50]:
            key=finding_id(source,s)
            fb=db.execute('SELECT rating,comment,revision FROM finding_feedback WHERE finding_id=?',(key,)).fetchone()
            status,revision=states.get(key,('active',0)) if source=='live' else ('active',0)
            followup = precomputed.get(key) or stored(db,key)
            result.append(dict(id=key,signal=s,followup=followup.get('recovery') if followup else None,archived=status=='archived',state_revision=revision,feedback=dict(zip(('rating','comment','revision'),fb)) if fb else None))
    # Existing reports get outcomes on review; live findings remain click-only.
    if source == 'backtest':
        from finding_followup import run
        for item in result:
            if item['followup'] is None:
                item['followup'] = run(store, ROOT, source, item['id'])['followup']
    page_count = (len(all_signals) + 49) // 50 if source == 'backtest' else None
    return dict(findings=result,has_more=len(signals)>50,total_count=len(all_signals) if source == 'backtest' else None,
                page_count=page_count,summary=summary,scan=json.loads(current[0]) if source=='live' and current else None)
