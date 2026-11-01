"""Opt-in chase worker within the existing installation owner; never submits trades."""
import json
import os
import time
from source_update import activity_connection
from datetime import datetime, timezone
from divergence_chasing import create, step, FRAMES, CHASE_FRAMES, DURATIONS

from shared_kraken import candles as _market_cache


def enabled():
    return os.environ.get('DIVERGENCE_CHASING_ENABLED','false').lower()=='true'


def people(store):
    with activity_connection(store) as db:
        if not db.execute("SELECT to_regprocedure('bot_runtime.chasing_recipients()')").fetchone()[0]:return []
        return db.execute('SELECT bot_runtime.chasing_recipients()').fetchone()[0]


def enqueue(db,s,event):
    from crypto_chase_policy import POLICY
    if s.get('watch_policy') == POLICY and event['kind'] in ('started','chase','expired') and not event.get('destination'):
        for destination in s.get('destinations', ['chasing']):
            enqueue(db, s, event | dict(destination=destination))
        return
    key=f"{s['id']}:{event['kind']}:{event['at']}"
    if s.get('watch_policy') == POLICY and event.get('signal'):
        signal=event['signal']
        key+=f":{signal['timeframe']}:{signal['confirmed_at']}:{signal['pivot2']}"
    if event.get('request_id'):key+=':'+event['request_id']
    if event.get('destination'):key+=':'+event['destination']
    db.execute('INSERT INTO chasing_outbox(id,session_id,event) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
               (key,s['id'],json.dumps(event)))


def proposal(root,person,now,automatic=False):
    from crypto_chase_policy import is_crypto, ORDER, POLICY
    crypto=is_crypto(root)
    frames=ORDER[:-3] if crypto else ('1h','4h')
    if not enabled() or root.get('timeframe') not in frames or root.get('signal_status')!='provisional':return
    route='crypto_auto_chase' if automatic else 'chasing'
    if not person.get(route) or not person['routes'].get(route):return
    # Never enroll an old catch-up alert or pre-registration history.
    max_age=DURATIONS.get(root['timeframe'],24*3600000)
    created=person.get('auto_created_at' if automatic else 'created_at',now/1000)
    if now-root['confirmed_at']>max_age or root['confirmed_at']<created*1000:return
    s=create(person['user_id'],root,now,person['stop_pct'],watch_policy=POLICY if crypto else None)
    if crypto:s['destinations']=[route]
    s['settings_revision']=person['revision']
    return s


def chase_buttons(key):
    return [[dict(text='Chase',callback_data='chase:start:'+key),
             dict(text='Chase + look back',callback_data='chase:lookback:'+key)]]


def enroll(store,root,person,automatic=False):
    now=int(time.time()*1000)
    s=proposal(root,person,now,automatic=automatic)
    if s is None:return
    if s.get('watch_policy'):s.update(asset_context(store,root))
    with activity_connection(store) as db:
        if s.get('watch_policy'):
            return _enroll_crypto(db,s,now)
        created=db.execute('INSERT INTO chasing_sessions SELECT %s,installation,%s,%s FROM runtime_control WHERE id=1 AND bot_runtime.chasing_is_owner(installation,%s::uuid) ON CONFLICT DO NOTHING RETURNING id',
                          (s['id'],s['user'],json.dumps(s),s['user'])).fetchone()
        if created:enqueue(db,s,dict(kind='started',at=now,session=s['id']))
    return s


