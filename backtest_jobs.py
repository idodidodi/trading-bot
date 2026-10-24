"""One bounded background replay at a time; live scanning stays independent."""
from postgres_store import RETRYABLE, DatabaseError
import json
import time
from platform_app import ROOT, load_rules

STRATEGIES = {
    'confirmed': 'RSI(3), Wilder; Bollinger Bands(20, 2 population deviations). Strict close-price pivots, 2 left / 1 right; compare all earlier same-type pivots at spacing 2–60 (at least one intervening candle). Opposing closing price and RSI; only P2 must wick-touch its band. No slope filter. Structural confirmation after the following candle closes; dashboard/history only, no additional live Telegram alert.',
    'warmup': 'Same RSI, bands and pivot-pair rules. Potential second pivot evaluated at its own close using only preceding candles. Live Telegram alert stage; subsequent candles can invalidate the candidate.'
}

def options(store, job_id=None):
    from finding_feedback import latest_report
    with store.connect() as db:
        report = latest_report(db, ROOT)
        db.require_table('backtest_jobs')
        job = db.execute('SELECT id,status,payload,updated FROM backtest_jobs WHERE id=%s',(job_id,)).fetchone() if job_id else db.execute('SELECT id,status,payload,updated FROM backtest_jobs ORDER BY updated DESC LIMIT 1').fetchone()
    assets = {r['asset'] for r in report['results']} if report else set()
    with store.connect() as db:
        assets.update(r[0] for r in db.execute('SELECT DISTINCT feed FROM backtest_candle_cache'))
    catalog=ROOT/'data/historical-2020/backtest-config.json'
    if catalog.exists(): assets.update(a['id'] for a in json.loads(catalog.read_text(encoding='utf-8'))['assets'])
    from alpaca_feed import supported
    from managed_assets import effective
    from scanner import load_config
    live, _ = effective(store, load_config())
    alpaca_assets = sorted(a['id'] for a in live['assets'] if supported(a))
    tiingo_assets = sorted(a['id'] for a in live['assets'] if a.get('market')=='forex' and a.get('feed_confirmed') is True)
    fmp_assets = sorted(a['id'] for a in live['assets'] if a.get('provider')=='fmp' and a.get('feed_confirmed') is True)
    csv_assets = sorted(assets)
    assets=sorted(assets | set(alpaca_assets) | set(tiingo_assets) | set(fmp_assets))
    return dict(assets=assets, strategies=STRATEGIES, timeframes=['monthly','weekly','daily','4h','1h'], year=report.get('year', 2020) if report else 2020,
                sources=['csv','alpaca','tiingo','fmp'], fmp_assets=fmp_assets, csv_assets=csv_assets, alpaca_assets=alpaca_assets, tiingo_assets=tiingo_assets,
                job=dict(id=job[0],status=job[1],**json.loads(job[2]),updated=job[3]) if job else None)

def validate(body, available):
    assets=body.get('assets');frames=body.get('timeframes');strategy=body.get('strategy')
    if (not isinstance(assets,list) or not assets or len(assets)>100 or not set(assets)<=set(available)
        or not isinstance(frames,list) or not frames or not set(frames)<= {'monthly','weekly','daily','4h','1h'} or strategy not in STRATEGIES):
        raise ValueError('Select available historical assets, a strategy and timeframes')
    return assets,frames,strategy

def start(store, body):
    validate(body,options(store)['assets'])
    if body.get('source', 'csv') not in ('csv','alpaca','tiingo','fmp'):
        raise ValueError('Invalid replay source')
    import uuid
    job=str(uuid.uuid4())
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(714269004)')
        if db.execute("SELECT 1 FROM backtest_jobs WHERE status IN ('pending','running')").fetchone():
            raise LookupError('A backtest is already active')
        db.execute('INSERT INTO backtest_jobs VALUES(%s,%s,%s,%s)',(job,'pending',json.dumps(body),time.time()))
    return dict(id=job,status='pending',runtime=True)


def execute(store,job,body):
    try:
        assets,frames,strategy=body['assets'],body['timeframes'],body['strategy']
        from backtest import run_backtest
        year=options(store)['year']
        catalog_path=ROOT/f'data/historical-{year}/backtest-config.json'
        cfg=json.loads(catalog_path.read_text()) if catalog_path.exists() else dict(assets=[dict(id=a,provider='csv',path=f'data/historical-{year}/'+a+'-{timeframe}.csv') for a in assets])
        cfg['year']=year
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
        if body.get('source') == 'fmp':
            from managed_assets import effective
            from scanner import load_config
            live, _ = effective(store, load_config())
            feeds = {a['id']: a for a in live['assets'] if a.get('provider') == 'fmp'}
            cfg['assets'] = [feeds.get(a, dict(id=a, provider='unsupported')) for a in assets]
        run_backtest(store,cfg,load_rules(),provisional=strategy=='warmup',job_id=job)
        status='completed';result=body
    except (DatabaseError, *RETRYABLE):
        raise
    except Exception:
        status='failed';result=body|dict(error='Historical replay failed; check selected data source and credentials')
        store.log('Backtest failed',result['error'])
    if status == 'failed':
        with store.connect() as db:db.execute('UPDATE backtest_jobs SET status=%s,payload=%s,updated=%s WHERE id=%s',(status,json.dumps(result),time.time(),job))



def worker(store,stop):
    with store.connect() as db:
        db.execute("UPDATE backtest_jobs SET status='pending' WHERE status='running' AND payload::jsonb->>'executor' IS DISTINCT FROM 'edge'")
    while not stop.is_set():
        with store.connect() as db:
            row=db.execute("""WITH picked AS (SELECT id FROM backtest_jobs WHERE status='pending' AND payload::jsonb->>'executor' IS DISTINCT FROM 'edge'
                ORDER BY updated FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE backtest_jobs j SET status='running' FROM picked p WHERE j.id=p.id
                RETURNING j.id,j.payload""").fetchone()
        if row:execute(store,row[0],json.loads(row[1]))
        else:stop.wait(1)
