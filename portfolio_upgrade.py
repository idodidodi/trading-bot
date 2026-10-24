"""Explicit, idempotent portfolio migration; never overwrites existing feeds."""
import copy
import json
import time
from managed_assets import DEFAULTS, validate_asset

STOCKS=[('MRVL','NASDAQ','Marvell Technology','stock'),('NASA','AMEX','Tema Space Innovators ETF','etf'),
        ('QCOM','NASDAQ','Qualcomm','stock'),('META','NASDAQ','Meta Platforms','stock'),
        ('AMD','NASDAQ','Advanced Micro Devices','stock'),('CRCL','NYSE','Circle Internet Group','stock'),
        ('EROC','NYSE','ERock, Inc.','stock')]


def upgraded(config):
    result=copy.deepcopy(config)
    assets=result['assets'];known={a['id'] for a in assets}
    for symbol,exchange,name,market in STOCKS:
        if symbol not in known:
            assets.append(dict(id=symbol,symbol=symbol,name=name,market=market,provider='alpaca',exchange=exchange,
                enabled=True,feed_confirmed=True,timeframes=DEFAULTS.copy(),tradingview_symbol=exchange+':'+symbol))
    from daily_universe import asset as pair
    for symbol,market in [('GBP/JPY','forex'),('USD/JPY','forex'),('EUR/USD','forex'),('NEAR/USD','crypto'),('DOGE/USD','crypto'),('BTC/USD','crypto')]:
        if symbol.replace('/','') not in known:
            assets.append(pair(symbol,market,DEFAULTS,'kraken'))
    for symbol,name in [('CL','WTI crude oil futures'),('GC','Gold futures'),('ES','E-mini S&P 500 futures')]:
        if symbol not in known:
            assets.append(dict(id=symbol,name=name,market='futures',provider='csv',enabled=False,timeframes=DEFAULTS.copy(),
                path='data/candles/{asset}-{timeframe}.csv',tradingview_symbol='',
                unavailable_reason='Exact futures provider, continuous-contract roll convention and feed access await configuration. No spot/CFD substitution.'))
    for a in assets:
        a.setdefault('enabled',True)
        a.setdefault('timeframes',DEFAULTS.copy())
        a['portfolio']=True
        if a['id'].replace('/','').upper()=='USDCNY':
            a['enabled']=False
        if a['id']=='NVDA':a.setdefault('market','stock')
        validate_asset(a)
    result.setdefault('excluded_assets',[])
    if 'USDCNY' not in result['excluded_assets']:result['excluded_assets'].append('USDCNY')
    return result


def install(store):
    with store.connect() as db:
        db.require_table('app_migrations')
        done=db.execute("SELECT 1 FROM app_migrations WHERE name='family-2026-10-09'").fetchone()
        revision=db.execute('SELECT revision FROM managed_config WHERE id=1').fetchone()[0]
    if done:return dict(revision=revision,changed=False)
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        revision,applied,raw=db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        config=upgraded(json.loads(raw))
        changed=config!=json.loads(raw)
        if changed:
            revision+=1;raw=json.dumps(config)
            db.execute('UPDATE managed_config SET revision=%s,payload=%s WHERE id=1',(revision,raw))
            db.execute('INSERT INTO config_history VALUES(%s,%s,%s)',(revision,raw,time.time()))
        db.execute("INSERT INTO app_migrations VALUES('family-2026-10-09',%s) ON CONFLICT DO NOTHING",(time.time(),))
    return dict(revision=revision,changed=changed)
