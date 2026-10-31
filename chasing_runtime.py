"""Opt-in chase worker within the existing installation owner; never submits trades."""
import json
import os
import time
import threading
from datetime import datetime, timezone
from divergence_chasing import create, step, FRAMES, CHASE_FRAMES, DURATIONS

_market_cache={}
_market_cache_lock=threading.Lock()


def enabled():
    return os.environ.get('DIVERGENCE_CHASING_ENABLED','false').lower()=='true'


def people(store):
    with store.connect() as db:
        if not db.execute("SELECT to_regprocedure('bot_runtime.chasing_recipients()')").fetchone()[0]:return []
        return db.execute('SELECT bot_runtime.chasing_recipients()').fetchone()[0]


def enqueue(db,s,event):
    key=f"{s['id']}:{event['kind']}:{event['at']}"
    db.execute('INSERT INTO chasing_outbox(id,session_id,event) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
               (key,s['id'],json.dumps(event)))


def enroll(store,root,person):
    if not enabled() or root.get('timeframe') not in ('1h','4h') or root.get('signal_status')!='provisional':return
    now=int(time.time()*1000)
    if not person['chasing'] or not person['routes'].get('chasing'):return
    # Never enroll an old catch-up alert or pre-registration history.
    if now-root['confirmed_at']>DURATIONS[root['timeframe']] or root['confirmed_at']<person.get('created_at',now/1000)*1000:return
    s=create(person['user_id'],root,now,person['stop_pct'])
    s['settings_revision']=person['revision']
    with store.connect() as db:
        created=db.execute('INSERT INTO chasing_sessions SELECT %s,installation,%s,%s FROM runtime_control WHERE id=1 AND bot_runtime.chasing_is_owner(installation,%s::uuid) ON CONFLICT DO NOTHING RETURNING id',
                          (s['id'],s['user'],json.dumps(s),s['user'])).fetchone()
        if created:enqueue(db,s,dict(kind='started',at=now,session=s['id']))


def market(root,now):
    """Initial live support: exact Kraken spot roots only. Other feeds stay unavailable."""
    from scanner import check_candles, detect
    from kraken_feed import fetch_kraken
    symbol=root['symbol']
    if not symbol.startswith('kraken:Kraken:'):raise ValueError('Chasing live feed currently requires Kraken spot')
    asset=dict(symbol=symbol.split(':',2)[2],exchange='Kraken',feed_confirmed=True)
    from divergence_chasing import DURATIONS
    bars={}
    for f in reversed(FRAMES):
        if DURATIONS[f]>DURATIONS[root.get('timeframe','4h')]:continue
        key=(symbol,f);boundary=now//DURATIONS[f]
        with _market_cache_lock:
            cached=_market_cache.get(key)
            if cached and cached[0]==boundary:rows=cached[1]
            else:
                rows=check_candles(fetch_kraken(asset,f),now)
                # Do not freeze delayed/empty data for a whole higher-frame bar.
                if rows and rows[-1].end>=boundary*DURATIONS[f]:
                    if len(_market_cache)>256:_market_cache.clear()
                    _market_cache[key]=(boundary,rows)
        bars[f]=rows
    minute=bars['1m']
    if not minute:raise ValueError('Minute prices unavailable')
    signals=[s for f,rows in bars.items() if f in CHASE_FRAMES for s in detect(rows,root['rules'],symbol,f,provisional=True)]
    return minute[-1].close,minute[-1].end,signals


def tick(store,now,fetch=market,clock=None):
    from postgres_store import assert_worker
    assert_worker(store)
    with store.connect() as db:
        rows=db.execute("SELECT id,state FROM chasing_sessions WHERE bot_runtime.chasing_is_owner(installation,user_id) AND (state->>'next_due')::bigint<=%s ORDER BY (state->>'next_due')::bigint LIMIT 100",(now,)).fetchall()
    cache={}
    for key,snapshot in rows:
        checked_now=clock() if clock else now
        try:
            root=snapshot['root'];feed=(root['symbol'],json.dumps(root['rules'],sort_keys=True))
            if feed not in cache:cache[feed]=fetch(root,checked_now)
            price,price_at,signals=cache[feed]
            checked_now=clock() if clock else now
        except (ValueError, OSError, KeyError, TypeError, IndexError):
            checked_now=clock() if clock else now
            with store.connect() as db:
                row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(key,)).fetchone()
                if not row or row[0]['revision']!=snapshot['revision']:continue
                state=row[0];state['next_due']=checked_now+60000
                if state.get('position') and not state.get('data_delayed'):
                    state['data_delayed']=True
                    enqueue(db,state,dict(kind='position_data_delayed',at=checked_now,session=key))
                state['revision']+=1
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),key))
            store.log('Chasing data unavailable',f"{root['symbol']}: awaiting supported, fresh closed candles")
            continue
        with store.connect() as db:
            row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(key,)).fetchone()
            if not row or row[0]['revision']!=snapshot['revision']:continue
            s,events=step(row[0],checked_now,price=price,price_at=price_at,signals=signals)
            if s.get('position'):
                fresh=price_at is not None and checked_now-120000<=price_at<=checked_now
                if not fresh and not s.get('data_delayed'):
                    s['data_delayed']=True
                    events.append(dict(kind='position_data_delayed',at=checked_now,session=key))
                elif fresh and s.get('data_delayed'):
                    s['data_delayed']=False
                    events.append(dict(kind='position_data_resumed',at=checked_now,session=key))
                if s.get('data_delayed')!=row[0].get('data_delayed'):s['revision']+=1
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(s),key))
            for event in events:enqueue(db,s,event)