def _enroll_crypto(db,s,now):
    """One owner/instrument pre-entry watch; enrollment never resets an active timer."""
    from crypto_chase_policy import lower
    owner=db.execute('SELECT installation FROM runtime_control WHERE id=1 AND bot_runtime.chasing_is_owner(installation,%s::uuid)',(s['user'],)).fetchone()
    if not owner:return
    installation=owner[0]
    asset_key=s.get('asset_key',s['root']['symbol'])
    db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(installation)+s['user']+asset_key+':crypto-watch',))
    same=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(s['id'],)).fetchone()
    if same:
        saved=same[0]
        while saved.get('superseded_by'):
            saved=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(saved['superseded_by'],)).fetchone()[0]
        if saved['status']!='chasing':return saved
    rows=db.execute("SELECT id,state FROM chasing_sessions WHERE installation=%s AND user_id=%s AND coalesce(state->>'asset_key',state->'root'->>'symbol')=%s AND state->>'status'='chasing' FOR UPDATE",(installation,s['user'],asset_key)).fetchall()
    active=None
    for key,state in rows:
        if state.get('expires_at') is not None and now>=state['expires_at']:
            state,events=step(state,now)
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),key))
            for event in events:enqueue(db,state,event)
        else:active=state
    if active:
        if active['root']['direction']!=s['root']['direction'] and not lower(s['stage'],active['stage']):return
        added=set(s['destinations'])-set(active.get('destinations',['chasing']))
        active['destinations']=sorted(set(active.get('destinations',['chasing']))|set(s['destinations']))
        if lower(s['stage'],active['stage']):
            if s['root']['direction']==active['root']['direction']:
                # A newly published lower original is also a secondary divergence.
                active,events=step(active,now,signals=[s['root']])
                for event in events:enqueue(db,active,event)
            else:
                # An opposite original cannot be called a same-direction follow-up.
                # Its lower timeframe still has priority as a new original watch.
                s['destinations']=active['destinations']
                active.update(status='superseded',next_due=None,superseded_by=s['id'],revision=active['revision']+1)
                if active.get('lookback',{}).get('status')=='pending':active['lookback'].update(status='cancelled',reason='Lower timeframe original has priority')
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(active),active['id']))
                active=None
        if active:
            active['revision']+=1
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(active),active['id']))
            for destination in added:enqueue(db,active,dict(kind='started',at=now,session=active['id'],destination=destination))
            if s['id']!=active['id']:
                s.update(status='superseded',next_due=None,superseded_by=active['id'])
                db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(s['id'],installation,s['user'],json.dumps(s)))
            return active
    if same:return same[0]  # Duplicate old alert never reopens a terminal watch.
    db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(s['id'],installation,s['user'],json.dumps(s)))
    enqueue(db,s,dict(kind='started',at=now,session=s['id']))
    return s


def enroll_automatic(store,root):
    from crypto_chase_policy import is_crypto
    if not enabled() or not is_crypto(root):return
    for person in people(store):
        if person.get('crypto_auto_chase'):enroll(store,root,person,automatic=True)


def asset_context(store,root):
    """Resolve only configured feeds; never guess an exchange or quote currency."""
    from managed_assets import for_timeframe
    context=dict(asset_key=root['symbol'],chase_symbol=root['symbol'])
    with activity_connection(store) as db:
        row=db.execute('SELECT payload FROM managed_config WHERE id=1').fetchone()
    for asset in json.loads(row[0]).get('assets',[]) if row else []:
        source=for_timeframe(asset,root['timeframe'])
        identity=lambda a:f"{a['provider']}:{a.get('exchange') or 'configured'}:{a.get('symbol') or a['id']}"
        if identity(source)!=root['symbol']:continue
        context['asset_key']=asset['id']
        # Higher originals can use their explicitly configured Kraken 1h feed.
        # Shorter confirmations stay on that exact venue and quote pair.
        lower_feed=for_timeframe(asset,'1h')
        if lower_feed['provider']=='kraken' and lower_feed.get('feed_confirmed'):
            context['chase_symbol']=identity(lower_feed)
        break
    return context


