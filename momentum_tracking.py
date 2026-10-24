"""Durable, explicitly simulated tracking for momentum entries the paper broker did not fill."""
import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum import outcome
from paper_trading import initialize as initialize_paper, save
from platform_app import ROOT, Store, load_env
from scanner import Candle


def exit_deadline(signal):
    opening=signal.get('entry_open_at')
    if not opening:return None
    days=int(signal.get('rules',{}).get('max_hold',10))
    if signal['market']=='crypto':
        return int((datetime.fromtimestamp(opening/1000,ZoneInfo('UTC'))+timedelta(days=days)).timestamp()*1000)
    from market_calendar import stock_trading_day,stock_close
    date=datetime.fromtimestamp(opening/1000,ZoneInfo('America/New_York')).date();count=1
    while count<days:
        date+=timedelta(days=1)
        if stock_trading_day(date):count+=1
    return int(stock_close(date).timestamp()*1000)-30_000


def refresh(store, now=None):
    now=int(time.time()*1000) if now is None else int(now)
    with store.connect() as db:
        initialize_paper(db)
        rows=[json.loads(x[0]) for x in db.execute('SELECT payload FROM paper_trades')]
        histories={(m,s):[Candle(**b) for b in json.loads(raw)] for m,s,raw in db.execute('SELECT market,symbol,payload FROM momentum_history')}
    events=[];tracking=[];groups={}
    for record in rows:
        signal=record.get('signal',{})
        if not signal:continue
        signature=(signal.get('market'),signal.get('symbol'),signal.get('direction'),signal.get('confirmed_at'))
        groups.setdefault(signature,[]).append(record)
    for group in groups.values():
        # A real fill wins over any duplicate-version failure for the same candle.
        if any(float((r.get('order') or {}).get('filled_qty') or 0)>0 for r in group):continue
        failed=[r for r in group if r.get('state') in {'skipped','closed unfilled'}]
        if not failed:continue
        # Older versions can contain duplicate journals for one issued confirmation.
        record=next((r for r in failed if r.get('tracking')),max(failed,key=lambda r:r.get('created_at',0)))
        signal=record.get('signal',{});state=record.get('state')
        track=record.get('tracking')
        if not track:
            quote=record.get('entry_quote') or {}
            if quote.get('price') is not None:
                price=float(quote['price']);source=quote.get('source','broker quote');price_at=quote.get('timestamp')
            else:
                price=float(signal['entry']);source='momentum confirmation close (reference)';price_at=signal.get('confirmed_at')
            track=dict(id='track-'+record['id'],kind='simulated_tracking',label='SIMULATED TRACKING — not an Alpaca position',
                       symbol=signal['symbol'],name=record.get('asset_name') or signal['symbol'],market=signal['market'],
                       direction=signal['direction'],paper_entry_state=state,paper_failure=record.get('reason'),
                       entry_price=price,entry_price_source=source,entry_price_at=price_at,
                       stop=float(signal['stop']),target=float(signal['target']),time_exit_at=exit_deadline(signal),
                       outcome_status='awaiting daily candle',exit=None,tracking_return_pct=None,
                       notifications={'entry_failure_reported_at':None,'close_reported_at':None},created_at=now)
        bars=histories.get((signal['market'],signal['symbol']),[])
        result=outcome(signal,bars) if bars else {'status':'unavailable','reason':'No persisted daily history'}
        track['paper_entry_state']=state;track['paper_failure']=record.get('reason')
        track['outcome_status']=result.get('status')
        if result.get('entry_price') is not None:
            track['simulated_entry_price']=float(result['entry_price'])
            track['simulated_entry_source']='next-session daily candle open'
        if result.get('exit_price') is not None:
            direction=1 if signal['direction']=='buy' else -1
            entry=float(track.get('simulated_entry_price',track['entry_price']))
            gross=direction*(float(result['exit_price'])/entry-1)*100
            costs=2*float(signal.get('rules',{}).get('cost_bps',10))/100
            track['exit']={'timestamp':result.get('exit_at'),'reference_price':float(result['exit_price']),
                           'reason':result['status'],'price_source':'daily OHLC simulation',
                           'gross_return_pct':gross,'net_return_pct':gross-costs}
            track['tracking_return_pct']=gross-costs
        if result.get('status') in {'stop','target','time exit'}:track['tracking_state']='closed'
        elif result.get('status')=='awaiting entry':track['tracking_state']='awaiting daily candle'
        elif result.get('status') in {'skipped gap','skipped reward/risk','unavailable'}:track['tracking_state']='reference only'
        else:track['tracking_state']='open simulated position'
        track['updated_at']=now
        record['tracking']=track;save(store,record)
        tracking.append(track)
        notifications=track.setdefault('notifications',{})
        if not notifications.get('entry_failure_reported_at'):
            events.append(dict(id=track['id']+':entry_failure',kind='entry_failure',record_id=record['id'],tracking=track))
        if track.get('exit') and not notifications.get('close_reported_at'):
            events.append(dict(id=track['id']+':tracking_close',kind='tracking_close',record_id=record['id'],tracking=track))
    # Real paper fills/exits are reported independently from simulated tracking.
    for record in rows:
        order=record.get('order') or {};filled=float(order.get('filled_qty') or 0)
        notifications=record.setdefault('notifications',{})
        if filled and not notifications.get('fill_reported_at'):
            events.append(dict(id=record['id']+':paper_fill',kind='paper_fill',record_id=record['id'],trade=record))
        exits=record.get('exits',[])+([record['exit_order']] if record.get('exit_order') else [])
        if record.get('state')=='closed' and exits and not notifications.get('close_reported_at'):
            events.append(dict(id=record['id']+':paper_close',kind='paper_close',record_id=record['id'],trade=record))
    return {'tracking':tracking,'events':events,'event_count':len(events)}


def acknowledge(store, record_id, kind, now=None):
    now=int(time.time()*1000) if now is None else int(now)
    fields={'entry_failure':'tracking.notifications.entry_failure_reported_at',
            'tracking_close':'tracking.notifications.close_reported_at',
            'paper_fill':'notifications.fill_reported_at','paper_close':'notifications.close_reported_at'}
    if kind not in fields:raise ValueError('Unknown tracking notification type')
    with store.connect() as db:
        initialize_paper(db)
        row=db.execute('SELECT payload FROM paper_trades WHERE id=%s',(record_id,)).fetchone()
        if not row:raise ValueError('Tracking record not found')
        record=json.loads(row[0])
        if kind.startswith('tracking_') or kind=='entry_failure':
            if not record.get('tracking'):raise ValueError('Simulated tracking record not found')
            key=kind.removeprefix('tracking_')
            record['tracking'].setdefault('notifications',{})['entry_failure_reported_at' if key=='entry_failure' else 'close_reported_at']=now
        else:
            field='fill_reported_at' if kind=='paper_fill' else 'close_reported_at'
            record.setdefault('notifications',{})[field]=now
        db.execute('INSERT INTO paper_trades VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(record_id,json.dumps(record)))
    return {'record_id':record_id,'kind':kind,'acknowledged_at':now}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ack',nargs=2,metavar=('RECORD_ID','KIND'));args=parser.parse_args()
    load_env();store=Store()
    result=acknowledge(store,*args.ack) if args.ack else refresh(store)
    print(json.dumps(result,indent=2))
