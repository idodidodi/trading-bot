"""Momentum execution restricted to Alpaca's simulated endpoint, with durable order IDs."""
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

PAPER_HOST = 'https://paper-api.alpaca.markets'
DATA_HOST = 'https://data.alpaca.markets'
TERMINAL = {'filled','canceled','expired','rejected'}
ENTRY_WINDOW_MS = 300_000


class BrokerError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f'Alpaca paper API HTTP {status}')


class AlpacaPaper:
    def request(self, path, method='GET', body=None, data=False):
        # No configurable trading host: live endpoint cannot be selected by .env.
        if not path.startswith('/') or '://' in path:
            raise ValueError('Invalid Alpaca API path')
        headers={'APCA-API-KEY-ID':os.environ['ALPACA_API_KEY'],
                 'APCA-API-SECRET-KEY':os.environ['ALPACA_SECRET_KEY']}
        raw=json.dumps(body).encode() if body is not None else None
        if raw is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request((DATA_HOST if data else PAPER_HOST)+path,data=raw,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=15) as response:
                payload=response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            raise BrokerError(exc.code) from None

    def lookup(self, client_id):
        try:return self.request('/v2/orders:by_client_order_id?client_order_id='+client_id)
        except BrokerError as exc:
            if exc.status==404:return None
            raise

    def quote(self, signal):
        symbol=urllib.parse.quote(signal['symbol'],safe='')
        path=('/v2/stocks/quotes/latest?feed=iex&symbols=' if signal['market']=='stock'
              else '/v1beta3/crypto/us/latest/quotes?symbols=')+symbol
        return self.request(path,data=True)['quotes'][signal['symbol']]


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS paper_trades(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS paper_status(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)')


def identity(signal):
    key=[signal[k] for k in ('market','symbol','direction','confirmed_at','version')]
    return 'mom-'+hashlib.sha256(json.dumps(key).encode()).hexdigest()[:28]


def save(store, record):
    with store.connect() as db:
        initialize(db)
        db.execute('INSERT OR REPLACE INTO paper_trades VALUES(?,?)',(record['id'],json.dumps(record)))


def view(store):
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT payload FROM paper_status WHERE id=1').fetchone()
        trades=[json.loads(r[0]) for r in db.execute('SELECT payload FROM paper_trades')]
    for record in trades:
        entry=record.get('order') or {};quantity=float(entry.get('filled_qty') or 0);price=float(entry.get('filled_avg_price') or 0)
        exits=record.get('exits',[])+([record['exit_order']] if record.get('exit_order') else [])
        exited=sum(float(o.get('filled_qty') or 0) for o in exits)
        if quantity and price and exited>=quantity:
            proceeds=sum(float(o.get('filled_qty') or 0)*float(o.get('filled_avg_price') or 0) for o in exits)
            d=1 if record['signal']['direction']=='buy' else -1
            record['gross_pnl']=d*(proceeds-price*exited)
            record['gross_return_pct']=d*(proceeds/exited/price-1)*100
    return dict(enabled=os.environ.get('PAPER_TRADING_ENABLED','false').lower()=='true',
                broker='Alpaca paper',risk_pct=float(os.environ.get('PAPER_RISK_PCT','1')),
                max_notional=float(os.environ.get('PAPER_MAX_NOTIONAL','10000')),
                status=json.loads(row[0]) if row else {},trades=trades)


def money(value):
    return str(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))


