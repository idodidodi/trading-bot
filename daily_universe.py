"""Morning asset selection, ranked by venue-specific 24-hour USD turnover."""
import copy
from datetime import datetime, timedelta
import json
import math
import time
import urllib.request
from zoneinfo import ZoneInfo

FIAT = {'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD', 'CNY', 'CNH'}
STABLE = {'USDT', 'USDC', 'DAI', 'PYUSD', 'USDD', 'TUSD', 'FDUSD', 'USDG', 'USDE', 'EURC', 'EURT'}
CORE = {'forex': ['EUR/USD', 'USD/JPY', 'USD/CNY', 'GBP/USD', 'USD/CHF'],
        'crypto': ['BTC/USD', 'ETH/USD', 'SOL/USD', 'NEAR/USD', 'DOGE/USD']}
POLICY = dict(enabled=True, timezone='Asia/Jerusalem', hour=8, count=5,
              source='Kraken 24-hour USD turnover; Twelve Data candle feeds')


def asset(symbol, market, frames, crypto_native_provider='twelvedata', forex_provider='twelvedata'):
    result = dict(id=symbol.replace('/', ''), symbol=symbol, provider='twelvedata',
                exchange='Binance' if market == 'crypto' else '', feed_confirmed=True,
                enabled=True, timeframes=list(frames), tradingview_symbol='', market=market)
    if market == 'crypto' and crypto_native_provider == 'kraken':
        result['timeframe_providers'] = {f: 'kraken' for f in frames if f in ('1h', '4h', 'daily', 'weekly')}
    if market == 'forex' and forex_provider == 'oanda':
        result.update(provider='oanda', exchange='OANDA')
    if market == 'forex' and forex_provider == 'tiingo':
        result.update(provider='tiingo', exchange='Tiingo')
    return result


def configure(config):
    """Explicit installation operation; preserve unrelated assets and custom fields."""
    config = copy.deepcopy(config)
    by_id = {a['id']: a for a in config['assets']}
    for market, symbols in CORE.items():
        for symbol in symbols:
            new = asset(symbol, market, config['timeframes'])
            if new['id'] in by_id:
                by_id[new['id']].update(market=market, enabled=True)
            else:
                config['assets'].append(new)
    config['daily_universe'] = dict(POLICY)
    # 23 assets x 4 frames x 6 cycles = 552 requests/day, below the 800/day budget.
    config['poll_seconds'] = max(config.get('poll_seconds', 0), 14400)
    return config


def session(policy, now):
    local = datetime.fromtimestamp(now / 1000, ZoneInfo(policy['timezone']))
    boundary = local.replace(hour=policy['hour'], minute=0, second=0, microsecond=0)
    if local < boundary:
        boundary -= timedelta(days=1)
    return boundary.date().isoformat(), int(boundary.timestamp() * 1000)


def seconds_to_refresh(policy, now):
    local = datetime.fromtimestamp(now, ZoneInfo(policy['timezone']))
    boundary = local.replace(hour=policy['hour'], minute=0, second=0, microsecond=0)
    if local >= boundary:
        boundary += timedelta(days=1)
    return max(1, boundary.timestamp() - now)


def read_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        result = json.load(response)
    if result.get('error') or result.get('status') == 'error':
        raise ValueError('Ranking provider rejected the request')
    return result


def ranking_inputs(forex_provider='twelvedata'):
    pairs = read_json('https://api.kraken.com/0/public/AssetPairs')['result']
    tickers = read_json('https://api.kraken.com/0/public/Ticker')['result']
    forex = read_json('https://api.twelvedata.com/forex_pairs')['data']
    crypto = read_json('https://api.twelvedata.com/cryptocurrencies')['data']
    supported = {'forex': {r['symbol'] for r in forex},
                 'crypto': {r['symbol'] for r in crypto if 'Binance' in r.get('available_exchanges', [])}}
    if forex_provider == 'oanda':
        from oanda_feed import available_forex
        supported['forex'] &= available_forex()
    return pairs, tickers, supported


