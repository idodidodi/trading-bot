"""Bybit DEMO execution of fresh stored signals, within the existing worker.

No scanner, Telegram poller, mainnet selector, or automatic submission retries.
Persist intents before writes. Unknown outcomes block entries across restarts.
"""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import hashlib
import json
import os
import threading
import time

from execution_gateway import BybitDemo, ExecutionError, SubmissionUnknown, number

TERMINAL = {'closed', 'unfilled', 'skipped'}
ORDER_TERMINAL = {'Filled', 'Cancelled', 'Rejected', 'Deactivated', 'PartiallyFilledCanceled'}
_lock = threading.Lock()


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('Invalid nonnegative broker value')
    return result


def quantize(value, step, rounding=ROUND_DOWN):
    return (value / step).to_integral_value(rounding=rounding) * step


def protection(price, side, tick, distance='0.01'):
    price, tick, distance = number(price), number(tick), number(distance)
    sign = 1 if side == 'Buy' else -1
    stop = quantize(price * (1-sign*distance), tick, ROUND_HALF_UP)
    target = quantize(price * (1+sign*distance), tick, ROUND_HALF_UP)
    if not stop > 0 or not sign*(price-stop) > 0 or not sign*(target-price) > 0:
        raise ValueError('Protection cannot be represented at the instrument tick')
    return {'stopLoss': format(stop, 'f'), 'takeProfit': format(target, 'f')}


def policy_valid(config):
    # Versioned policy is independent of signal algorithms and saved per intent.
    if config.get('version') != 1:
        raise ValueError('Unsupported demo execution policy')
    if number(config['allocation']) > Decimal('.25') or number(config['exit_distance']) != Decimal('.01'):
        raise ValueError('Policy exceeds authorized allocation or exit distance')
    if not 0 < config['max_signal_age_ms'] <= 300000:
        raise ValueError('Invalid signal freshness window')
    if set(config['strategies']) - {'chase-4h-5m', 'rsi-pb'}:
        raise ValueError('Unknown entry strategy')
    if not config['symbols'] or any(not a.endswith('USD') or s != a[:-3]+'USDT' for a,s in config['symbols'].items()):
        raise ValueError('Only explicit USD crypto to USDT mappings are supported')


def intent(strategy, signal, asset, source_id, config, activated_at, now, root=None):
    if strategy not in config['strategies'] or asset not in config['symbols']:
        return None
    at = signal.get('confirmed_at', 0)
    if at < activated_at or not 0 <= now-at <= config['max_signal_age_ms']:
        return None
    if signal.get('direction') not in ('bullish', 'bearish'):
        return None
    if strategy == 'chase-4h-5m':
        if not root or root.get('timeframe') != '4h' or signal.get('timeframe') != '5m':
            return None
        if root['direction'] != signal['direction'] or at <= root['confirmed_at']:
            return None
    # Deduplicate the same signal across group/manual destinations and revisions.
    identity = [strategy, asset, signal.get('timeframe'), signal['direction'],
                signal.get('pivot2'), at]
    key = 'demo-' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:28]
    return dict(id=key, strategy=strategy, asset=asset, symbol=config['symbols'][asset],
                side='Buy' if signal['direction']=='bullish' else 'Sell',
                signal=signal, root=root, source_id=source_id, created_at=now,
                policy=config)