def upgrade_watches(store,now):
    """Adopt active crypto watches only; completed/entered history stays untouched."""
    from crypto_chase_policy import is_crypto, ORDER, POLICY, deadline
    with activity_connection(store) as db:
        rows=db.execute("SELECT installation,user_id,state FROM chasing_sessions WHERE state->>'status'='chasing' AND bot_runtime.chasing_is_owner(installation,user_id)").fetchall()
    # Save asset identities before collapsing legacy per-original sessions.
    for installation,user,state in rows:
        if is_crypto(state['root']) and not state.get('asset_key'):
            original=json.dumps(state)
            state=state|asset_context(store,state['root'])
            state['revision']+=1
            with activity_connection(store) as db:
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s AND state=%s::jsonb',(json.dumps(state),state['id'],original))
    with activity_connection(store) as db:
        rows=db.execute("SELECT installation,user_id,state FROM chasing_sessions WHERE state->>'status'='chasing' AND bot_runtime.chasing_is_owner(installation,user_id)").fetchall()
    groups={(str(i),str(u),s.get('asset_key',s['root']['symbol'])) for i,u,s in rows if is_crypto(s['root'])}
    for installation,user,symbol in groups:
        with activity_connection(store) as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(installation+user+symbol+':crypto-watch',))
            rows=db.execute("SELECT id,state FROM chasing_sessions WHERE installation=%s AND user_id=%s AND coalesce(state->>'asset_key',state->'root'->>'symbol')=%s AND state->>'status'='chasing' FOR UPDATE",(installation,user,symbol)).fetchall()
            states=[]
            for key,state in rows:
                if state.get('watch_policy')!=POLICY:
                    state.update(watch_policy=POLICY,destinations=['chasing'],watch_timeouts=None,
                                 expires_at=deadline(state['stage'],state['parent_at'],state.get('expires_at')),
                                 next_due=now,revision=state['revision']+1)
                if state.get('expires_at') is not None and now>=state['expires_at']:
                    state,events=step(state,now)
                    for event in events:enqueue(db,state,event)
                else:states.append(state)
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),key))
            if not states:continue
            winner=max(states,key=lambda s:(ORDER.index(s['stage']),s['started_at']))
            destinations=set(winner['destinations'])
            for state in states:
                destinations.update(state['destinations'])
                if state['id']==winner['id']:continue
                state.update(status='superseded',next_due=None,superseded_by=winner['id'],revision=state['revision']+1)
                if state.get('lookback',{}).get('status')=='pending':state['lookback'].update(status='cancelled',reason='Lower timeframe watch has priority')
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),state['id']))
            if set(winner['destinations'])!=destinations:
                winner.update(destinations=sorted(destinations),revision=winner['revision']+1)
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(winner),winner['id']))


def market_bars(root,now):
    """Initial live support: exact Kraken spot roots only. Other feeds stay unavailable."""
    from scanner import check_candles
    from shared_kraken import fetch as fetch_kraken
    symbol=root['symbol']
    if not symbol.startswith('kraken:Kraken:'):raise ValueError('Chasing live feed currently requires Kraken spot')
    asset=dict(symbol=symbol.split(':',2)[2],exchange='Kraken',feed_confirmed=True)
    from divergence_chasing import DURATIONS
    bars={}
    stage=root.get('_search_stage')
    for f in reversed(FRAMES):
        if stage:
            from crypto_chase_policy import lower
            if f in ('1m','4h') or not lower(f,stage):continue
        elif DURATIONS[f]>DURATIONS.get(root.get('timeframe','4h'),DURATIONS['1h']):continue
        bars[f]=check_candles(fetch_kraken(asset,f,now),now)
    if not stage and not bars['1m']:raise ValueError('Minute prices unavailable')
    return bars


def signals_for(root,bars,now=None):
    from shared_kraken import detect
    now=int(time.time()*1000) if now is None else now
    return [s for f,rows in bars.items() if f in CHASE_FRAMES for s in detect(rows,root['rules'],root['symbol'],f,now)]


def market(root,now):
    bars=market_bars(root,now);minute=bars.get('1m')
    return (minute[-1].close if minute else None,minute[-1].end if minute else None,signals_for(root,bars,now))


def history(root,now):
    from scanner import minimum_history
    bars=market_bars(root,now)
    for frame,rows in bars.items():
        if frame=='1m':continue
        if sum(b.end<=root['confirmed_at'] for b in rows)<minimum_history(root['rules']):
            raise ValueError('Historical indicator warm-up is unavailable')
    return bars['1m'],signals_for(root,bars,now)


