"""Alert-only position rules on fresh closed-minute prices; no broker actions."""
from copy import deepcopy
from decimal import Decimal


def number(value):
    x=Decimal(str(value))
    if not x.is_finite() or x<=0:raise ValueError('Invalid position price')
    return x


def start(frame,at,widen_mode='widen'):
    if frame not in ('1h','4h'):raise ValueError('Position rules require original 1h or 4h timeframe')
    if widen_mode not in ('widen','keep_level'):raise ValueError('Unknown trailing transition')
    return dict(status='awaiting_reference',requested_at=at,frame=frame,entry_price=None,
                entry_price_at=None,last_price_at=None,best=None,additional_divergence=None,
                trail_pct=None,trail_stop=None,wide_stage=False,widen_mode=widen_mode,
                stop_pct=.5 if frame=='1h' else 1.,target_pct=.5 if frame=='1h' else 1.)


def update(position,now,price,price_at,direction,signals=(),symbol=None):
    p=deepcopy(position);events=[]
    if p['status']=='closed':return p,events
    if price is None or price_at is None or not p['requested_at']<=price_at<=now or now-price_at>120000:
        return p,events
    if p['last_price_at'] is not None and price_at<=p['last_price_at']:return p,events
    value=number(price);d=1 if direction=='bullish' else -1
    if direction not in ('bullish','bearish'):raise ValueError('Invalid position direction')
    previous_at=p['last_price_at'] if p['last_price_at'] is not None else p['requested_at']
    p.update(last_price_at=price_at,last_price=float(value))
    # Explicitly a post-click market reference, never an inferred broker fill.
    if p['entry_price'] is None:
        p.update(status='monitoring',entry_price=float(value),entry_price_at=price_at,best=float(value))
        events.append(dict(kind='position_reference',price=float(value),price_at=price_at))
    if p['status']=='exit_alerted':return p,events
    entry=number(p['entry_price']);move=d*(value/entry-1)*100
    same=[s for s in signals if s.get('symbol')==symbol and s.get('direction')==direction
          and previous_at<s['confirmed_at']<=price_at
          and s['timeframe'] in ('5m','15m','30m','1h','4h')
          and (p['frame']=='4h' or s['timeframe']!='4h')]
    # Conservative close-sampled ordering: an existing exit wins over a newly
    # available divergence or a change to trailing width on this same observation.
    reason=None
    if p['trail_stop'] is not None and d*(value-number(p['trail_stop']))<=0:reason='trailing_stop'
    elif p['trail_stop'] is None and move<=-Decimal(str(p['stop_pct'])):reason='loss_stop'
    elif p['additional_divergence'] is None and move>=Decimal(str(p['target_pct'])):reason='profit_target'
    if reason:
        p.update(status='exit_alerted',exit_reason=reason,exit_price=float(value),exit_price_at=price_at)
        events.append(dict(kind='exit_signal',reason=reason,price=float(value),price_at=price_at,
                           reference=p['entry_price'],move_pct=float(move)))
        return p,events
    if same and p['additional_divergence'] is None:
        chosen=min(same,key=lambda s:(s['confirmed_at'],('5m','15m','30m','1h','4h').index(s['timeframe'])))
        p['additional_divergence']=deepcopy(chosen)
        # Begin the trail's high/low water mark at the additional divergence,
        # rather than activating from a historical pre-extension extreme.
        p['best']=float(value)
        events.append(dict(kind='position_extended',signal=deepcopy(chosen)))
    if p['additional_divergence'] is not None:
        p['best']=float(max(number(p['best']),value) if d==1 else min(number(p['best']),value))
        width=p['trail_pct']
        if width is None and move>=1:width=.5
        if p['frame']=='4h' and move>=2:p['wide_stage']=True
        if p['wide_stage']:width=1.
        if width is not None:
            stop=number(p['best'])*(1-Decimal(d)*Decimal(str(width))/100)
            old=p['trail_stop'];changed=p['trail_pct']!=width
            if old is not None and (not changed or p['widen_mode']=='keep_level'):
                stop=max(number(old),stop) if d==1 else min(number(old),stop)
            p.update(trail_pct=width,trail_stop=float(stop))
            if changed:events.append(dict(kind='position_trail',trail_pct=width,stop=float(stop),price_at=price_at))
    return p,events