def plan(trade, account, instrument, ticker, positions, orders):
    symbol = trade['symbol']
    if any(instrument.get(k) != v for k,v in dict(symbol=symbol,status='Trading',
           contractType='LinearPerpetual',quoteCoin='USDT',settleCoin='USDT').items()):
        raise ValueError('Instrument is not an active USDT perpetual')
    if any(not (o.get('reduceOnly') is True and o.get('stopOrderType') in
                ('StopLoss','TakeProfit','PartialStopLoss','PartialTakeProfit')) for o in orders):
        raise ValueError('Working account orders block new entries')
    active = [p for p in positions if decimal(p['size']) > 0]
    if any(p['symbol']==symbol for p in active):
        raise ValueError('A position already exists on this symbol')
    if any(int(p['positionIdx']) != 0 for p in positions):
        raise ValueError('Hedge mode is unsupported')
    usdt=next(c for c in account['coin'] if c['coin']=='USDT')
    usd_per_usdt=number(usdt['usdValue'])/number(usdt['equity'])
    equity = number(account['totalEquity']) / usd_per_usdt
    available = decimal(account['totalAvailableBalance']) / usd_per_usdt
    allocation = equity * number(trade['policy']['allocation'])
    exposure = sum((abs(Decimal(p['positionValue'])) for p in active), Decimal(0))
    if exposure + allocation > equity or allocation > available * Decimal('.99'):
        raise ValueError('Account exposure or available collateral limit')
    bid, ask = number(ticker['bid1Price']), number(ticker['ask1Price'])
    if ask < bid or ask/bid > Decimal('1.005'):
        raise ValueError('Invalid or excessive execution spread')
    price = ask if trade['side']=='Buy' else bid
    limits = instrument['lotSizeFilter']
    # Reserve 0.1% for market slippage so quantity never multiplies by leverage.
    quantity = quantize(allocation/(price*Decimal('1.001')), number(limits['qtyStep']))
    if not number(limits['minOrderQty']) <= quantity <= number(limits['maxMktOrderQty']):
        raise ValueError('25% allocation is outside market quantity limits')
    if quantity*price < number(limits['minNotionalValue']):
        raise ValueError('Below minimum order notional')
    tick = instrument['priceFilter']['tickSize']
    return dict(qty=format(quantity,'f'), reference_price=format(price,'f'), tick=tick,
                equity=format(equity,'f'), notional_cap=format(allocation,'f'),
                **protection(price,trade['side'],tick,trade['policy']['exit_distance']))


class Broker(BybitDemo):
    def pages(self, path, params):
        rows, cursor, seen = [], '', set()
        for _ in range(100):
            result = self._request(path, params | {'limit':200 if path=='/v5/position/list' else 50} | ({'cursor':cursor} if cursor else {}))
            rows.extend(result['list'])
            cursor = result.get('nextPageCursor')
            if not cursor:
                return rows
            if cursor in seen:
                break
            seen.add(cursor)
        raise ExecutionError('Incomplete broker pagination')

    def account(self):
        rows = self._request('/v5/account/wallet-balance', {'accountType':'UNIFIED'})['list']
        if len(rows)!=1 or rows[0]['accountType']!='UNIFIED':
            raise ExecutionError('Unexpected account response')
        return rows[0]

    def positions(self, symbol=None):
        params = dict(category='linear')
        if symbol:
            return self.pages('/v5/position/list', params | {'symbol':symbol})
        return sum((self.pages('/v5/position/list', params | {'settleCoin':coin})
                    for coin in ('USDT','USDC')), [])

    def orders(self):
        return sum((self.pages('/v5/order/realtime', {'category':'linear','settleCoin':coin,'openOnly':0})
                    for coin in ('USDT','USDC')), [])

    def order(self, trade, close=False):
        key = trade['id'] + ('-x' if close else '')
        params = dict(category='linear',symbol=trade['symbol'],orderLinkId=key)
        rows = self.pages('/v5/order/realtime',params)
        if not rows:
            rows = self.pages('/v5/order/history',params)
        matching = [r for r in rows if r.get('orderLinkId')==key]
        if len(matching)>1:
            raise ExecutionError('Ambiguous order identity')
        return matching[0] if matching else None

    def instrument(self, symbol):
        rows = self._request('/v5/market/instruments-info',{'category':'linear','symbol':symbol})['list']
        if len(rows)!=1:
            raise ExecutionError('Instrument unavailable')
        return rows[0]

    def ticker(self, symbol):
        rows = self._request('/v5/market/tickers',{'category':'linear','symbol':symbol})['list']
        if len(rows)!=1 or rows[0]['symbol']!=symbol:
            raise ExecutionError('Quote unavailable')
        return rows[0]

    def entry(self, trade):
        p=trade['plan']
        return self._request('/v5/order/create',body=dict(category='linear',symbol=trade['symbol'],
            side=trade['side'],orderType='Market',qty=p['qty'],orderLinkId=trade['id'],
            positionIdx=0,reduceOnly=False,tpslMode='Full',tpOrderType='Market',slOrderType='Market',
            tpTriggerBy='LastPrice',slTriggerBy='LastPrice',takeProfit=p['takeProfit'],stopLoss=p['stopLoss']))

    def protect(self, trade, levels):
        return self._request('/v5/position/trading-stop',body=dict(category='linear',symbol=trade['symbol'],
            positionIdx=0,tpslMode='Full',tpOrderType='Market',slOrderType='Market',
            tpTriggerBy='LastPrice',slTriggerBy='LastPrice',**levels))

    def flatten(self, trade, quantity):
        return self._request('/v5/order/create',body=dict(category='linear',symbol=trade['symbol'],
            side='Sell' if trade['side']=='Buy' else 'Buy',orderType='Market',qty=str(quantity),
            orderLinkId=trade['id']+'-x',positionIdx=0,reduceOnly=True))


