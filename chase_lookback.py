"""Read-only causal replay from the original alert; never reports an entry."""
from copy import deepcopy
from divergence_chasing import create,step,PRE_ENTRY_MS


def replay(session,until,minutes,signals):
    origin=session['started_at'] if session.get('watch_policy') else session['root']['confirmed_at']
    if until<origin:raise ValueError('Lookback cannot precede the original alert')
    if until-origin>12*3600000:
        return dict(status='incomplete',reason='The alert exceeds the supported 12-hour provider-history window',from_at=origin,through_at=None)
    cutoff=until//60000*60000
    bars={b.start:b for b in minutes if b.end<=cutoff}
    if len(bars)!=len([b for b in minutes if b.end<=cutoff]):raise ValueError('Duplicate lookback minutes')
    state=create(session['user'],session['root'],origin,session['stop_pct'],expires_at=session.get('expires_at'),
                 watch_policy=session.get('watch_policy'),watch_timeouts=session.get('watch_timeouts'))
    if session.get('chase_symbol'):state['chase_symbol']=session['chase_symbol']
    for at in range(origin//60000*60000,cutoff,60000):
        bar=bars.get(at)
        if bar is None or bar.end!=at+60000:
            return dict(status='incomplete',reason='A required historical minute is unavailable; a missed signal cannot be ruled out',from_at=origin,through_at=at)
        if bar.end<state['next_due']:continue
        state,events=step(state,bar.end,price=bar.close,price_at=bar.end,signals=signals)
        if state['status']!='chasing':
            event=next(e for e in reversed(events) if e['kind'] in ('chase','favorable_stop','expired'))
            outcome={'chase':'found','favorable_stop':'stopped_favorable','expired':'expired'}[event['kind']]
            return dict(status=outcome,from_at=origin,through_at=bar.end,
                        signal=deepcopy(event.get('signal')),event=event,findings=deepcopy(state['chain']),replayed_state=state)
    return dict(status='found_intermediate' if state['chain'] else 'none',from_at=origin,through_at=cutoff,
                findings=deepcopy(state['chain']),replayed_state=state)


def request(session,now,request_id):
    """Queue once per Telegram callback, preserving terminal/position state."""
    state=deepcopy(session)
    if state['entry_at'] is not None:return state,'Entry already reported; lookback cannot change this position.'
    if state['status']=='stopped_manual':return state,'You stopped this chase. It was not restarted.'
    old=state.get('lookback',{})
    if old.get('request_id')==request_id:return state,'This lookback request was already recorded.'
    if old.get('status')=='pending':return state,'Lookback is already queued.'
    state['lookback']=dict(status='pending',request_id=request_id,requested_at=now,
                           previous_next_due=state['next_due'])
    state.update(next_due=now,revision=state['revision']+1)
    return state,'Looking back from the original alert. Results will appear in Chasing.'


def finish(session,result,now):
    state=deepcopy(session);job=state['lookback']
    # Adopt verified replay results only for a still-live, unentered chase.
    # A retrospective lookup never revives a stopped session.
    if state['status']=='chasing' and result.get('replayed_state'):
        simulated=result['replayed_state']
        if session.get('watch_policy'):
            from crypto_chase_policy import lower
            # Replay can only move down, never rewind a lower live stage. Keep
            # the historical deadline rather than granting a new timer now.
            if lower(simulated['stage'],state['stage']):
                state.update(stage=simulated['stage'],parent_at=simulated['parent_at'])
                known={(c['symbol'],c['timeframe'],c['pivot1'],c['pivot2']) for c in state['chain']}
                state['chain'] += [deepcopy(c) for c in simulated['chain'] if (c['symbol'],c['timeframe'],c['pivot1'],c['pivot2']) not in known]
                deadlines=[v for v in (state.get('expires_at'),simulated.get('expires_at')) if v is not None]
                state['expires_at']=min(deadlines) if deadlines else None
                if simulated['status']=='complete_first':state.update(status='complete_first',next_due=None)
            if state['status']=='chasing' and state.get('expires_at') is not None and now>=state['expires_at']:
                state.update(status='expired',next_due=None)
            simulated=None
        if simulated is not None:
            for field in ('status','stage','parent_at','checked_at','last_price_at','last_signed','last_return_at','chain'):
                state[field]=deepcopy(simulated[field])
            state['next_due']=max(now,simulated['next_due']) if simulated['next_due'] is not None else None
        elif state['status']=='chasing':
            state['next_due']=now
    else:
        state['next_due']=job['previous_next_due']
        if state['status']=='chasing' and state['next_due'] is not None:
            interval=60000 if state.get('watch_policy') else PRE_ENTRY_MS
            state['next_due']=max(now+interval,state['next_due'])
            if state.get('expires_at') is not None:state['next_due']=min(state['next_due'],state['expires_at'])
    job.update(status='complete',result={k:v for k,v in result.items() if k!='replayed_state'},completed_at=now)
    state['revision']+=1
    event=dict(kind='lookback_result',at=now,session=state['id'],request_id=job['request_id'],result=job['result'])
    return state,event