def text_for(s,event):
    root=s['root'];head=f"{root['symbol']} · original {root['timeframe']} {root['direction']}\nOriginal close: {root['price2']:g}\n"
    kind=event['kind']
    if kind=='favorable_stop':return head+f"Favorable move reached {s['stop_pct']:g}% (observed {event['price']:g}). Chasing stopped."
    if kind=='opposite':return head+f"Opposite {event['signal']['direction']} {root['timeframe']} divergence found. Opposite search stopped. Minute position monitoring continues until you report exiting."
    if kind=='chase':return head+f"Same-direction {event['signal']['timeframe']} divergence. "+('First eligible divergence: chasing stopped; entry unconfirmed.')
    if kind=='entered':return head+'Entry reported. Checking closed-minute prices every minute; waiting for the first fresh post-click market reference. No broker fill is known.'
    if kind=='position_reference':
        p=s['position']
        at=datetime.fromtimestamp(event['price_at']/1000,timezone.utc).strftime('%d %b %Y %H:%M UTC')
        wider=' On 4h signals, widen to a 1% trail at +2%.' if p['frame']=='4h' else ''
        return head+f"Monitoring reference: {event['price']:g} (closed minute at {at}). This is not a broker fill. Initial loss alert: {p['stop_pct']:g}%; profit alert without additional divergence: {p['target_pct']:g}%. With an additional divergence: activate a 0.5% trail at +1%.{wider} Checked every minute."
    if kind=='position_extended':return head+f"Additional same-direction {event['signal']['timeframe']} divergence. Fixed profit target replaced by trailing rules; original loss stop remains until the trail activates."
    if kind=='position_trail':return head+f"Trailing distance now {event['trail_pct']:g}%; observed stop level {event['stop']:g}. Monitoring continues every minute."
    if kind=='exit_signal':return head+f"EXIT ALERT · {event['reason'].replace('_',' ')}\nObserved price: {event['price']:g}\nMove from monitoring reference: {event['move_pct']:+.3f}%\nPlease review and exit manually. No order was sent. Monitoring continues until you confirm I've exited."
    if kind=='reported_exit':return head+'You reported exiting. Position monitoring stopped. No broker closure was inferred.'
    if kind=='position_data_delayed':return head+'Position data delayed. Exit conditions cannot be evaluated reliably; please monitor the position manually. Retrying every minute.'
    if kind=='position_data_resumed':return head+'Fresh minute data resumed. Position monitoring continues; movement during the data gap is unknown.'
    return head+dict(started='Chasing started. Only I’m in confirms your entry.',return_to_close='Price returned to the original close. Chasing continues.',expired='Chase expired after 40 hours.')[kind]


