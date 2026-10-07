"""One bounded background replay at a time; live scanning stays independent."""
import json
import threading
import time
from platform_app import ROOT, load_rules

_lock = threading.Lock()
STRATEGIES = {
    'confirmed': 'RSI(3), Wilder; Bollinger Bands(20, 2 population deviations). Consecutive strict pivots, 2 left / 1 right, spacing 5–60. Price lower low / higher high with opposing RSI and a band touch at P2. Alert after the following candle closes.',
    'warmup': 'Same RSI, bands and spacing. Potential second pivot evaluated at its own close using only preceding candles. Early warning; the following candle can invalidate the candidate.'
}

def options(store, job_id=None):
    from finding_feedback import latest_report
    with store.connect() as db:
        report = latest_report(db, ROOT)
        db.execute('CREATE TABLE IF NOT EXISTS backtest_jobs(id TEXT PRIMARY KEY,status TEXT,payload TEXT,updated REAL)')
        if not _lock.locked(): db.execute("UPDATE backtest_jobs SET status='failed',payload=json_set(payload,'$.error','Replay interrupted by restart'),updated=? WHERE status='running'",(time.time(),))
        job = db.execute('SELECT id,status,payload,updated FROM backtest_jobs WHERE id=?',(job_id,)).fetchone() if job_id else db.execute('SELECT id,status,payload,updated FROM backtest_jobs ORDER BY updated DESC LIMIT 1').fetchone()
    assets = {r['asset'] for r in report['results']} if report else set()
    catalog=ROOT/'data/historical-2020/backtest-config.json'
    if catalog.exists(): assets.update(a['id'] for a in json.loads(catalog.read_text())['assets'])
    from alpaca_feed import supported
    from managed_assets import effective
    from scanner import load_config
    live, _ = effective(store, load_config())
    alpaca_assets = sorted(a['id'] for a in live['assets'] if supported(a))
    tiingo_assets = sorted(a['id'] for a in live['assets'] if a.get('market')=='forex' and a.get('feed_confirmed') is True)
    csv_assets = sorted(assets)
    assets=sorted(assets | set(alpaca_assets) | set(tiingo_assets))
    return dict(assets=assets, strategies=STRATEGIES, timeframes=['monthly','weekly','daily','4h','1h'], year=2020,
                sources=['csv','alpaca','tiingo'], csv_assets=csv_assets, alpaca_assets=alpaca_assets, tiingo_assets=tiingo_assets,
                job=dict(id=job[0],status=job[1],**json.loads(job[2]),updated=job[3]) if job else None)

def validate(body, available):
    assets=body.get('assets');frames=body.get('timeframes');strategy=body.get('strategy')
    if (not isinstance(assets,list) or not assets or len(assets)>100 or not set(assets)<=set(available)
        or not isinstance(frames,list) or not frames or not set(frames)<= {'monthly','weekly','daily','4h','1h'} or strategy not in STRATEGIES):
        raise ValueError('Select available historical assets, a strategy and timeframes')
    return assets,frames,strategy

def start(store, body):
    available=options(store)['assets'];assets,frames,strategy=validate(body,available)
    if body.get('source', 'csv') not in ('csv', 'alpaca', 'tiingo'):
        raise ValueError('Select stored CSV history, Alpaca or Tiingo')
    if not _lock.acquire(blocking=False): raise LookupError('A backtest is already running')
    job=str(time.time_ns())
    try:
        with store.connect() as db:
            db.execute('INSERT INTO backtest_jobs VALUES(?,?,?,?)',(job,'running',json.dumps(body),time.time()))
    except Exception:
        _lock.release()
        raise
    def work():
        try:
            from backtest import run_backtest
            cfg=json.loads((ROOT/'data/historical-2020/backtest-config.json').read_text())
            catalog = {a['id']: a for a in cfg['assets']}
            cfg['assets']=[catalog.get(a, dict(id=a, provider='unsupported')) for a in assets];cfg['timeframes']=frames
            if body.get('source') == 'alpaca':
                from alpaca_feed import supported, fallback_asset
                from managed_assets import effective
                from scanner import load_config
                live, _ = effective(store, load_config())
                feeds = {a['id']: a for a in live['assets']}
                cfg['assets'] = [fallback_asset(feeds[a]) if a in feeds and supported(feeds[a])
                                 else dict(id=a, provider='unsupported') for a in assets]
            if body.get('source') == 'tiingo':
                from managed_assets import effective
                from scanner import load_config
                live, _ = effective(store, load_config())
                feeds = {a['id']: a for a in live['assets']}
                eligible = set(options(store)['tiingo_assets'])
                cfg['assets'] = [feeds[a] | dict(provider='tiingo', exchange='Tiingo', timeframe_providers={})
                                 if a in feeds and a in eligible
                                 else dict(id=a, provider='unsupported') for a in assets]
            run_backtest(store,cfg,load_rules(),provisional=strategy=='warmup')
            status='completed';result=body
        except Exception:
            status='failed';result=body|dict(error='Historical replay failed; check selected data source and credentials')
            store.log('Backtest failed',result['error'])
        finally:
            with store.connect() as db:db.execute('UPDATE backtest_jobs SET status=?,payload=?,updated=? WHERE id=?',(status,json.dumps(result),time.time(),job))
            _lock.release()
    threading.Thread(target=work,daemon=True).start()
    return dict(id=job,status='running')
