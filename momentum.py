"""Closed daily-candle momentum candidates and conservative next-open simulation."""
import hashlib
import json
import math
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

VERSION = 'momentum-v1'
RULES = dict(fast=20, slow=50, breakout=20, roc=10, min_roc_pct=2,
             min_efficiency=.35, max_extension_atr=2, entry_tolerance_atr=.5,
             stop_atr=1.5, target_r=2, max_hold=10, cost_bps=10)
MIN_HISTORY = 80
from stock_policy import MIN_PRICE, scan_day
RULES['min_stock_price'] = MIN_PRICE
VERSION += '-' + hashlib.sha256(json.dumps(RULES,sort_keys=True).encode()).hexdigest()[:8]


def ema(values, period):
    result=[]; value=values[0]
    for price in values:
        value += 2/(period+1)*(price-value); result.append(value)
    return result


def features(bars):
    closes=[b.close for b in bars]
    fast=ema(closes,RULES['fast']); slow=ema(closes,RULES['slow'])
    tr=[b.high-b.low if not i else max(b.high-b.low,abs(b.high-bars[i-1].close),abs(b.low-bars[i-1].close)) for i,b in enumerate(bars)]
    atr=[]; value=sum(tr[:14])/14 if len(tr)>=14 else 0
    for i,t in enumerate(tr):
        if i>=14:value=(value*13+t)/14
        atr.append(value if i>=13 else None)
    return fast,slow,atr


def candidate(bars, symbol, market, index=None, indicators=None):
    i=len(bars)-1 if index is None else index
    if i<MIN_HISTORY-1:return None
    fast,slow,atr=indicators or features(bars)
    b,previous=bars[i],bars[i-1]; a=atr[i]
    if market=='stock' and b.close<MIN_PRICE:return None
    if not a or a<=0:return None
    roc=(b.close/bars[i-10].close-1)*100
    travel=sum(abs(bars[k].close-bars[k-1].close) for k in range(i-9,i+1))
    efficiency=abs(b.close-bars[i-10].close)/travel if travel else 0
    if efficiency<RULES['min_efficiency']:return None
    # Breakout yesterday; today's independent close must hold beyond the old range.
    window=bars[i-21:i-1]
    for direction in (1,-1):
        level=max(c.high for c in window) if direction==1 else min(c.low for c in window)
        if not (direction*(previous.close-level)>0 and direction*(b.close-level)>0
                and direction*(b.close-b.open)>0
                and direction*(fast[i]-slow[i])>0
                and direction*(slow[i]-slow[i-5])>0
                and direction*roc>=RULES['min_roc_pct']):continue
        extension=direction*(b.close-fast[i])/a
        if not 0<=extension<=RULES['max_extension_atr']:continue
        # Yesterday must be the first break, preventing repeated continuation alerts.
        if direction*(bars[i-2].close-level)>0:continue
        stop=b.close-direction*RULES['stop_atr']*a
        target=b.close+direction*RULES['stop_atr']*a*RULES['target_r']
        if min(stop,target)<=0:continue
        return dict(strategy='momentum',version=VERSION,symbol=symbol,market=market,timeframe='daily',
                    direction='buy' if direction==1 else 'sell',confirmed_at=b.end,
                    entry=b.close,entry_min=b.close-.5*a,entry_max=b.close+.5*a,
                    stop=stop,target=target,atr=a,breakout_level=level,roc_pct=roc,
                    efficiency=efficiency,extension_atr=extension,
                    score=round(abs(roc)/(a/b.close*100)*efficiency,6),rules=dict(RULES),
                    explanation='Breakout held for two closed daily candles; EMA trend, directional return and efficiency confirmed. Enter only within the entry range; expire after the next session. Sell means a short candidate, requiring an eligible account/instrument.')
    return None