def deliver_pending(store,token):
    from postgres_store import assert_worker, WorkerOwned
    from telegram_delivery import deliver
    from telegram_personal import prepare_message, bind_message
    from telegram_checklist import api, bot_key
    recipients={str(p['user_id']):p for p in people(store)}
    with store.connect() as db:
        rows=db.execute("SELECT o.id,o.event,s.state FROM chasing_outbox o JOIN chasing_sessions s ON s.id=o.session_id WHERE o.status='pending' AND o.retry_at<=%s ORDER BY o.event->>'at' LIMIT 100",(int(time.time()*1000),)).fetchall()
    for key,event,s in rows:
        person=recipients.get(s['user']);route='live_positions' if event['kind'] in ('opposite','entered','position_reference','position_extended','position_trail','exit_signal','reported_exit','position_data_delayed','position_data_resumed') else 'chasing'
        stale=event['kind'] in ('started','return_to_close','chase') and s['status']!='chasing' and not(event['kind']=='chase' and s['status']=='complete_first')
        if route=='live_positions' and s.get('position',{}).get('status')=='closed' and event['kind']!='reported_exit':stale=True
        if not person or not person[route] or not person['routes'].get(route) or stale:
            with store.connect() as db:db.execute("UPDATE chasing_outbox SET status='suppressed' WHERE id=%s",(key,))
            continue
        if event['kind']=='return_to_close':
            with store.connect() as db:
                slot=db.execute('''INSERT INTO chasing_price_slots VALUES(%s,%s,extract(epoch FROM clock_timestamp()))
                  ON CONFLICT(user_id) DO UPDATE SET event_id=excluded.event_id,reserved_at=excluded.reserved_at
                  WHERE chasing_price_slots.event_id=excluded.event_id OR chasing_price_slots.reserved_at<=excluded.reserved_at-60
                  RETURNING user_id''',(s['user'],key)).fetchone()
                if not slot:db.execute("UPDATE chasing_outbox SET status='suppressed' WHERE id=%s",(key,))
            if not slot:continue
        assert_worker(store)
        chat=person['routes'][route]
        with store.connect() as db:
            db.execute('UPDATE chasing_outbox SET chat=coalesce(chat,%s),telegram_user_id=coalesce(telegram_user_id,%s),bot=coalesce(bot,%s) WHERE id=%s',(chat,person['telegram_user_id'],bot_key(token),key))
            chat,owner=db.execute('SELECT chat,telegram_user_id FROM chasing_outbox WHERE id=%s',(key,)).fetchone()
        def send(token,chat,text,**kwargs):
            assert_worker(store)
            item=prepare_message(store,token,chat,text,[],owner)
            if s['status'] in ('chasing','complete_first'):
                item.extra_keyboard=[[dict(text="I'm in",callback_data='chase:enter:'+s['id'])]]
            elif s.get('position') and s['position']['status']!='closed':
                item.extra_keyboard=[[dict(text="I've exited",callback_data='chase:exit:'+s['id'])]]
            message=api(token,'sendMessage',dict(chat_id=chat,text=text,reply_markup=item.keyboard(),disable_notification=kwargs.get('silent',False)))
            bind_message(store,item,message)
            with store.connect() as db:
                db.execute('UPDATE chasing_outbox SET message_id=%s,dismissal_id=%s WHERE id=%s',(message['message_id'],item.key,key))
            return message['message_id']
        try:
            deliver(store,token,chat,text_for(s,event),key,send)
        except WorkerOwned:
            raise
        except RuntimeError:
            with store.connect() as db:db.execute('UPDATE chasing_outbox SET retry_at=%s WHERE id=%s',(int(time.time()*1000)+60000,key))
            store.log('Chasing delivery delayed','Personal delivery will retry; other recipients continue.')
            continue
        with store.connect() as db:db.execute("UPDATE chasing_outbox SET status='sent' WHERE id=%s",(key,))


def callback(store,token,query):
    from telegram_checklist import api,bot_key
    from postgres_store import assert_worker
    leaving=query.get('data','').startswith('chase:exit:')
    key=query.get('data','').removeprefix('chase:exit:' if leaving else 'chase:enter:')
    message=query.get('message') or {};user=query.get('from',{}).get('id')
    assert_worker(store)
    answer='Entry unavailable for this message.'
    with store.connect() as db:
        binding=db.execute('SELECT 1 FROM chasing_outbox o JOIN telegram_personal_messages m ON m.id=o.dismissal_id WHERE o.session_id=%s AND o.bot=%s AND o.chat=%s AND o.message_id=%s AND o.telegram_user_id=%s AND m.state::jsonb->>\'phase\'=\'active\'',
                           (key,bot_key(token),str(message.get('chat',{}).get('id')),message.get('message_id'),user)).fetchone()
        if binding:
            row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s AND bot_runtime.chasing_is_owner(installation,user_id) FOR UPDATE',(key,)).fetchone()
            if not row:
                api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text=answer))
                return
            s=row[0]
            s,events=step(s,int(time.time()*1000),enter=not leaving,leave=leaving)
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(s),key))
            for event in events:enqueue(db,s,event)
            if leaving and s['status']=='reported_exited':answer='Exit reported. Monitoring stopped.'
            elif s['entry_at'] is not None:answer='Entry recorded. Minute monitoring is starting; awaiting a fresh market reference.' if events else 'Entry was already recorded.'
    api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text=answer))


def worker(store,token,dry_run,stop):
    from local_settings import delivery_dry_run
    if not enabled():return
    while not stop.is_set():
        if not delivery_dry_run(store,dry_run):
            tick(store,int(time.time()*1000),clock=lambda:int(time.time()*1000))
            deliver_pending(store,token)
        stop.wait(1)