def plan(signal, account, asset, quote, now):
    if not asset.get('tradable') or asset.get('status')!='active':raise ValueError('Instrument unavailable on Alpaca paper')
    expected='us_equity' if signal['market']=='stock' else 'crypto'
    if asset.get('class')!=expected:raise ValueError('Instrument class mismatch')
    if signal['direction']=='sell' and (signal['market']=='crypto' or not asset.get('shortable') or not account.get('shorting_enabled')):
        raise ValueError('Short entry unsupported by this paper account/instrument')
    timestamp=int(datetime.fromisoformat(quote['t'].replace('Z','+00:00')).timestamp()*1000)
    if not 0<=now-timestamp<=60_000:raise ValueError('Fresh execution quote unavailable')
    bid,ask=float(quote['bp']),float(quote['ap'])
    from stock_policy import MIN_PRICE
    if signal['market']=='stock' and (min(bid,ask,signal['entry_min'])<MIN_PRICE):
        raise ValueError('Stock below $5 minimum; paper entry excluded')
    if not 0<bid<=ask or (ask/bid-1)>.01:raise ValueError('Invalid or excessive quote spread')
    price=ask if signal['direction']=='buy' else bid
    if not signal['entry_min']<=price<=signal['entry_max']:raise ValueError('Opening quote outside entry range')
    d=1 if signal['direction']=='buy' else -1
    # Tight marketable limit; round to broker precision, then size from worst fill.
    precision=2 if signal['market']=='stock' else 8
    limit=round(min(signal['entry_max'],ask*1.001) if d==1 else max(signal['entry_min'],bid*.999),precision)
    stop=float(money(signal['stop'])) if signal['market']=='stock' else round(signal['stop'],8)
    target=float(money(signal['target'])) if signal['market']=='stock' else round(signal['target'],8)
    risk=d*(limit-stop);reward=d*(target-limit)
    if risk<=0 or reward/risk<1.5:raise ValueError('Reward/risk below 1.5 at execution limit')
    equity=float(account['equity']);available=min(equity,float(account['buying_power']))
    if signal['market']=='crypto':available=min(available,float(account.get('non_marginable_buying_power',available)))
    risk_pct=float(os.environ.get('PAPER_RISK_PCT','1'));cap=float(os.environ.get('PAPER_MAX_NOTIONAL','10000'))
    if not 0<risk_pct<=1 or not 0<cap<=10000:raise ValueError('Invalid paper sizing; maximum 1% risk and $10,000')
    quantity=min(equity*risk_pct/100/risk,cap/signal['entry_max'],available*.95/signal['entry_max'])
    if signal['market']=='stock':quantity=math.floor(quantity)
    else:
        step=Decimal(str(asset['min_trade_increment']))
        quantity=float((Decimal(str(quantity))/step).to_integral_value(rounding=ROUND_DOWN)*step)
        if quantity<float(asset['min_order_size']):raise ValueError('Below crypto minimum order size')
    if quantity<=0:raise ValueError('Insufficient paper buying power')
    order=dict(symbol=signal['symbol'],side=signal['direction'],type='limit',qty=str(quantity),
               limit_price=str(limit),time_in_force='gtc',client_order_id=identity(signal))
    if signal['market']=='stock':
        order.update(order_class='bracket',take_profit=dict(limit_price=money(target)),stop_loss=dict(stop_price=money(stop)))
    return order,dict(quantity=quantity,limit=limit,stop=stop,target=target,risk_dollars=quantity*risk,
                      notional=quantity*limit,risk_pct=risk_pct,max_notional=cap)


def time_exit(signal, now):
    days=signal.get('rules',{}).get('max_hold',10)
    opening=datetime.fromtimestamp(signal['entry_open_at']/1000,ZoneInfo('America/New_York' if signal['market']=='stock' else 'UTC'))
    if signal['market']=='crypto':return now>=int((opening+timedelta(days=days)).timestamp()*1000)
    from market_calendar import stock_trading_day,stock_close
    date=opening.date();count=1
    while count<days:
        date+=timedelta(days=1)
        if stock_trading_day(date):count+=1
    return now>=int(stock_close(date).timestamp()*1000)-30_000


def order_summary(order):
    return {k:order.get(k) for k in ('id','client_order_id','status','symbol','side','qty','filled_qty','filled_avg_price','filled_at','submitted_at')}


def cancel_wait(broker, order):
    if order['status'] in TERMINAL:return order
    broker.request('/v2/orders/'+order['id'],'DELETE')
    # Reconcile in a later cycle; cancellation acknowledgment is not final cancellation.
    return broker.request('/v2/orders/'+order['id'])