def save(store, trade, state, **values):
    from runtime_store import activity_connection
    if state=='closing':trade['close_requested']=True
    trade.update(values, state=state, updated_at=int(time.time()*1000))
    with activity_connection(store) as db:
        db.execute('UPDATE demo_execution_trades SET state=%s,payload=%s,updated_at=%s WHERE id=%s',
                   (state,json.dumps(trade),trade['updated_at'],trade['id']))


def opposing_divergence(store, trade, now):
    original=trade['root'] if trade['strategy']=='chase-4h-5m' else trade['signal']
    source=original['symbol'] if trade['strategy']=='chase-4h-5m' else original['source']
    direction='bearish' if trade['side']=='Buy' else 'bullish'
    candidates=[e['evidence'] for e in store.events() if e['strategy']=='rsi'
        and e['kind']!='invalidated' and e['origin']=='live' and e['delivery_mode']=='live'
        and e['instrument']['source']==source and e['timeframe']==original['timeframe']
        and e['direction']==direction and e['evidence'].get('signal_status')=='provisional'
        and trade['entry_terminal_at']<e['occurred_at']<=now]
    return min(candidates,key=lambda s:s['confirmed_at']) if candidates else None


def divergence_close(store, trade, signal, now):
    trade.update(state='closing',close_requested=True,updated_at=now,exit_reason='opposite_divergence',
                 exit_divergence=signal,reason='Opposing RSI divergence on original timeframe')
    original=trade['root'] if trade['strategy']=='chase-4h-5m' else trade['signal']
    notice=dict(id=trade['id']+':exit',kind='demo_exit',at=now,root=original,signal=signal,symbol=trade['symbol'])
    with store.connect() as db:
        db.execute('UPDATE demo_execution_trades SET state=%s,payload=%s,updated_at=%s WHERE id=%s',
                   ('closing',json.dumps(trade),now,trade['id']))
        db.execute('INSERT OR IGNORE INTO notifications(id,payload) VALUES(?,?)',(notice['id'],json.dumps(notice)))


def reconcile(store, broker, trade, now):
    from runtime_store import assert_worker
    order = broker.order(trade)
    if not order:
        # Absence is inconclusive, including after a crash before submission.
        save(store,trade,'unknown',reason='Entry outcome unresolved; no retry')
        return
    status = order['orderStatus']
    filled = decimal(order.get('cumExecQty') or '0')
    if status not in ORDER_TERMINAL:
        save(store,trade,'pending',reason='Awaiting terminal entry status')
        return
    if not filled:
        save(store,trade,'unfilled',reason='Entry terminated without a fill')
        return
    price = number(order['avgPrice'])
    if 'entry_terminal_at' not in trade:
        # Terminal update is a conservative boundary for partial fills/cancellations.
        try:terminal_at=int(order.get('updatedTime') or now)
        except (ValueError,TypeError):terminal_at=now
        trade['entry_terminal_at']=terminal_at if trade['created_at']<=terminal_at<=now else now
    trade.update(filled_qty=str(filled),fill_price=str(price))
    positions = [p for p in broker.positions(trade['symbol']) if decimal(p['size'])>0]
    if not positions:
        # Avoid interpreting a just-filled order's lagging position snapshot as flat.
        seen = trade.get('flat_since',now)
        save(store,trade,'closed' if now-seen>=30000 else 'pending',flat_since=seen,
             filled_qty=str(filled),fill_price=str(price))
        return
    trade.pop('flat_since',None)
    if len(positions)!=1 or positions[0]['side']!=trade['side'] or int(positions[0]['positionIdx'])!=0:
        save(store,trade,'blocked',reason='Position differs from owned entry')
        return
    position=positions[0]
    size=number(position['size'])
    if size>filled or number(position['avgPrice'])!=price:
        save(store,trade,'blocked',reason='Position was modified outside this entry')
        return
    if trade['state']=='closing' or trade.get('close_requested'):
        close=broker.order(trade,close=True)
        save(store,trade,'closing',reason='Awaiting verified flat position; no close retry',close_status=close.get('orderStatus') if close else None)
        return
    opposite=opposing_divergence(store,trade,now)
    if opposite:
        divergence_close(store,trade,opposite,now)
        assert_worker(store)
        broker.flatten(trade,size)
        return
    levels=protection(price,trade['side'],trade['plan']['tick'],trade['policy']['exit_distance'])
    matches=all(decimal(position.get(k) or 0)==number(v) for k,v in levels.items())
    oversized=filled*price>number(trade['plan']['notional_cap'])*Decimal('1.001')
    if matches and not oversized:
        save(store,trade,'protected',filled_qty=str(filled),fill_price=str(price),protection=levels)
        return
    if trade.get('protection_attempted') or oversized:
        # Initial attached protection exists, but failed fill-relative correction
        # or excess slippage closes only this known position, never adds exposure.
        save(store,trade,'closing',reason='Protection unverified or allocation exceeded')
        assert_worker(store)
        broker.flatten(trade,size)
        return
    save(store,trade,'protecting',protection_attempted=True,filled_qty=str(filled),fill_price=str(price),protection=levels)
    assert_worker(store)
    broker.protect(trade,levels)