def outcome(signal, bars):
    rules=signal.get('rules',RULES)
    def opened_at(bar):
        if signal['market']!='stock':return bar.start
        local=datetime.fromtimestamp(bar.start/1000,ZoneInfo('America/New_York'))
        return int(local.replace(hour=9,minute=30,second=0,microsecond=0).timestamp()*1000)
    baseline=max(signal['confirmed_at'],signal.get('published_at',0))
    following=[b for b in bars if opened_at(b)>=baseline]
    if not following:return dict(status='awaiting entry')
    first=following[0];entry=first.open
    if signal.get('entry_open_at') and opened_at(first)!=signal['entry_open_at']:
        return dict(status='unavailable',reason='Entry-session candle missing; no later entry substituted')
    if opened_at(first)-baseline>(5 if signal['market']=='stock' else 2)*86400000:
        return dict(status='unavailable',reason='Entry-session history missing; no later entry substituted')
    if not signal['entry_min']<=entry<=signal['entry_max']:
        return dict(status='skipped gap',entry_at=opened_at(first))
    d=1 if signal['direction']=='buy' else -1
    # Preserve quoted stop/target; next-open entry must still offer at least 1.5R.
    risk=d*(entry-signal['stop']);reward=d*(signal['target']-entry)
    if risk<=0 or reward/risk<1.5:return dict(status='skipped reward/risk')
    adverse=favorable=0
    for n,b in enumerate(following[:rules['max_hold']],1):
        adverse=max(adverse,max(0,d*(entry-(b.low if d==1 else b.high)))/entry*100)
        favorable=max(favorable,max(0,d*((b.high if d==1 else b.low)-entry))/entry*100)
        # Stop first if both levels are touched; opening gaps execute at the open.
        stop_hit=b.low<=signal['stop'] if d==1 else b.high>=signal['stop']
        target_hit=b.high>=signal['target'] if d==1 else b.low<=signal['target']
        if stop_hit:
            exit_price=min(b.open,signal['stop']) if d==1 else max(b.open,signal['stop']);status='stop'
        elif target_hit:exit_price=signal['target'];status='target'
        elif n==rules['max_hold']:exit_price=b.close;status='time exit'
        else:continue
        return dict(status=status,entry_at=opened_at(first),entry_price=entry,exit_at=b.end,
                    exit_price=exit_price,net_pct=d*(exit_price/entry-1)*100-2*rules['cost_bps']/100,
                    adverse_pct=adverse,favorable_pct=favorable)
    return dict(status='open',entry_at=opened_at(first),entry_price=entry,adverse_pct=adverse,favorable_pct=favorable)