def reconcile(store, broker, record, now):
    signal=record['signal'];order=broker.lookup(record['id'])
    if order is None:
        # Ambiguous POST never retried automatically. Avoid duplicates after outages/restarts.
        if record['state'] in {'submitting','entry pending','submission unknown'}:
            record['state']='submission unknown';record['reason']='Order lookup absent after submission; operator review required'
        save(store,record);return
    order=broker.request('/v2/orders/'+order['id']+'?nested=true')
    record['order']=order_summary(order)
    filled=float(order.get('filled_qty') or 0)
    cutoff=min(signal['entry_open_at']+ENTRY_WINDOW_MS,signal['expires_at'])
    if order['status'] not in TERMINAL and now>=cutoff:
        order=cancel_wait(broker,order);record['order']=order_summary(order)
        filled=float(order.get('filled_qty') or 0)
    if filled and order['status'] not in TERMINAL:
        order=cancel_wait(broker,order);record['order']=order_summary(order)
        filled=float(order.get('filled_qty') or 0)
    if not filled:
        record['state']='closed unfilled' if order['status'] in TERMINAL else 'entry pending'
        save(store,record);return
    if order['status'] not in TERMINAL:
        record['state']='entry pending';save(store,record);return
    record['state']='open'
    # Account positions only confirm our recorded quantity; never liquidate unrelated positions.
    positions=broker.request('/v2/positions')
    position=next((p for p in positions if p['symbol'].replace('/','')==signal['symbol'].replace('/','')),None)
    if position and float(position['qty'])*(1 if signal['direction']=='buy' else -1)<0:
        raise ValueError('Paper position direction changed; operator review required')
    remaining=min(filled,abs(float(position['qty']))) if position else 0
    legs=order.get('legs') or []
    if signal['market']=='stock':
        record['protective_orders']=[order_summary(leg) for leg in legs]
        record['exits']=[order_summary(leg) for leg in legs if float(leg.get('filled_qty') or 0)>0]
        if sum(float(leg.get('filled_qty') or 0) for leg in legs)>=filled:
            record['state']='closed';record['exit_reason']='broker stop/target'
            save(store,record);return
    exit_id=record['id']+'-exit';exit_order=broker.lookup(exit_id)
    if exit_order:
        record['exit_order']=order_summary(exit_order)
        if exit_order['status']=='filled':record['state']='closed'
        elif exit_order['status'] in TERMINAL:record['state']='exit failed';record['reason']='Exit order rejected/canceled; operator review required'
        else:record['state']='exit pending'
        save(store,record);return
    if record.get('exit_submitting'):
        record['state']='exit unknown';record['reason']='Exit submission ambiguous; operator review required';save(store,record);return
    exit_reason='time exit' if time_exit(signal,now) else None
    if signal['market']=='crypto':
        stop_id=record['id']+'-stop';protect=broker.lookup(stop_id)
        if protect:
            record['protective_orders']=[order_summary(protect)]
            record['exits']=[order_summary(protect)] if float(protect.get('filled_qty') or 0)>0 else []
            if protect['status']=='filled':
                record['state']='closed';record['exit_reason']='crypto stop';record['exits']=[order_summary(protect)];save(store,record);return
        if remaining<=0:
            record['state']='closed';record['exit_reason']='position no longer present';save(store,record);return
        if not exit_reason and not protect and not record.get('stop_submitting'):
            record['stop_submitting']=True;save(store,record)
            protect=broker.request('/v2/orders','POST',dict(symbol=signal['symbol'],side='sell',type='stop_limit',qty=str(remaining),time_in_force='gtc',
                                  stop_price=str(record['sizing']['stop']),limit_price=str(round(record['sizing']['stop']*.99,8)),client_order_id=stop_id))
            record['protective_orders']=[order_summary(protect)];save(store,record);return
        if not exit_reason and (not protect or protect['status'] in {'rejected','canceled','expired'}):
            exit_reason='protective order unavailable'
        if not exit_reason:
            quote=broker.quote(signal)
            quote_time=int(datetime.fromisoformat(quote['t'].replace('Z','+00:00')).timestamp()*1000)
            if not 0<=now-quote_time<=60_000:raise ValueError('Stale quote; crypto exit check unavailable')
            bid=float(quote['bp'])
            if bid<=record['sizing']['stop']:exit_reason='crypto stop fallback'
            elif bid>=record['sizing']['target']:exit_reason='crypto target'
        legs=[protect] if protect else []
    # Stock bracket failure/partial entry cancellation must not leave an unprotected position.
    elif not legs or any(leg['status'] in {'canceled','expired','rejected'} for leg in legs):
        exit_reason='protective bracket unavailable'
    if remaining<=0:
        record['state']='closed';record['exit_reason']='position no longer present';save(store,record);return
    if exit_reason:
        if signal['market']=='stock' and not broker.request('/v2/clock')['is_open']:
            record['reason']='Exit awaits regular stock session';save(store,record);return
        for leg in legs:
            leg=cancel_wait(broker,leg)
            if leg['status'] not in TERMINAL:
                record['exit_reason']=exit_reason;save(store,record);return
        # Re-read position after cancellations because an exit may have filled in the race.
        positions=broker.request('/v2/positions')
        position=next((p for p in positions if p['symbol'].replace('/','')==signal['symbol'].replace('/','')),None)
        if position and float(position['qty'])*(1 if signal['direction']=='buy' else -1)<0:
            raise ValueError('Paper position direction changed; operator review required')
        remaining=min(filled,abs(float(position['qty']))) if position else 0
        if not remaining:
            record['state']='closed';record['exit_reason']='protective fill during cancellation';save(store,record);return
        record['exit_submitting']=True;record['exit_reason']=exit_reason;save(store,record)
        response=broker.request('/v2/orders','POST',dict(symbol=signal['symbol'],side='sell' if signal['direction']=='buy' else 'buy',qty=str(remaining),type='market',time_in_force='day' if signal['market']=='stock' else 'gtc',client_order_id=exit_id))
        record['exit_order']=order_summary(response);record['state']='exit pending'
    save(store,record)