def candidates(store, policy, now):
    items=[]
    for strategy in policy['config']['strategies']:
        for event in store.signals(strategy,now):
            signal=event['evidence'];asset=event['instrument']['symbol']
            candidate=intent(strategy,signal,asset,event['event_id'],policy['config'],policy['activated_at'],now,root=signal.get('root'))
            if candidate:
                candidate.update(signal_id=event['signal_id'],event_id=event['event_id'])
                items.append(candidate)
    return sorted(items,key=lambda t:(t['signal']['confirmed_at'],t['id']))


def cycle(store, broker=None, now=None):
    from runtime_store import assert_worker
    if not _lock.acquire(blocking=False):return
    try:
        assert_worker(store)
        now=int(time.time()*1000) if now is None else now
        with store.connect() as db:
            row=db.execute('SELECT revision,enabled,activated_at,config FROM demo_execution_policy WHERE id=1').fetchone()
            if not row:return
            policy=dict(zip(('revision','enabled','activated_at','config'),row));policy['config']=json.loads(policy['config'])
            active=[json.loads(r[0]) for r in db.execute("SELECT payload FROM demo_execution_trades WHERE state NOT IN ('closed','unfilled','skipped') ORDER BY id")]
        policy_valid(policy['config'])
        broker=broker or Broker(os.environ['family_trading_bot_bybit_demo_api_key'],os.environ['family_trading_bot_bybit_demo_api_secret'])
        for trade in active:reconcile(store,broker,trade,now)
        with store.connect() as db:
            unresolved=db.execute("SELECT 1 FROM demo_execution_trades WHERE state NOT IN ('closed','unfilled','skipped','protected') LIMIT 1").fetchone()
        if unresolved or not policy['enabled']:return
        for trade in candidates(store,policy,now):
            with store.connect() as db:
                if db.execute('SELECT 1 FROM demo_execution_trades WHERE id=?',(trade['id'],)).fetchone():continue
            try:
                instrument=broker.instrument(trade['symbol']);account=broker.account();positions=broker.positions()
                positions+=broker.positions(trade['symbol']) if not any(p['symbol']==trade['symbol'] for p in positions) else []
                trade['plan']=plan(trade,account,instrument,broker.ticker(trade['symbol']),positions,broker.orders())
            except ValueError:
                trade.update(state='skipped',reason='Instrument, exposure, collateral or sizing check failed')
            else:trade.update(state='submitting')
            if int(time.time()*1000)-trade['signal']['confirmed_at']>policy['config']['max_signal_age_ms']:continue
            with store.connect() as db:
                current=db.execute('SELECT revision,enabled FROM demo_execution_policy WHERE id=1').fetchone()
                if current!=(policy['revision'],1):return
                inserted=db.execute('INSERT OR IGNORE INTO demo_execution_trades VALUES(?,?,?,?)',
                    (trade['id'],trade['state'],json.dumps(trade),now)).rowcount
            if not inserted or trade['state']=='skipped':continue
            assert_worker(store);broker.entry(trade);save(store,trade,'submitted')
            break
    finally:_lock.release()


def check(store):
    try:cycle(store)
    except (ExecutionError,ValueError,KeyError,TypeError,ArithmeticError,StopIteration):
        store.log('Demo check failed','New entries blocked until reconciliation')
