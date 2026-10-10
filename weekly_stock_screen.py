"""Daily rotating weekly BB-touch screen across S&P 400/500 constituents."""
import json
from datetime import datetime, time as wall_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from platform_app import ROOT

POLICY = dict(enabled=True, timezone='Asia/Jerusalem', hour=8, count=10,
              lookback_weeks=5,signal_lookback_weeks=0,
              source='S&P 400 and S&P 500 constituents; Alpaca split-adjusted weekly candles')


def local_day(policy, now):
    local = datetime.fromtimestamp(now / 1000, ZoneInfo(policy['timezone']))
    boundary = local.replace(hour=policy['hour'], minute=0, second=0, microsecond=0)
    if local < boundary:
        boundary -= timedelta(days=1)
    from market_calendar import stock_trading_day
    while not stock_trading_day(boundary.date()):
        boundary-=timedelta(days=1)
    return boundary.date().isoformat(), int(boundary.timestamp() * 1000)


def week_start(policy, day):
    local = datetime.combine(datetime.fromisoformat(day).date(), wall_time.min, ZoneInfo(policy['timezone']))
    start = local - timedelta(days=local.weekday())
    return int(start.timestamp() * 1000), start.date().isoformat()


def universe():
    data=json.loads((ROOT/'stock_universe.json').read_text(encoding='utf-8'))
    return data['indexes']['sp400'], data['indexes']['sp500']


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS weekly_stock_screen(day TEXT PRIMARY KEY, week TEXT NOT NULL, payload TEXT NOT NULL)')


def status(store):
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT payload FROM weekly_stock_screen ORDER BY day DESC LIMIT 1').fetchone()
    return json.loads(row[0]) if row else None


def _market_snapshot(symbols, now):
    from alpaca_feed import fetch_alpaca_multi
    return fetch_alpaca_multi(symbols, 'weekly', end=now)


def resolve(store, config, rules, now):
    policy=config.get('weekly_stock_screen', {})
    if not policy.get('enabled'):
        return config, None
    from stock_policy import scan_day,MIN_PRICE
    if not scan_day(now):
        return config,dict(status='weekday only',reason='Stock screening paused Saturday/Sunday')
    day, starts_at=local_day(policy, now)
    week_start_at, week=week_start(policy, day)
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT payload FROM weekly_stock_screen WHERE day=?',(day,)).fetchone()
        prior=db.execute('SELECT payload FROM weekly_stock_screen WHERE week=? AND day<? ORDER BY day',(week,day)).fetchall()
    selection=json.loads(row[0]) if row else None
    permanent={a.get('symbol',a['id']).upper() for a in config['assets'] if a.get('market') not in ('forex','crypto','futures')}
    policy_stamp={key:policy.get(key) for key in ('count','lookback_weeks','source')}
    policy_stamp['excluded_portfolio']=sorted(permanent)
    policy_stamp['min_stock_price']=MIN_PRICE
    if selection and selection.get('policy')!=policy_stamp:
        selection=None
    if selection:
        signal_since=week_start_at-policy.get('signal_lookback_weeks',0)*7*86400000
        if selection.get('signal_lookback_start')!=signal_since:
            selection['signal_lookback_start']=signal_since
            with store.connect() as db:
                db.execute('UPDATE weekly_stock_screen SET payload=? WHERE day=?',(json.dumps(selection),day))
    if selection is None or selection['status']=='unavailable' and now-selection['checked_at']>=3600000:
        from scanner import indicators
        sp400,sp500=universe()
        pool=sorted(set(sp400)|set(sp500))
        seen=set(permanent)
        for saved, in prior:
            old=json.loads(saved)
            seen.update(old.get('symbols', []))
        selection=dict(day=day,week=week,week_starts_at=week_start_at,starts_at=starts_at,
                       checked_at=now,source=policy.get('source',POLICY['source']),
                       policy=policy_stamp,
                       candidates={'upper':[],'lower':[]},selected={'upper':[],'lower':[]},
                       counts={'upper':0,'lower':0})
        try:
            histories=cached_snapshot(store,pool,now)
            upper=[];lower=[]
            for symbol,candles in histories.items():
                if symbol in seen or len(candles)<rules['bb_period'] or candles[-1].close<MIN_PRICE:
                    continue
                _,lo,up=indicators(candles,rules)
                lookback=min(policy.get('lookback_weeks',5),len(candles))
                for i in range(len(candles)-lookback,len(candles)):
                    candle=candles[i];low_band,upper_band=lo[i],up[i]
                    if low_band is None or upper_band is None or candle.close<MIN_PRICE:
                        continue
                    if candle.high>=upper_band:
                        upper.append(dict(symbol=symbol,close=candle.close,band=upper_band,
                                          distance_pct=abs(candle.close-upper_band)/candle.close*100,
                                          candle_at=candle.start))
                    if candle.low<=low_band:
                        lower.append(dict(symbol=symbol,close=candle.close,band=low_band,
                                          distance_pct=abs(candle.close-low_band)/candle.close*100,
                                          candle_at=candle.start))
            def unique_recent(rows):
                latest={}
                for item in rows:
                    previous=latest.get(item['symbol'])
                    if previous is None or (item['candle_at'],-item['distance_pct'])>(previous['candle_at'],-previous['distance_pct']):
                        latest[item['symbol']]=item
                return sorted(latest.values(),key=lambda row:(-row['candle_at'],row['distance_pct'],row['symbol']))
            upper=unique_recent(upper);lower=unique_recent(lower)
            count=policy.get('count',10)
            selected_upper=upper[:count]
            used={item['symbol'] for item in selected_upper}
            selected_lower=[item for item in lower if item['symbol'] not in used][:count]
            selection.update(status='selected' if len(selected_upper)==count and len(selected_lower)==count else 'partial',
                             candidates={'upper':upper,'lower':lower},
                             selected={'upper':selected_upper,'lower':selected_lower},
                             counts={'upper':len(upper),'lower':len(lower)},
                             symbols=sorted(used|{item['symbol'] for item in selected_lower}),
                             weekly_candle_at=max((item['candle_at'] for item in selected_upper+selected_lower),default=None),
                             signal_lookback_start=week_start_at-policy.get('signal_lookback_weeks',0)*7*86400000)
            if selection['status']=='partial':
                selection['reason']=f"Only {len(selected_upper)} upper and {len(selected_lower)} lower band touches available after this week's earlier picks; no non-touching stocks were substituted."
        except Exception:
            selection.update(status='unavailable',reason='Weekly stock screen unavailable; check Alpaca credentials, feed entitlement, and symbol coverage.')
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO weekly_stock_screen VALUES(?,?,?)',(day,week,json.dumps(selection)))
        store.log('Weekly stock screen',json.dumps({k:v for k,v in selection.items() if k!='candidates'}))

    result=dict(config)
    result['assets']=list(config['assets'])
    for side in ('upper','lower'):
        for row in selection.get('selected',{}).get(side,[]):
            symbol=row['symbol']
            result['assets'].append(dict(id='STOCK-'+symbol,provider='alpaca',symbol=symbol,
                exchange='Alpaca-sip-split',market='stock',feed_confirmed=True,enabled=True,
                timeframes=['weekly'],tradingview_symbol='',weekly_screen_selected=True,
                weekly_screen_week_start=week_start_at,
                weekly_screen_since=0,weekly_screen_side=side))
    return result,selection