def tick(store, broker=None, now=None):
    from momentum import view as signals_view
    now=int(time.time()*1000) if now is None else now
    broker=broker or AlpacaPaper()
    if os.environ.get('PAPER_TRADING_ENABLED','false').lower()!='true':return view(store)
    account=broker.request('/v2/account')
    fingerprint=hashlib.sha256(account['id'].encode()).hexdigest()[:16]
    if account['status']!='ACTIVE' or account.get('account_blocked') or account.get('trading_blocked'):
        raise ValueError('Paper account is not enabled for trading')
    records={r['id']:r for r in view(store)['trades']}
    for signal in signals_view(store)['signals']:
        key=identity(signal)
        if key not in records:
            records[key]=dict(id=key,signal=signal,state='scheduled',account=fingerprint,created_at=now)
            save(store,records[key])
    busy=set()
    for record in records.values():
        if record['state'] in {'scheduled','skipped','closed','closed unfilled'}:continue
        if record['account']!=fingerprint:raise ValueError('Paper account changed; existing records require review')
        try:reconcile(store,broker,record,now)
        except Exception as exc:
            record['reason']=str(exc) if isinstance(exc,(BrokerError,ValueError)) else type(exc).__name__;save(store,record)
        if record['state'] not in {'closed','closed unfilled'}:busy.add(record['signal']['market'])
    positions=broker.request('/v2/positions');orders=broker.request('/v2/orders?status=open&nested=true&limit=500')
    for record in sorted(records.values(),key=lambda r:r['signal']['entry_open_at']):
        if record['state']!='scheduled':continue
        signal=record['signal'];opening=signal['entry_open_at']
        try:
            if record['account']!=fingerprint:raise ValueError('Paper account changed; scheduled entry requires review')
            if now>=min(opening+ENTRY_WINDOW_MS,signal['expires_at']):raise ValueError('Opening execution window missed; no late entry')
            if signal['market']=='crypto' and signal['direction']=='sell':raise ValueError('Alpaca spot crypto does not support short entries')
            if now<opening:continue
            if signal['market'] in busy:raise ValueError('One momentum position per market already reserved')
            if any(p['asset_class']==('us_equity' if signal['market']=='stock' else 'crypto') for p in positions):raise ValueError('Existing paper position in this market; no overlapping entry')
            if any(o['asset_class']==('us_equity' if signal['market']=='stock' else 'crypto') for o in orders):raise ValueError('Existing paper order in this market; no overlapping entry')
            if signal['market']=='stock' and not broker.request('/v2/clock')['is_open']:continue
            asset=broker.request('/v2/assets/'+urllib.parse.quote(signal['symbol'],safe=''))
            order,sizing=plan(signal,account,asset,broker.quote(signal),now)
            record.update(state='submitting',sizing=sizing,order_request=order)
            save(store,record) # Commit before sending, including deterministic client order ID.
            busy.add(signal['market'])
            response=broker.request('/v2/orders','POST',order)
            record['order']=order_summary(response);record['state']='entry pending';save(store,record)
        except ValueError as exc:
            record.update(state='skipped',reason=str(exc));save(store,record)
        except Exception as exc:
            record['reason']=str(exc) if isinstance(exc,BrokerError) else type(exc).__name__;save(store,record)
    status=dict(checked_at=now,broker='Alpaca paper',account_status=account['status'],equity=float(account['equity']),
                buying_power=float(account['buying_power']),shorting_enabled=account.get('shorting_enabled'),crypto_status=account.get('crypto_status'))
    with store.connect() as db:db.execute('INSERT OR REPLACE INTO paper_status VALUES(1,?)',(json.dumps(status),))
    return view(store)


def cycle(store):
    # OS advisory lock covers network calls too, preventing worker/CLI competing writers.
    lock_path=Path(str(store.path)+'.paper.lock')
    with lock_path.open('a+b') as handle:
        try:
            if os.name=='nt':
                import msvcrt
                handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,BlockingIOError):return view(store)
        try:return tick(store)
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def worker(store, stop):
    while not stop.is_set():
        try:cycle(store)
        except Exception as exc:store.log('Paper trading check failed',str(exc) if isinstance(exc,(ValueError,BrokerError)) else type(exc).__name__)
        stop.wait(15)


if __name__=='__main__':
    from platform_app import ROOT, Store, load_env
    load_env();store=Store(Path(os.environ.get('DATA_DIR',str(ROOT/'data')))/'signals.sqlite3')
    result=cycle(store)
    print(json.dumps(result,indent=2))