def rank(pairs, tickers, supported, excluded, count):
    records = {}
    for key, pair in pairs.items():
        if pair.get('status') != 'online' or key not in tickers:
            continue
        symbol = pair.get('wsname', '')
        if '/' not in symbol:
            continue
        base, quote = symbol.split('/')
        base = {'XBT': 'BTC', 'XDG': 'DOGE'}.get(base, base)
        try:
            ticker = tickers[key]
            volume, vwap, price = float(ticker['v'][1]), float(ticker['p'][1]), float(ticker['c'][0])
            if not all(math.isfinite(v) and v > 0 for v in (volume, vwap, price)):
                continue
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        records[f'{base}/{quote}'] = (volume * vwap, price)
    rates = {'USD': 1.0}
    for currency in FIAT - {'USD'}:
        if f'{currency}/USD' in records:
            rates[currency] = records[f'{currency}/USD'][1]
        elif f'USD/{currency}' in records:
            rates[currency] = 1 / records[f'USD/{currency}'][1]
    ranked = {'forex': [], 'crypto': []}
    for symbol, (turnover, _) in records.items():
        base, quote = symbol.split('/')
        market = 'forex' if base in FIAT and quote in FIAT else 'crypto'
        if market == 'crypto' and (quote != 'USD' or base in FIAT | STABLE):
            continue
        if symbol not in supported[market] or symbol.replace('/', '') in excluded or quote not in rates:
            continue
        usd = turnover * rates[quote]
        if math.isfinite(usd):
            ranked[market].append(dict(symbol=symbol, volume_usd=usd))
    return {market: sorted(rows, key=lambda r: (-r['volume_usd'], r['symbol']))[:count]
            for market, rows in ranked.items()}


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS daily_universe(session TEXT PRIMARY KEY, payload TEXT NOT NULL)')


def status(store):
    with store.connect() as db:
        initialize(db)
        row = db.execute('SELECT payload FROM daily_universe ORDER BY session DESC LIMIT 1').fetchone()
    return json.loads(row[0]) if row else None


def resolve(store, config, now):
    policy = config.get('daily_universe', {})
    if not policy.get('enabled'):
        return config, None
    day, boundary = session(policy, now)
    with store.connect() as db:
        initialize(db)
        row = db.execute('SELECT payload FROM daily_universe WHERE session=?', (day,)).fetchone()
    selection = json.loads(row[0]) if row else None
    if selection is None or (selection['status'] == 'unavailable' and now - selection['checked_at'] >= 3600000):
        selection = dict(session=day, starts_at=boundary, checked_at=now, source=policy['source'],
                         selected={'forex': [], 'crypto': []})
        try:
            inputs = ranking_inputs('oanda') if policy.get('forex_provider') == 'oanda' else ranking_inputs()
            ranked = rank(*inputs, {a['id'] for a in config['assets']}, policy['count'])
            # Never silently claim a full top-five result from inadequate data.
            if any(len(rows) != policy['count'] for rows in ranked.values()):
                raise ValueError('Fewer than five eligible pairs with valid volume in a market')
            selection.update(status='selected', selected=ranked)
        except Exception:
            selection.update(status='unavailable', reason='Daily volume selection unavailable; retry next scan. Permanent assets remain active.')
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO daily_universe VALUES(?,?)', (day, json.dumps(selection)))
        store.log('Daily asset selection', json.dumps(selection))
    result = copy.deepcopy(config)
    known = {a['id'] for a in result['assets']}
    for market, rows in selection['selected'].items():
        for row in rows:
            forex_provider = policy.get('forex_provider', 'twelvedata')
            # A frozen selection predating activation may contain unsupported pairs.
            if forex_provider in ('oanda','tiingo') and row['symbol'] not in policy.get(forex_provider + '_verified_pairs', []):
                forex_provider = 'twelvedata'
            new = asset(row['symbol'], market, config['timeframes'], policy.get('crypto_native_provider', 'twelvedata'), forex_provider)
            if new['id'] not in known:
                new['daily_selected'] = True
                result['assets'].append(new)
                known.add(new['id'])
    return result, selection


def install(store):
    """Versioned migration for this installation; do not run on every startup."""
    from cloud_sync import enqueue
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revision, applied, payload = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        config = configure(json.loads(payload))
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1', (revision, payload))
        db.execute('INSERT INTO config_history VALUES(?,?,?)', (revision, payload, time.time()))
        enqueue(db, 'config', 'current', dict(revision=revision, applied_revision=applied, config=config))
    return config
