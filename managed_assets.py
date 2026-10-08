"""Versioned asset configuration; scanner.json is imported, never rewritten."""
import copy
import json
import re
import time

DEFAULTS = ['monthly', 'weekly', 'daily', '4h']
SUPPORTED_TIMEFRAMES = DEFAULTS + ['1h']
LIMIT = 100
PROVIDERS = ('csv', 'twelvedata', 'kraken', 'oanda', 'tiingo', 'alpaca')
TOKEN = re.compile(r'[A-Za-z0-9_^=:.!/\-]{1,100}')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS managed_config(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, applied_revision INTEGER NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS config_history(revision INTEGER PRIMARY KEY, payload TEXT NOT NULL, changed REAL NOT NULL)')
    db.execute('''CREATE TABLE IF NOT EXISTS candle_cache(feed TEXT, asset TEXT, timeframe TEXT, start INTEGER, end INTEGER, open REAL, high REAL, low REAL, close REAL, PRIMARY KEY(feed,timeframe,start))''')


def ensure(store, config):
    value = copy.deepcopy(config)
    for asset in value['assets']:
        asset.setdefault('timeframes', value.get('timeframes', DEFAULTS))
        asset.setdefault('enabled', True)
        asset.setdefault('tradingview_symbol', '')
    with store.connect() as db:
        initialize(db)
        db.execute('INSERT OR IGNORE INTO managed_config VALUES(1,1,0,?)', (json.dumps(value),))
    return get(store)


def get(store):
    with store.connect() as db:
        row = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
    if not row:
        raise ValueError('Asset configuration has not been initialized')
    result=dict(revision=row[0], applied_revision=row[1], config=json.loads(row[2]))
    with store.connect() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_meta'").fetchone():
            result['sync']=dict(db.execute("SELECT key,value FROM cloud_meta WHERE key IN ('error','last_sync')"))
            result['pending_uploads']=db.execute('SELECT count(*) FROM cloud_pending').fetchone()[0]
            result['conflicts']=db.execute('SELECT count(*) FROM cloud_conflicts').fetchone()[0]
            result['configuration_conflict']=bool(db.execute("SELECT 1 FROM cloud_conflicts WHERE id='config'").fetchone())
    return result


def validate_asset(asset):
    if not isinstance(asset, dict) or not isinstance(asset.get('id'),str) or not TOKEN.fullmatch(asset['id']):
        raise ValueError('Invalid ticker')
    if asset.get('provider') not in PROVIDERS:
        raise ValueError('Select CSV, Twelve Data, Kraken, OANDA, Tiingo or Alpaca')
    if type(asset.get('enabled')) is not bool:
        raise ValueError('Invalid enabled state')
    frames=asset.get('timeframes')
    if not isinstance(frames,list) or not frames or any(f not in SUPPORTED_TIMEFRAMES for f in frames) or len(frames)!=len(set(frames)):
        raise ValueError('Choose at least one supported candle timeframe')
    for key in ('symbol','exchange','tradingview_symbol'):
        value=asset.get(key,'')
        if not isinstance(value,str) or (value and not TOKEN.fullmatch(value)):
            raise ValueError(f'Invalid {key}')
    if asset.get('tradingview_symbol') and ':' not in asset['tradingview_symbol']:
        raise ValueError('TradingView symbol must include its exchange, e.g. NASDAQ:NVDA')
    overrides = asset.get('timeframe_providers', {})
    if not isinstance(overrides, dict) or any(f not in SUPPORTED_TIMEFRAMES or p not in ('twelvedata', 'kraken', 'oanda', 'tiingo', 'alpaca') for f, p in overrides.items()):
        raise ValueError('Invalid timeframe providers')
    if overrides and asset['provider'] == 'csv':
        raise ValueError('Timeframe provider overrides require an API feed')
    if asset['provider'] in ('kraken', 'oanda', 'tiingo') and 'twelvedata' in overrides.values():
        raise ValueError('Use Twelve Data as the primary feed and Kraken timeframe overrides for mixed venues')
    if asset['enabled'] and any(for_timeframe(asset, f)['provider'] != 'csv' for f in frames) and (not asset.get('symbol') or asset.get('feed_confirmed') is not True):
        raise ValueError('Confirm the exact provider symbol before enabling')
    from kraken_feed import validate_feed
    for frame in frames:
        if asset.get('timeframe_providers', {}).get(frame, asset['provider']) == 'alpaca' and asset['enabled']:
            from alpaca_feed import supported
            if not supported(asset):
                raise ValueError('Alpaca requires a confirmed US equity or ETF feed')
        feed = for_timeframe(asset, frame)
        if feed['provider'] == 'kraken' and asset['enabled']:
            if asset['provider'] == 'kraken' and asset.get('exchange') != 'Kraken':
                raise ValueError('Select Kraken as the provider exchange')
            validate_feed(feed, frame)
        if feed['provider'] == 'oanda' and asset['enabled']:
            from oanda_feed import validate_feed as validate_oanda
            if asset['provider'] == 'oanda' and asset.get('exchange') != 'OANDA':
                raise ValueError('Select OANDA as the provider exchange')
            validate_oanda(feed, frame)

        if feed['provider'] == 'tiingo' and asset['enabled']:
            from tiingo_feed import validate_feed as validate_tiingo
            if asset['provider'] == 'tiingo' and asset.get('exchange') != 'Tiingo':
                raise ValueError('Select Tiingo as the provider exchange')
            validate_tiingo(feed, frame)


def for_timeframe(asset, timeframe):
    """Resolve an explicitly selected provider while retaining feed identity."""
    feed = dict(asset)
    feed['provider'] = asset.get('timeframe_providers', {}).get(timeframe, asset['provider'])
    if feed['provider'] == 'alpaca':
        from alpaca_feed import fallback_asset, supported
        if supported(asset):
            feed = fallback_asset(asset)
        if asset['provider'] == 'alpaca':
            feed['tradingview_symbol'] = asset.get('tradingview_symbol', '')
    if feed['provider'] == 'kraken':
        feed['exchange'] = 'Kraken'
        # A mapping for the primary venue is not a verified Kraken chart mapping.
        if asset['provider'] != 'kraken':
            feed['tradingview_symbol'] = ''
    if feed['provider'] == 'oanda':
        import os
        feed['exchange'] = 'OANDA-' + os.environ.get('OANDA_ENVIRONMENT', 'practice')
        if asset['provider'] != 'oanda':
            feed['tradingview_symbol'] = ''
    if feed['provider'] == 'tiingo':
        feed['exchange'] = 'Tiingo-UTC'
        if asset['provider'] != 'tiingo':
            feed['tradingview_symbol'] = ''
    return feed


def change(store, body):
    if not isinstance(body,dict) or type(body.get('revision')) is not int:
        raise ValueError('Expected configuration revision')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT revision,payload FROM managed_config WHERE id=1').fetchone()
        if not row or row[0]!=body['revision']:
            raise LookupError('Assets changed in another session. Reload before saving.')
        config=json.loads(row[1]);assets=config['assets'];results=[]
        if body.get('action')=='accept_online':
            remote=db.execute("SELECT payload FROM cloud_conflicts WHERE id='config'").fetchone()
            if not remote:raise ValueError('No online configuration conflict')
            cloud=json.loads(remote[0])
            for asset in cloud['config']['assets']:validate_asset(asset)
            db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1',(cloud['revision'],json.dumps(cloud['config'])))
            db.execute("DELETE FROM cloud_pending WHERE kind='config'")
            db.execute("DELETE FROM cloud_seen WHERE kind='config'")
            db.execute("DELETE FROM cloud_conflicts WHERE id='config'")
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('config_base',str(cloud['revision'])))
            db.execute('INSERT OR REPLACE INTO cloud_meta VALUES(?,?)',('error',''))
            return dict(revision=cloud['revision'],applied_revision=0,config=cloud['config'],results=[dict(ticker='Configuration',status='online version accepted; pending scanner')])
        if body.get('action')=='bulk':
            text=body.get('tickers')
            if not isinstance(text,str) or len(text)>10000:
                raise ValueError('Enter a comma-separated list, up to 100 tickers')
            tokens=list(dict.fromkeys(t.strip().upper() for t in text.split(',') if t.strip()))
            if not tokens or len(tokens)>LIMIT:raise ValueError('Enter 1–100 tickers')
            known={a['id'].upper() for a in assets}
            for token in tokens:
                if not TOKEN.fullmatch(token):results.append(dict(ticker=token,status='invalid'));continue
                if token in known:results.append(dict(ticker=token,status='already added'));continue
                if len(assets)>=LIMIT:results.append(dict(ticker=token,status='asset limit reached'));continue
                # New entries remain drafts until their actual data feed is configured.
                assets.append(dict(id=token,provider='csv',path='data/candles/{asset}-{timeframe}.csv',enabled=False,timeframes=DEFAULTS.copy(),tradingview_symbol=''))
                known.add(token);results.append(dict(ticker=token,status='added as disabled draft'))
        elif body.get('action')=='remove':
            ticker=body.get('ticker')
            if not isinstance(ticker,str) or not TOKEN.fullmatch(ticker):raise ValueError('Select a valid asset')
            index=next((i for i,a in enumerate(assets) if a['id']==ticker),None)
            if index is None:raise ValueError('Asset was already removed')
            assets.pop(index)
            results=[dict(ticker=ticker,status='removed from assets; stored history was retained')]
        elif body.get('action')=='save':
            incoming=body.get('asset');validate_asset(incoming)
            index=next((i for i,a in enumerate(assets) if a['id']==incoming['id']),None)
            if index is None:raise ValueError('Add the ticker before editing it')
            current=assets[index]
            validate_asset(current | incoming)
            # Do not accept arbitrary file paths, credentials or unknown scanner fields.
            for key in ('provider','enabled','timeframes','symbol','exchange','tradingview_symbol','feed_confirmed','timeframe_providers'):
                if key in incoming:current[key]=incoming[key]
            current.setdefault('path','data/candles/{asset}-{timeframe}.csv')
            results=[dict(ticker=current['id'],status='saved')]
        else:raise ValueError('Invalid asset operation')
        payload=json.dumps(config);revision=row[0]+1
        db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1',(revision,payload))
        db.execute('INSERT INTO config_history VALUES(?,?,?)',(revision,payload,time.time()))
        from cloud_sync import enqueue
        applied=db.execute('SELECT applied_revision FROM managed_config WHERE id=1').fetchone()[0]
        enqueue(db,'config','current',dict(revision=revision,applied_revision=applied,config=config))
    return get(store)|dict(results=results)


def effective(store, fallback):
    with store.connect() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='managed_config'").fetchone()
    if not exists:return fallback,None
    try:state=get(store)
    except ValueError:return fallback,None
    return state['config'],state['revision']


def cache_candles(store, asset, timeframe, candles):
    feed=f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
    with store.connect() as db:
        initialize(db)
        db.executemany('INSERT INTO candle_cache VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(feed,timeframe,start) DO UPDATE SET end=excluded.end,open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close',
            ((feed,asset['id'],timeframe,c.start,c.end,c.open,c.high,c.low,c.close) for c in candles))