def seconds_to_refresh(policy, now):
    local=datetime.fromtimestamp(now,ZoneInfo(policy['timezone']))
    boundary=local.replace(hour=policy['hour'],minute=0,second=0,microsecond=0)
    if local>=boundary: boundary+=timedelta(days=1)
    from market_calendar import stock_trading_day
    while not stock_trading_day(boundary.date()): boundary+=timedelta(days=1)
    return max(1,boundary.timestamp()-now)


def install(store):
    """Add the screen policy and remove the obsolete DAX ETF from managed assets."""
    import time
    from cloud_sync import enqueue
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if not row:
            raise ValueError('Asset configuration has not been initialized')
        revision,applied,payload=row
        config=json.loads(payload)
        before=len(config['assets'])
        config['assets']=[asset for asset in config['assets'] if asset['id']!='DAX-ETF']
        changed=len(config['assets'])!=before or config.get('weekly_stock_screen')!=POLICY
        if changed:
            config['weekly_stock_screen']=dict(POLICY)
            revision+=1
            payload=json.dumps(config)
            db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1',(revision,payload))
            db.execute('INSERT INTO config_history VALUES(?,?,?)',(revision,payload,time.time()))
            enqueue(db,'config','current',dict(revision=revision,applied_revision=applied,config=config))
    return dict(revision=revision,changed=changed,removed=before-len(config['assets']))


def cached_snapshot(store,symbols,now):
    """Daily ranking reuses the same completed weekly OHLC across restarts."""
    from datetime import timezone
    from dataclasses import asdict
    import os
    from scanner import Candle
    from market_calendar import NY,previous_session,stock_close
    local=datetime.fromtimestamp(now/1000,NY)
    friday=local.date()-timedelta(days=(local.weekday()-4)%7)
    end=stock_close(previous_session(friday))+timedelta(minutes=16)
    if end.timestamp()*1000>now:friday-=timedelta(days=7)
    key=friday.isoformat()+':'+os.environ.get('ALPACA_DATA_FEED','sip')
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS stock_screen_history(key TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        row=db.execute('SELECT payload FROM stock_screen_history WHERE key=?',(key,)).fetchone()
    if row:
        return {symbol:[Candle(**c) for c in candles] for symbol,candles in json.loads(row[0]).items()}
    histories=_market_snapshot(symbols,now)
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO stock_screen_history VALUES(?,?)',(key,json.dumps({symbol:[asdict(c) for c in bars] for symbol,bars in histories.items()})))
    return histories