def tick(store,now,fetch=market,clock=None,lookback_fetch=history):
    from postgres_store import assert_worker
    assert_worker(store)
    with activity_connection(store) as db:
        rows=db.execute("SELECT id,state FROM chasing_sessions WHERE bot_runtime.chasing_is_owner(installation,user_id) AND (state->>'next_due')::bigint<=%s ORDER BY (state->>'next_due')::bigint LIMIT 100",(now,)).fetchall()
    cache={}
    for key,snapshot in rows:
        checked_now=clock() if clock else now
        if snapshot['status']=='chasing' and snapshot.get('expires_at') is not None and checked_now>=snapshot['expires_at']:
            # Expiry is a clock rule and must work even during a provider outage.
            with activity_connection(store) as db:
                row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(key,)).fetchone()
                if not row or row[0]['revision']!=snapshot['revision']:continue
                state,events=step(row[0],checked_now)
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),key))
                for event in events:enqueue(db,state,event)
            continue
        if snapshot.get('lookback',{}).get('status')=='pending':
            from chase_lookback import replay,finish
            try:
                if snapshot['chain'] and not snapshot.get('watch_policy'):
                    result=dict(status='found',from_at=snapshot['root']['confirmed_at'],through_at=snapshot['parent_at'],signal=snapshot['chain'][0],recorded=True)
                elif snapshot['lookback']['requested_at']-snapshot['root']['confirmed_at']>12*3600000:
                    result=replay(snapshot,snapshot['lookback']['requested_at'],[],[])
                else:
                    root=snapshot['root']
                    if snapshot.get('chase_symbol'):root=root|dict(symbol=snapshot['chase_symbol'])
                    minutes,signals=lookback_fetch(root,checked_now)
                    result=replay(snapshot,snapshot['lookback']['requested_at'],minutes,signals)
            except (ValueError,OSError,KeyError,TypeError,IndexError):
                result=dict(status='incomplete',reason='Provider history is unavailable; no missed-signal conclusion can be made',from_at=snapshot['root']['confirmed_at'],through_at=None)
            checked_now=clock() if clock else now
            with activity_connection(store) as db:
                row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(key,)).fetchone()
                if not row or row[0]['revision']!=snapshot['revision']:continue
                state,event=finish(row[0],result,checked_now)
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),key))
                enqueue(db,state,event)
                if row[0]['status']=='chasing' and state.get('watch_policy'):
                    from crypto_chase_policy import lower
                    stage=row[0]['stage']
                    for finding in state['chain']:
                        if lower(finding['timeframe'],stage):
                            enqueue(db,state,dict(kind='chase',at=checked_now,session=key,signal=finding,retrospective=True))
                            stage=finding['timeframe']
                    if state['status']=='expired':enqueue(db,state,dict(kind='expired',at=checked_now,session=key))
            continue
        try:
            root=snapshot['root']
            if snapshot.get('watch_policy') and snapshot['status']=='chasing':root=root|dict(_search_stage=snapshot['stage'],symbol=snapshot.get('chase_symbol',root['symbol']))
            feed=(root['symbol'],root['timeframe'],root.get('_search_stage'),json.dumps(root['rules'],sort_keys=True))
            if feed not in cache:cache[feed]=fetch(root,checked_now)
            price,price_at,signals=cache[feed]
            checked_now=clock() if clock else now
        except (ValueError, OSError, KeyError, TypeError, IndexError):
            checked_now=clock() if clock else now
            with activity_connection(store) as db:
                row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(key,)).fetchone()
                if not row or row[0]['revision']!=snapshot['revision']:continue
                state=row[0];state['next_due']=checked_now+60000
                if state['status']=='chasing' and state.get('expires_at') is not None:state['next_due']=min(state['next_due'],state['expires_at'])
                if state.get('position') and not state.get('data_delayed'):
                    state['data_delayed']=True
                    enqueue(db,state,dict(kind='position_data_delayed',at=checked_now,session=key))
                state['revision']+=1
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(state),key))
            store.log('Chasing data unavailable',f"{root['symbol']}: awaiting supported, fresh closed candles")
            continue
        with activity_connection(store) as db:
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
    if kind=='chase':
        if s.get('watch_policy'):
            final=event['signal']['timeframe']=='5m'
            retrospective='LOOKBACK · historical finding reported now. ' if event.get('retrospective') else ''
            return head+retrospective+f"Same-direction {event['signal']['timeframe']} divergence · {event['signal']['symbol']}. "+('5m reached: chasing complete; entry unconfirmed.' if final else 'The watch continues on lower timeframes toward 5m.')
        return head+f"Same-direction {event['signal']['timeframe']} divergence. First eligible divergence: chasing stopped; entry unconfirmed."
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
    if kind=='lookback_result':
        from zoneinfo import ZoneInfo
        result=event['result']
        def when(at):return datetime.fromtimestamp(at/1000,ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y %H:%M %Z')
        prefix=head+'LOOKBACK · retrospective review since '+when(result['from_at'])+'\n'
        if result['status']=='found':
            signal=result['signal']
            suffix='Chasing is complete; entry is unconfirmed.' if s['status']=='complete_first' else 'The prior session state is unchanged; this does not reopen it.'
            return prefix+f"Same-direction {signal['timeframe']} divergence available at {when(signal['confirmed_at'])}.\nThis is a historical finding reported now. {suffix}"
        if result['status']=='found_intermediate':
            frames=', '.join(c['timeframe'] for c in result['findings'])
            suffix=f"Watching below {s['stage']} toward 5m." if s['status']=='chasing' else f"Current watch status: {s['status']}."
            return prefix+f"Historical same-direction divergences found: {frames}. {suffix}"
        if result['status']=='stopped_favorable':return prefix+'The favorable-move stop was reached at '+when(result['through_at'])+' before an eligible second divergence. Chasing is stopped.'
        if result['status']=='expired':return prefix+'The configured chase deadline was reached at '+when(result['through_at'])+'. This does not restart chasing.'
        if result['status']=='incomplete':return prefix+result['reason']+'. Lookback is incomplete; no absence of divergence is claimed.'
        return prefix+'No eligible second divergence at the scheduled five-minute checks through '+when(result['through_at'])+'. Live chasing continues if this session is still active.'
    started='Chasing started. Watching lower timeframes toward 5m.' if s.get('watch_policy') else 'Chasing started. Only I’m in confirms your entry.'
    return head+dict(started=started,return_to_close='Price returned to the original close. Chasing continues.',expired='Chase reached its time limit. Watching stopped.')[kind]


def deliver_pending(store,token):
    from postgres_store import assert_worker, WorkerOwned
    from telegram_delivery import deliver
    from telegram_personal import prepare_message, bind_message
    from telegram_checklist import api, bot_key
    recipients={str(p['user_id']):p for p in people(store)}
    with activity_connection(store) as db:
        rows=db.execute("SELECT o.id,o.event,s.state FROM chasing_outbox o JOIN chasing_sessions s ON s.id=o.session_id WHERE o.status='pending' AND o.retry_at<=%s ORDER BY o.event->>'at' LIMIT 100",(int(time.time()*1000),)).fetchall()
    for key,event,s in rows:
        person=recipients.get(s['user']);route=event.get('destination') or ('live_positions' if event['kind'] in ('opposite','entered','position_reference','position_extended','position_trail','exit_signal','reported_exit','position_data_delayed','position_data_resumed') else 'chasing')
        stale=event['kind'] in ('started','return_to_close','chase') and s['status']!='chasing' and not(event['kind']=='chase' and s['status']=='complete_first')
        if s.get('watch_policy') and route in ('chasing','crypto_auto_chase') and event['kind'] in ('started','chase','expired') and route not in s.get('destinations',[]):stale=True
        if route=='live_positions' and s.get('position',{}).get('status')=='closed' and event['kind']!='reported_exit':stale=True
        if not person or not person.get(route) or not person['routes'].get(route) or stale:
            with activity_connection(store) as db:db.execute("UPDATE chasing_outbox SET status='suppressed' WHERE id=%s",(key,))
            continue
        if event['kind']=='return_to_close':
            with activity_connection(store) as db:
                slot=db.execute('''INSERT INTO chasing_price_slots VALUES(%s,%s,extract(epoch FROM clock_timestamp()))
                  ON CONFLICT(user_id) DO UPDATE SET event_id=excluded.event_id,reserved_at=excluded.reserved_at
                  WHERE chasing_price_slots.event_id=excluded.event_id OR chasing_price_slots.reserved_at<=excluded.reserved_at-60
                  RETURNING user_id''',(s['user'],key)).fetchone()
                if not slot:db.execute("UPDATE chasing_outbox SET status='suppressed' WHERE id=%s",(key,))
            if not slot:continue
        assert_worker(store)
        chat=person['routes'][route]
        with activity_connection(store) as db:
            db.execute('UPDATE chasing_outbox SET chat=coalesce(chat,%s),telegram_user_id=coalesce(telegram_user_id,%s),bot=coalesce(bot,%s) WHERE id=%s',(chat,person['telegram_user_id'],bot_key(token),key))
            chat,owner=db.execute('SELECT chat,telegram_user_id FROM chasing_outbox WHERE id=%s',(key,)).fetchone()
        def send(token,chat,text,**kwargs):
            assert_worker(store)
            item=prepare_message(store,token,chat,text,[],owner)
            if route!='crypto_auto_chase' and s['status'] in ('chasing','complete_first') and s['root']['timeframe'] in ('1h','4h'):
                item.extra_keyboard=[[dict(text="I'm in",callback_data='chase:enter:'+s['id'])]]
                item.extra_keyboard+=chase_buttons(s['id'])
            elif s.get('position') and s['position']['status']!='closed':
                item.extra_keyboard=[[dict(text="I've exited",callback_data='chase:exit:'+s['id'])]]
            message=api(token,'sendMessage',dict(chat_id=chat,text=text,reply_markup=item.keyboard(),disable_notification=kwargs.get('silent',False)))
            bind_message(store,item,message)
            with activity_connection(store) as db:
                db.execute('UPDATE chasing_outbox SET message_id=%s,dismissal_id=%s WHERE id=%s',(message['message_id'],item.key,key))
            return message['message_id']
        try:
            deliver(store,token,chat,text_for(s,event),key,send)
        except WorkerOwned:
            raise
        except RuntimeError:
            with activity_connection(store) as db:db.execute('UPDATE chasing_outbox SET retry_at=%s WHERE id=%s',(int(time.time()*1000)+60000,key))
            store.log('Chasing delivery delayed','Personal delivery will retry; other recipients continue.')
            continue
        with activity_connection(store) as db:db.execute("UPDATE chasing_outbox SET status='sent' WHERE id=%s",(key,))


def callback(store,token,query):
    from telegram_checklist import api,bot_key
    from postgres_store import assert_worker
    data=query.get('data','');parts=data.split(':',2)
    if len(parts)!=3 or parts[0]!='chase' or parts[1] not in ('enter','exit','start','lookback'):return
    action=parts[1];key=parts[2];leaving=action=='exit'
    message=query.get('message') or {};user=query.get('from',{}).get('id')
    assert_worker(store)
    answer='Entry unavailable for this message.'
    with activity_connection(store) as db:
        binding=db.execute('SELECT 1 FROM chasing_outbox o JOIN telegram_personal_messages m ON m.id=o.dismissal_id WHERE o.session_id=%s AND o.bot=%s AND o.chat=%s AND o.message_id=%s AND o.telegram_user_id=%s AND m.state::jsonb->>\'phase\'=\'active\'',
                           (key,bot_key(token),str(message.get('chat',{}).get('id')),message.get('message_id'),user)).fetchone()
        if not binding:
            binding=db.execute('''SELECT 1 FROM telegram_personal_messages m
              CROSS JOIN LATERAL jsonb_array_elements(coalesce(m.state::jsonb->'extra_keyboard','[]')) AS r(value)
              CROSS JOIN LATERAL jsonb_array_elements(r.value) AS b(value)
              WHERE m.bot=%s AND m.state::jsonb->>'chat_id'=%s AND (m.state::jsonb->>'message_id')::bigint=%s
              AND (m.state::jsonb->>'user_id')::bigint=%s AND m.state::jsonb->>'phase'='active'
              AND b.value->>'callback_data'=%s''',(bot_key(token),str(message.get('chat',{}).get('id')),message.get('message_id'),user,data)).fetchone()
        if binding:
            row=db.execute('SELECT state FROM chasing_sessions WHERE id=%s AND bot_runtime.chasing_is_owner(installation,user_id) FOR UPDATE',(key,)).fetchone()
            if not row:
                api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text=answer))
                return
            s=row[0]
            while s.get('superseded_by'):
                target=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(s['superseded_by'],)).fetchone()
                if not target or target[0]['root']['direction']!=s['root']['direction']:
                    api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text='This watch was replaced. Use the current alert.'))
                    return
                s=target[0]
                key=s['id']
            now=int(time.time()*1000);events=[]
            if s['status']=='chasing' and s.get('expires_at') is not None and now>=s['expires_at']:
                s,expired_events=step(s,now)
                db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(s),key))
                for event in expired_events:enqueue(db,s,event)
                if action in ('enter','start'):
                    api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text='This watch has reached its time limit. It was not restarted.'))
                    return
            if action=='enter' and s.get('entered_session'):
                entered=db.execute('SELECT state FROM chasing_sessions WHERE id=%s AND user_id=%s AND bot_runtime.chasing_is_owner(installation,user_id)',(s['entered_session'],s['user'])).fetchone()
                answer=('This position is already closed; this alert cannot reopen it.' if entered and entered[0].get('position',{}).get('status')=='closed'
                        else 'Entry was already recorded. Use the existing position card.')
                api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text=answer))
                return
            if action=='lookback':
                from chase_lookback import request
                import hashlib
                request_id=hashlib.sha256(query['id'].encode()).hexdigest()[:16]
                s,answer=request(s,now,request_id)
            elif action=='start' and s.get('watch_policy') and s['status']=='chasing':
                destinations=set(s.get('destinations',[]))
                if 'chasing' not in destinations:
                    s['destinations']=sorted(destinations|{'chasing'})
                    s['revision']+=1
                    events.append(dict(kind='started',at=now,session=s['id'],destination='chasing'))
                answer='Using the existing asset watch. Its timeframe and deadline are unchanged.'
            elif action=='start':
                if s['status']=='chasing' and s.get('lookback',{}).get('status')!='pending':
                    if not s.get('manual_live_from_at'):
                        s.update(checked_at=now,last_price_at=now,next_due=now+300000,manual_live_from_at=now,revision=s['revision']+1)
                    answer='Chasing live from your click; use Chase + look back to review the missed interval.'
                else:answer='This chase is already complete, stopped, entered, or reviewing history; it was not restarted.'
            elif action=='enter' and s['root']['timeframe'] not in ('1h','4h'):
                answer='Position monitoring supports 1h and 4h originals. This watch continues toward 5m.'
            elif action=='enter' and s.get('watch_policy') and s['status']=='chasing' and 'crypto_auto_chase' in s.get('destinations',[]):
                # Reported entry owns position monitoring; the automatic group
                # continues its original watch until 5m or expiry.
                import hashlib
                from copy import deepcopy
                position_key=hashlib.sha256((s['id']+':reported-position').encode()).hexdigest()[:32]
                existing=db.execute('SELECT state FROM chasing_sessions WHERE id=%s FOR UPDATE',(position_key,)).fetchone()
                if existing:
                    s=existing[0]
                else:
                    position=deepcopy(s);position['id']=position_key
                    position['destinations']=['chasing']
                    position,events=step(position,now,enter=True)
                    if position.get('entry_at') is not None:
                        s['destinations']=[d for d in s['destinations'] if d!='chasing']
                        s.update(entered_session=position_key,revision=s['revision']+1)
                        db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(s),key))
                        db.execute('INSERT INTO chasing_sessions SELECT %s,installation,%s,%s FROM runtime_control WHERE id=1',(position_key,s['user'],json.dumps(position)))
                        s=position
                    else:s=position
                key=s['id']
            else:s,events=step(s,now,enter=not leaving,leave=leaving)
            db.execute('UPDATE chasing_sessions SET state=%s WHERE id=%s',(json.dumps(s),key))
            for event in events:enqueue(db,s,event)
            if leaving and s['status']=='reported_exited':answer='Exit reported. Monitoring stopped.'
            elif action=='enter' and s['entry_at'] is not None:answer='Entry recorded. Minute monitoring is starting; awaiting a fresh market reference.' if events else 'Entry was already recorded.'
    api(token,'answerCallbackQuery',dict(callback_query_id=query['id'],text=answer))


def worker(store,token,dry_run,stop):
    from local_settings import delivery_dry_run
    if not enabled():return
    upgrade_watches(store,int(time.time()*1000))
    while not stop.is_set():
        if not delivery_dry_run(store,dry_run):
            tick(store,int(time.time()*1000),clock=lambda:int(time.time()*1000))
            deliver_pending(store,token)
        stop.wait(1)
