"""Informational NEAR RSI context, populated only by the normal scanner."""
from calendar import monthrange
from datetime import datetime, timezone
import copy
import hashlib
from signal_contract import canonical

FRAMES = ('1h', '4h', 'daily', 'weekly', 'monthly')
PERIODS = {'1h':3600000, '4h':14400000, 'daily':86400000, 'weekly':604800000}


def target(asset):
    return asset.get('id')=='NEARUSD' and asset.get('symbol')=='NEAR/USD' and asset.get('market')=='crypto'


def source(asset):
    return f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"


def advance(at, frame, count=1):
    if frame!='monthly':return at+PERIODS[frame]*count
    dt=datetime.fromtimestamp(at/1000, timezone.utc)
    month=dt.year*12+dt.month-1+count;year,month=divmod(month,12);month+=1
    return int(dt.replace(year=year,month=month,day=min(dt.day,monthrange(year,month)[1])).timestamp()*1000)


def configure(config):
    """Pure preview transformation; existing managed configuration is never auto-overwritten."""
    result=copy.deepcopy(config)
    assets=[a for a in result['assets'] if target(a)]
    if len(assets)!=1:raise ValueError('Expected one NEAR/USD crypto asset')
    asset=assets[0];asset['enabled']=True
    asset['timeframes']=list(dict.fromkeys(asset.get('timeframes',result['timeframes'])+['1h','5m']))
    asset['scan_strategies']=list(dict.fromkeys(asset.get('scan_strategies',['rsi','pin-bars','rsi-pb','ma-cross'])+['rsi']))
    asset.setdefault('timeframe_providers',{})['5m']='kraken'
    asset['timeframe_providers']['1h']='kraken'
    return result


def refresh(store, asset, frame, candles, findings, rules, now):
    if not target(asset) or frame not in FRAMES or not candles:return
    positions={c.start:i for i,c in enumerate(candles)}
    matches={}
    for finding in findings:
        i=positions.get(finding['pivot2'])
        if i is None:continue
        # The existing strategy first detects provisionally at P2 close. Later
        # structural confirmation must not restart that finding's lifetime.
        at=candles[i].end;expires=advance(at,frame,2 if frame in ('1h','4h') else 1)
        if not at<=now<expires:continue
        following=candles[i+1:i+1+rules['pivot_right']]
        # A provisional pivot invalidated by a later close must leave the cache.
        if any(c.close<=candles[i].close if finding['direction']=='bullish' else c.close>=candles[i].close for c in following):continue
        key=hashlib.sha256(canonical([source(asset),rules['skill_hash'],frame,finding['direction'],finding['pivot2']]).encode()).hexdigest()
        value=dict(finding_id=key,source=source(asset),symbol=asset['symbol'],timeframe=frame,
            direction=finding['direction'],strategy_version=rules['skill_hash'],
            signal_status=finding.get('signal_status','confirmed'),detection_candle_closed_at=at,
            structural_confirmation_at=finding['confirmed_at'] if finding.get('signal_status')!='provisional' else None,
            pivot_candle_started_at=finding['pivot2'],pivot_candle_closed_at=candles[i].end,
            expires_at=expires)
        # Confirmation replaces the provisional stage for one pivot, never doubles it.
        if key not in matches or value['signal_status']=='confirmed':matches[key]=value
    payload=dict(analyzed_at=now,last_closed_at=candles[-1].end,
        next_close_at=advance(candles[-1].end,frame),
        candle_closes=[c.end for c in candles[-2:]],matches=sorted(matches.values(),key=lambda m:m['finding_id']))
    with store.connect() as db:
        db.execute('''INSERT INTO rsi_context_cache AS old(asset,source,timeframe,strategy_version,last_closed_at,payload)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(asset,source,timeframe,strategy_version)
            DO UPDATE SET last_closed_at=excluded.last_closed_at,payload=excluded.payload
            WHERE excluded.last_closed_at>=old.last_closed_at''',
            (asset['id'],source(asset),frame,rules['skill_hash'],candles[-1].end,canonical(payload)))


def read(store, asset, rules, now):
    """No strategy calls or provider requests; missing context never gates a 5m signal."""
    from managed_assets import for_timeframe
    result=dict(context_version=1,informational_only=True,evaluated_at=now,
        freshness_basis='detection_candle_close',candle_reference='latest_analyzed_closed_candle',matches=[],coverage=[])
    frames=[f for f in asset.get('timeframes',[]) if f in FRAMES]
    with store.connect() as db:
        for frame in frames:
            identity=source(for_timeframe(asset,frame))
            row=db.execute('SELECT payload FROM rsi_context_cache WHERE asset=%s AND source=%s AND timeframe=%s AND strategy_version=%s',
                (asset['id'],identity,frame,rules['skill_hash'])).fetchone()
            payload=row[0] if row else None
            state='missing' if not payload else 'pending_analysis' if now>=payload['next_close_at'] or now<payload['analyzed_at'] else 'ready'
            result['coverage'].append(dict(timeframe=frame,source=identity,status=state,
                analyzed_at=payload['analyzed_at'] if payload else None,last_closed_at=payload['last_closed_at'] if payload else None))
            if state!='ready':continue
            closes=payload['candle_closes']
            for match in payload['matches']:
                at=match['detection_candle_closed_at']
                if not at<=now<match['expires_at'] or at not in closes:continue
                label='latest' if at==closes[-1] else 'previous'
                result['matches'].append(match|dict(candle_position=label,cache_analyzed_at=payload['analyzed_at']))
    return result