def replay(histories):
    # Rank at each close across available symbols, using no future bars for selection.
    by_close={}
    for (market,symbol),bars in histories.items():
        values=features(bars) if bars else None
        for i in range(MIN_HISTORY-1,len(bars)):
            s=candidate(bars,symbol,market,i,values)
            if s:by_close.setdefault((market,s['confirmed_at']),[]).append(s)
    selected=[]
    for key,candidates in sorted(by_close.items()):
        s=sorted(candidates,key=lambda s:(-s['score'],s['symbol']))[0]
        selected.append(s|dict(outcome=outcome(s,histories[(s['market'],s['symbol'])])))
    closed=[s['outcome']['net_pct'] for s in selected if 'net_pct' in s['outcome']]
    return dict(strategy='momentum',version=VERSION,generated_at=int(time.time()*1000),signals=selected[-500:],
                metrics=dict(candidates=len(selected),closed_trades=len(closed),wins=sum(x>0 for x in closed),
                             win_rate_pct=sum(x>0 for x in closed)/len(closed)*100 if closed else None,
                             average_net_pct=sum(closed)/len(closed) if closed else None),
                note='Daily cross-sectional ranking; next-session open entries; gaps outside entry range skipped; stop first when both touched; 10 sessions maximum; 10 bps per side. Independent trade returns, not portfolio returns. Current stock universe and current crypto selection introduce survivorship/selection bias. No short borrow, funding or liquidity model. Recent replay is diagnostic, not an out-of-sample validation.')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS momentum_runs(day TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS momentum_history(market TEXT,symbol TEXT,payload TEXT NOT NULL,PRIMARY KEY(market,symbol))')
    db.execute('CREATE TABLE IF NOT EXISTS momentum_reports(id INTEGER PRIMARY KEY,created INTEGER,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS momentum_feeds(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL)')


def session(now):
    local=datetime.fromtimestamp(now/1000,ZoneInfo('Asia/Jerusalem'))
    boundary=local.replace(hour=8,minute=0,second=0,microsecond=0)
    if local<boundary:boundary-=timedelta(days=1)
    return boundary.date().isoformat()


def entry_session(market, now):
    zone=ZoneInfo('America/New_York' if market=='stock' else 'UTC')
    local=datetime.fromtimestamp(now/1000,zone)
    opening=local.replace(hour=9 if market=='stock' else 0,minute=30 if market=='stock' else 0,second=0,microsecond=0)
    if opening<local:opening+=timedelta(days=1)
    if market=='stock':
        from market_calendar import stock_trading_day,stock_close
        while not stock_trading_day(opening.date()):opening+=timedelta(days=1)
        closing=stock_close(opening.date())
    else:closing=opening+timedelta(days=1)
    return dict(entry_open_at=int(opening.timestamp()*1000),expires_at=int(closing.timestamp()*1000))


def seconds_to_scan(store):
    now=int(time.time()*1000)
    local=datetime.fromtimestamp(now/1000,ZoneInfo('Asia/Jerusalem'))
    boundary=local.replace(hour=8,minute=0,second=0,microsecond=0)
    if boundary<=local:boundary+=timedelta(days=1)
    delay=(boundary.timestamp()*1000-now)/1000
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT payload FROM momentum_runs WHERE day=?',(session(now),)).fetchone()
    if not row:return max(30,delay)
    saved=json.loads(row[0])
    if saved.get('retryable',saved['status']!='completed'):delay=min(delay,max(30,(saved['scanned_at']+3600000-now)/1000))
    return max(30,delay)


def scan(store, config, now=None, stop=None, force=False):
    from scanner import Candle, check_candles
    now=int(time.time()*1000) if now is None else now;day=session(now)
    with store.connect() as db:
        initialize(db)
        prior=db.execute('SELECT payload FROM momentum_runs WHERE day=?',(day,)).fetchone()
    prior=json.loads(prior[0]) if prior else None
    if prior and not force:
        if not prior.get('retryable',prior['status']!='completed') or now-prior['scanned_at']<3600000:return prior
    histories={};coverage=[]
    stock_enabled=scan_day(now)
    if stock_enabled:
        try:
            from weekly_stock_screen import universe
            from alpaca_feed import fetch_alpaca_multi
            from market_calendar import stock_trading_day, stock_close
            sp400,sp500=universe();symbols=sorted(set(sp400)|set(sp500))
            excluded={a.get('symbol',a['id']) for a in config['assets'] if a.get('enabled') is False}
            symbols=[s for s in symbols if s not in excluded]
            stock_bars=fetch_alpaca_multi(symbols,'daily',end=now,lookback_days=400)
            # Expected last US regular session, including holidays and early closes.
            ny=datetime.fromtimestamp(now/1000,ZoneInfo('America/New_York'));expected=ny.date()
            while not stock_trading_day(expected) or stock_close(expected).timestamp()*1000>now-16*60000:
                expected-=timedelta(days=1)
            for symbol in symbols:
                bars=check_candles(stock_bars.get(symbol,[]),now)
                if len(bars)<MIN_HISTORY or bars[-1].end<int(stock_close(expected).timestamp()*1000):
                    coverage.append(dict(market='stock',symbol=symbol,status='unavailable',reason='Insufficient or stale daily history'));continue
                if bars[-1].close<MIN_PRICE:
                    coverage.append(dict(market='stock',symbol=symbol,status='excluded',reason='Stock below $5 minimum'));continue
                histories[('stock',symbol)]=bars
            coverage.append(dict(market='stock',status='scanned',symbols=len(stock_bars),eligible=sum(k[0]=='stock' for k in histories)))
        except Exception:
            coverage.append(dict(market='stock',status='unavailable',reason='US daily scan failed; check Alpaca credentials and feed entitlement'))
    else:
        coverage.append(dict(market='stock',status='weekday only',reason='Stock scans run Monday–Friday on the Israel schedule'))
    from managed_assets import for_timeframe
    crypto={a.get('symbol',a['id']):a for a in config['assets'] if a.get('enabled',True) and a.get('market')=='crypto'}
    # Keep tracking issued trades after a rotating crypto leaves today's universe.
    with store.connect() as db:
        recent=[json.loads(r[0]) for r in db.execute('SELECT payload FROM momentum_runs ORDER BY day DESC LIMIT 20')]
        issued={s['symbol'] for r in recent for s in r['picks'].values() if s and s['market']=='crypto'}
        monitoring={s:json.loads(raw) for s,raw in db.execute('SELECT * FROM momentum_feeds') if s in issued}
        for symbol,asset in crypto.items():db.execute('INSERT OR REPLACE INTO momentum_feeds VALUES(?,?)',(symbol,json.dumps(asset)))
    for symbol,configured in sorted((monitoring|crypto).items()):
        if stop is not None and stop.is_set():return None
        try:
            asset=for_timeframe(configured,'daily')
            from kraken_feed import fetch_kraken
            from scanner import fetch_twelve_data,read_csv
            fetch={'kraken':fetch_kraken,'twelvedata':fetch_twelve_data,'csv':read_csv}[asset['provider']]
            bars=check_candles(fetch(asset,'daily'),now)
            if len(bars)<MIN_HISTORY or now-bars[-1].end>36*3600000:raise ValueError()
            histories[('crypto',symbol)]=bars
            coverage.append(dict(market='crypto',symbol=symbol,status='scanned',candles=len(bars)))
        except Exception:
            coverage.append(dict(market='crypto',symbol=symbol,status='unavailable',reason='Daily feed unavailable, stale or insufficient history'))
        delay=8 if configured.get('timeframe_providers',{}).get('daily',configured['provider'])=='twelvedata' else 1
        if stop is not None:
            if stop.wait(delay):return None
        else:time.sleep(delay)
    picks={};results=[]
    for market in ('stock','crypto'):
        candidates=[]
        for (m,symbol),bars in histories.items():
            if m!=market:continue
            if m=='crypto' and symbol not in crypto:continue
            s=candidate(bars,symbol,market)
            if s:candidates.append(s)
        candidates.sort(key=lambda s:(-s['score'],s['symbol']))
        pick=candidates[0] if candidates else None
        # Do not publish the same stock confirmation again over weekends/holidays.
        with store.connect() as db:
            old=[json.loads(r[0]).get('picks',{}).get(market) for r in db.execute('SELECT payload FROM momentum_runs WHERE day<? ORDER BY day DESC LIMIT 7',(day,))]
        repeated=pick and any(s and (s['symbol'],s['confirmed_at'],s['version'])==(pick['symbol'],pick['confirmed_at'],pick['version']) for s in old)
        # A retry can fill a missing market but never rewrite an already issued pick.
        issued=prior.get('picks',{}).get(market) if prior else None
        picks[market]=issued or (None if repeated else pick|dict(published_at=now)|entry_session(market,now) if pick else None)
        results.append(dict(market=market,status='signal' if picks[market] else 'already published' if repeated else 'no qualifying setup' if any(k[0]==market for k in histories) else 'weekday only' if market=='stock' and not stock_enabled else 'no eligible stocks' if market=='stock' and any(c['market']=='stock' and c['status']=='scanned' for c in coverage) else 'unavailable',candidates=len(candidates)))
    payload=dict(strategy='momentum',version=VERSION,day=day,scanned_at=now,picks=picks,results=results,coverage=coverage,
                 status='partial' if not crypto or any(c['status']=='unavailable' for c in coverage) else 'completed')
    payload['retryable']=not crypto or any(c['status']=='unavailable' and (c['market']=='crypto' or 'symbol' not in c) for c in coverage)
    replay_histories=dict(histories)
    if not stock_enabled:
        with store.connect() as db:
            for symbol,raw in db.execute("SELECT symbol,payload FROM momentum_history WHERE market='stock'"):
                replay_histories[('stock',symbol)]=[Candle(**b) for b in json.loads(raw)]
    report=replay(replay_histories)
    with store.connect() as db:
        for (market,symbol),bars in histories.items():
            db.execute('INSERT OR REPLACE INTO momentum_history VALUES(?,?,?)',(market,symbol,json.dumps([vars(b) for b in bars])))
        db.execute('INSERT OR REPLACE INTO momentum_runs VALUES(?,?)',(day,json.dumps(payload)))
        db.execute('INSERT INTO momentum_reports(created,payload) VALUES(?,?)',(now,json.dumps(report)))
    store.log('Momentum daily scan',json.dumps(dict(day=day,status=payload['status'],results=results)))
    return payload


def view(store, source='live', limit=30):
    with store.connect() as db:
        initialize(db)
        if source=='backtest':
            row=db.execute('SELECT payload FROM momentum_reports ORDER BY id DESC LIMIT 1').fetchone()
            result=json.loads(row[0]) if row else dict(signals=[],metrics={},note='Awaiting first daily momentum scan')
            result['signals']=result['signals'][-100:]
            result['note']+=' Showing the latest 100 candidates.'
            return result
        query='SELECT payload FROM momentum_runs ORDER BY day DESC'
        rows=[json.loads(r[0]) for r in db.execute(query+' LIMIT ?',(limit,))] if limit else [json.loads(r[0]) for r in db.execute(query)]
        latest=rows[0] if rows else None
        histories={(m,s):json.loads(raw) for m,s,raw in db.execute('SELECT * FROM momentum_history')}
    from scanner import Candle
    signals=[]
    for row in rows:
        for s in row['picks'].values():
            if s:
                if s.get('published_at') and not s.get('entry_open_at'):
                    s=s|entry_session(s['market'],s['published_at'])
                bars=[Candle(**b) for b in histories.get((s['market'],s['symbol']),[])]
                signals.append(s|dict(outcome=outcome(s,bars),published_day=row['day']))
    from paper_trading import view as paper_view, identity as paper_identity
    paper=paper_view(store);executions={r['id']:r for r in paper['trades']}
    for s in signals:s['paper']=executions.get(paper_identity(s))
    return dict(latest=latest,signals=signals,paper=paper,note='At most one new stock and one crypto candidate per daily scan. No qualifying setup and unavailable coverage are reported explicitly. Entry expires after the next session; shown levels are research candidates.')


if __name__=='__main__':
    import argparse
    from platform_app import ROOT,Store,load_env
    from scanner import load_config
    from managed_assets import effective
    parser=argparse.ArgumentParser();parser.add_argument('--scan',action='store_true');args=parser.parse_args()
    load_env();store=Store(os.path.join(os.environ.get('DATA_DIR',str(ROOT/'data')),'signals.sqlite3'))
    if args.scan:
        from daily_universe import resolve
        cfg,_=effective(store,load_config());cfg,_=resolve(store,cfg,int(time.time()*1000))
        scan(store,cfg)
    print(json.dumps(dict(live=view(store),backtest=view(store,'backtest')),indent=2))
