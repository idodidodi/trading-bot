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
    assets=sorted(assets)
    return dict(assets=assets, strategies=STRATEGIES, timeframes=['monthly','weekly','daily','4h'], year=2020,
                job=dict(id=job[0],status=job[1],**json.loads(job[2]),updated=job[3]) if job else None)

def validate(body, available):
    assets=body.get('assets');frames=body.get('timeframes');strategy=body.get('strategy')
    if (not isinstance(assets,list) or not assets or len(assets)>100 or not set(assets)<=set(available)
        or not isinstance(frames,list) or not frames or not set(frames)<= {'monthly','weekly','daily','4h'} or strategy not in STRATEGIES):
        raise ValueError('Select available historical assets, a strategy and timeframes')
    return assets,frames,strategy

def start(store, body):
    available=options(store)['assets'];assets,frames,strategy=validate(body,available)
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
            cfg['assets']=[a for a in cfg['assets'] if a['id'] in assets];cfg['timeframes']=frames
            run_backtest(store,cfg,load_rules(),provisional=strategy=='warmup')
            status='completed';result=body
        except Exception:
            status='failed';result=body|dict(error='Historical replay failed; check available CSV history')
            store.log('Backtest failed',result['error'])
        finally:
            with store.connect() as db:db.execute('UPDATE backtest_jobs SET status=?,payload=?,updated=? WHERE id=?',(status,json.dumps(result),time.time(),job))
            _lock.release()
    threading.Thread(target=work,daemon=True).start()
    return dict(id=job,status='running')
