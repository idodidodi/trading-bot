"""Side-effect-free discovery of published findings, never an execution feed."""
import base64
from collections import Counter
import hashlib
import hmac
import json
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from signal_contract import canonical

CATEGORIES = ('Stocks & ETFs', 'Forex', 'Futures', 'Crypto', 'Other assets')
STRATEGIES = ('rsi', 'pin-bars', 'bb-pin-bars', 'rsi-pb', 'momentum', 'chase-4h-5m')
FRAMES = ('5m', '15m', '30m', '1h', '4h', 'daily', 'weekly', 'monthly')
FILTERS = {'marketCategory', 'strategy', 'strategy_version', 'timeframe', 'source', 'symbol', 'direction'}
MAX_ROWS = 10000
SNAPSHOT_MS = 60000
FEED_AGE_MS = 300000


class DiscoveryError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def integer(value, low, high):
    if isinstance(value, bool):raise ValueError('Invalid integer')
    if not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]+', str(value)):raise ValueError('Invalid integer')
    value = int(value)
    if not low <= value <= high:raise ValueError('Integer outside allowed range')
    return value


def selection(value):
    if not isinstance(value, dict) or set(value)-FILTERS:raise ValueError('Unknown discovery filters')
    result = {}
    for key, values in value.items():
        if not isinstance(values, list) or len(values)>100 or any(not isinstance(v,str) or len(v)>200 for v in values):raise ValueError('Invalid discovery filter')
        values = sorted(set(v for v in values if v))
        allowed = {'marketCategory':CATEGORIES, 'strategy':STRATEGIES, 'timeframe':FRAMES, 'direction':('bullish','bearish')}.get(key)
        if allowed and set(values)-set(allowed):raise ValueError('Unsupported discovery filter value')
        if values:result[key] = values
    return result


def grant(value):
    if not isinstance(value,dict) or set(value)-{'enabled','filters','max_lookback_minutes'}:raise ValueError('Invalid discovery grant')
    if type(value.get('enabled')) is not bool:raise ValueError('Explicit enabled flag required')
    return dict(enabled=value['enabled'],filters=selection(value.get('filters',{})),
                max_lookback_minutes=integer(value.get('max_lookback_minutes',1440),1,10080))


def administer(store, project, value):
    permission = grant(value)
    with store.connect() as db:
        if not db.execute('UPDATE signal_subscriptions SET discovery=%s WHERE project=%s',(canonical(permission),project)).rowcount:
            raise ValueError('Unknown project')
    return dict(project=project,discovery=permission)


def category(asset):
    market=asset.get('market')
    if market=='crypto':return 'Crypto'
    if market=='forex':return 'Forex'
    if market=='futures':return 'Futures'
    if market in ('stock','etf','equity') or asset.get('exchange') in ('NASDAQ','NYSE','AMEX','NYSE ARCA'):return 'Stocks & ETFs'
    return 'Other assets'


def calendar(value):
    """Validate operator-reviewed UTC sessions; never infer a product calendar."""
    keys={'id','version','timezone','valid_from','valid_until','verified','evidence','continuous','sessions'}
    if not isinstance(value,dict) or set(value)-keys or value.get('verified') is not True:raise ValueError('Reviewed calendar required')
    for key in ('id','version','timezone','evidence'):
        if not isinstance(value.get(key),str) or not 1<=len(value[key])<=500:raise ValueError('Calendar provenance required')
    try:ZoneInfo(value['timezone'])
    except (ValueError,ZoneInfoNotFoundError):raise ValueError('Unknown calendar timezone') from None
    if any(type(value.get(k)) is not int for k in ('valid_from','valid_until')):raise ValueError('Calendar times must be integer UTC milliseconds')
    start=integer(value.get('valid_from'),0,10**15);end=integer(value.get('valid_until'),start+1,10**15)
    if type(value.get('continuous',False)) is not bool:raise ValueError('Invalid calendar policy')
    sessions=value.get('sessions',[])
    if not isinstance(sessions,list) or len(sessions)>10000:raise ValueError('Invalid sessions')
    if value.get('continuous') and sessions:raise ValueError('Continuous calendar cannot contain sessions')
    previous=start
    for interval in sessions:
        if not isinstance(interval,list) or len(interval)!=2:raise ValueError('Invalid interval')
        if any(type(t) is not int for t in interval):raise ValueError('Session times must be integer UTC milliseconds')
        opening=integer(interval[0],previous,end-1);closing=integer(interval[1],opening+1,end)
        previous=closing
    return value


def session(asset, policy, now):
    result=dict(status='unknown',reason='missing_calendar',evaluated_at=now,next_transition_at=None)
    if policy is None:return result
    try:calendar(policy)
    except ValueError:return result|dict(reason='invalid_calendar')
    result.update(calendar_id=policy['id'],calendar_version=policy['version'],timezone=policy['timezone'],valid_until=policy['valid_until'])
    if not policy['valid_from']<=now<policy['valid_until']:return result|dict(reason='expired_calendar')
    if policy.get('continuous'):
        if asset['marketCategory']!='Crypto':return result|dict(reason='continuous_policy_requires_crypto')
        return result|dict(status='open',reason='continuous',next_transition_at=policy['valid_until'])
    for opening,closing in policy.get('sessions',[]):
        if opening<=now<closing:return result|dict(status='open',reason='verified_session',next_transition_at=closing)
        if now<opening:return result|dict(status='closed',reason='outside_session',next_transition_at=opening)
    return result|dict(status='closed',reason='outside_session',next_transition_at=policy['valid_until'])


def match(item, filters):
    return all(item.get(key) in values for key,values in filters.items())


def unpack(value):
    return json.loads(value) if isinstance(value,str) else value


def source(asset):
    return f"{asset.get('provider','')}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset.get('id','')}"


def universe(config, status):
    """Latest scanner universe plus enabled static assets with missing coverage."""
    configured={a['id']:a for a in config.get('assets',[])}
    result={}
    for row in status.get('coverage',[]):
        if not isinstance(row,dict) or not all(row.get(k) for k in ('asset','provider','symbol','timeframe')):continue
        asset=configured.get(row['asset'])
        if asset and (not asset.get('enabled',True) or row['timeframe'] not in asset.get('timeframes',config.get('timeframes',[]))):continue
        if asset:
            expected=asset.get('timeframe_providers',{}).get(row['timeframe'],asset.get('provider'))
            if row['provider']!=expected and not (row['provider']=='alpaca' and row.get('fallback_from')==expected):continue
            if row['symbol']!=asset.get('symbol',asset['id']):continue
        strategies=list(row.get('scan_strategies',[]))
        if asset:strategies=[s for s in strategies if s in asset.get('scan_strategies',['rsi','pin-bars','rsi-pb'])]
        info={k:row.get(k) for k in ('provider','symbol','exchange','market','timeframe')}
        info.update(source=source(row),asset_id=row['asset'],marketCategory=category(row),strategies=strategies,
                    coverage=row,universe_origin='configured' if asset else 'latest_scanner_selection')
        result[(info['source'],info['timeframe'])]=info
    for asset in configured.values():
        if not asset.get('enabled',True):continue
        for frame in asset.get('timeframes',config.get('timeframes',[])):
            if any(i['asset_id']==asset['id'] and i['timeframe']==frame for i in result.values()):continue
            info={k:asset.get(k) for k in ('provider','symbol','exchange','market')}
            info['provider']=asset.get('timeframe_providers',{}).get(frame,info['provider'])
            info.update(source=source(info),asset_id=asset['id'],timeframe=frame,marketCategory=category(info),
                        strategies=asset.get('scan_strategies',['rsi','pin-bars','rsi-pb']),coverage={},universe_origin='configured')
            result[(info['source'],frame)]=info
    return result


def feed_status(info, strategy, now):
    row=info['coverage'];key={'pin-bars':'pin_bars','bb-pin-bars':'bb_pin_bars','rsi-pb':'rsi_pb'}.get(strategy)
    state=row.get(key,{}) if key else row
    checked=row.get('checked_at');closed=row.get('last_closed_at');due=row.get('next_close_at')
    valid_time=lambda t: type(t) is int and 0<t<=now
    reason='ready'
    if strategy not in info['strategies']:reason='strategy_not_enabled'
    elif not state or state.get('status') not in ('scanned','baseline set'):reason='not_ready'
    elif row.get('limited_history') or state.get('limited_history'):reason='insufficient_history'
    elif not valid_time(checked) or now-checked>FEED_AGE_MS:reason='stale_feed_check'
    elif not valid_time(closed):reason='missing_candle_time'
    elif type(due) is not int or due<=0 or now>due+FEED_AGE_MS:reason='overdue_candle_check'
    return dict(status=reason,checked_at=checked,last_closed_at=closed,next_check_at=due,max_check_age_ms=FEED_AGE_MS)


def signed(value, secret):
    raw=base64.urlsafe_b64encode(canonical(value).encode()).decode().rstrip('=')
    return raw+'.'+hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest()


def unsigned(token, secret):
    try:
        if len(token)>4096:raise ValueError()
        raw,signature=token.split('.')
        if not hmac.compare_digest(signature,hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest()):raise ValueError()
        return json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
    except (ValueError,TypeError,UnicodeError):raise ValueError('Invalid discovery page token') from None


def discover(store, token, params, secret, *, route='search', signal_id=None, now=None):
    now=int(time.time()*1000) if now is None else now
    if set(params)-(FILTERS|{'max_age_minutes','limit','page_token'}):raise ValueError('Unknown discovery parameter')
    def single(key,default):
        values=params.get(key,[default])
        if len(values)!=1:raise ValueError('Repeated scalar parameter')
        return values[0]
    requested=selection({k:v for k,v in params.items() if k in FILTERS})
    age=integer(single('max_age_minutes','60'),1,10080)
    limit=integer(single('limit','100'),1,200)
    page_token=single('page_token','')
    if route!='search' and page_token:raise ValueError('Pagination only applies to search')
    if signal_id is not None and not re.fullmatch('[a-f0-9]{64}',signal_id):raise ValueError('Invalid signal identity')
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        row=db.execute('SELECT project,discovery FROM signal_subscriptions WHERE token_hash=%s AND enabled=true',
                       (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:raise PermissionError()
        project,permission=row
        if not permission.get('enabled'):raise DiscoveryError(403,'discovery_not_granted')
        permission=grant(permission)
        if 'max_age_minutes' not in params:age=min(age,permission['max_lookback_minutes'])
        if age>permission['max_lookback_minutes']:raise ValueError('Lookback exceeds discovery grant')
        high=db.execute('SELECT value FROM signal_stream_clock WHERE id=1').fetchone()[0]
        config_row=db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        status_row=db.execute('SELECT payload FROM scanner_status WHERE id=1').fetchone()
        config=unpack(config_row[2]) if config_row else {}
        status=unpack(status_row[0]) if status_row else {}
        configuration_applied=bool(config_row and config_row[0]==config_row[1])
        if not configuration_applied:status=dict(coverage=[])
        calendars=dict(db.execute('SELECT source,payload FROM signal_discovery_calendars'))
        # Resolve lifecycle globally before any request filtering. A bounded result
        # fails explicitly rather than silently omitting findings from discovery.
        events=[r[0] for r in db.execute('SELECT payload FROM (SELECT DISTINCT ON(signal_id) payload,cursor FROM signal_events WHERE cursor<=%s ORDER BY signal_id,cursor DESC) latest ORDER BY cursor DESC LIMIT %s',(high,MAX_ROWS+1))]
    if len(events)>MAX_ROWS:raise DiscoveryError(503,'discovery_capacity_exceeded')
    revision=hashlib.sha256(canonical([project,permission,high,config_row,status,calendars]).encode()).hexdigest()
    query_hash=hashlib.sha256(canonical([requested,age,limit]).encode()).hexdigest()
    offset=0;as_of=now
    if page_token:
        cursor=unsigned(page_token,secret)
        if not isinstance(cursor,dict) or set(cursor)!={'revision','query','as_of','offset'}:raise ValueError('Invalid discovery page token')
        as_of=integer(cursor['as_of'],0,10**15);offset=integer(cursor['offset'],0,MAX_ROWS)
        if not 0<=now-as_of<SNAPSHOT_MS:raise DiscoveryError(410,'snapshot_expired')
        if cursor['revision']!=revision or cursor['query']!=query_hash:raise DiscoveryError(409,'snapshot_changed')
    assets=universe(config,status);items=[];excluded=Counter();catalogue=[]
    scope=permission['filters']
    # Only metadata visible under both grant and requested filters is returned.
    for info in assets.values():
        for strategy in info['strategies']:
            if strategy not in STRATEGIES:continue
            flat=info|dict(strategy=strategy)
            metadata_filters=lambda f:{k:v for k,v in f.items() if k not in ('direction','strategy_version')}
            if not match(flat,metadata_filters(scope)) or not match(flat,metadata_filters(requested)):continue
            current=session(info,calendars.get(info['source']),now)
            coverage=feed_status(info,strategy,now)
            catalogue.append({k:info[k] for k in ('source','symbol','provider','exchange','marketCategory','timeframe','asset_id','universe_origin')}|
                             dict(strategy=strategy,coverage=coverage,session=current))
    for event in events:
        instrument=event.get('instrument',{});info=assets.get((instrument.get('source'),event.get('timeframe')))
        if not info:continue  # Unconfigured sources are never exposed.
        flat=info|{k:event.get(k) for k in ('strategy','strategy_version','direction')}
        if not match(flat,scope) or not match(flat,requested):continue
        if signal_id and event['signal_id']!=signal_id:continue
        evidence=event.get('evidence',{});occurred=event.get('occurred_at');observed=evidence.get('observed_at',occurred);published=event.get('published_at')
        market=session(info,calendars.get(info['source']),as_of)
        coverage=feed_status(info,event.get('strategy'),as_of)
        reason=None
        if event.get('kind')=='invalidated':reason='invalidated'
        elif event.get('kind') not in ('detected','updated') or event.get('contract_version')!=1:reason='unsupported_lifecycle'
        elif event.get('origin')!='live':reason='replay_only'
        elif any(type(t) is not int or not 0<t<=as_of for t in (occurred,observed,published)) or observed<occurred:reason='invalid_timestamp'
        elif as_of-occurred>age*60000 or as_of-observed>age*60000:reason='stale_signal'
        elif coverage['status']!='ready':reason=coverage['status']
        elif observed>coverage['last_closed_at']:reason='coverage_behind_signal'
        elif market['status']!='open':reason=market['reason']
        if page_token:
            current=session(info,calendars.get(info['source']),now)
            if current['status']!=market['status'] or (reason is None and (now-occurred>age*60000 or feed_status(info,event.get('strategy'),now)['status']!='ready')):
                raise DiscoveryError(409,'snapshot_changed')
        if reason:excluded[reason]+=1
        result=dict(signal_id=event['signal_id'],event_id=event['event_id'],strategy=event['strategy'],strategy_version=event['strategy_version'],
            lifecycle=event['kind'],origin=event['origin'],direction=event['direction'],timeframe=event['timeframe'],
            instrument={k:info[k] for k in ('source','symbol','provider','exchange','marketCategory','asset_id')},
            occurred_at=occurred,observed_at=observed,published_at=published,evidence=evidence,
            coverage=coverage,session=market,eligible=reason is None,ineligible_reason=reason,execution_authorized=False)
        if reason is None or signal_id:items.append(result)
    if signal_id and not items:raise DiscoveryError(404,'finding_not_visible')
    items.sort(key=lambda item:(-item['observed_at'] if type(item['observed_at']) is int else 0,item['signal_id']))
    versions=sorted({i['strategy_version'] for i in items})
    if len(catalogue)>10000:raise DiscoveryError(503,'catalogue_capacity_exceeded')
    if route=='catalogue':items=[]
    page=items[offset:offset+limit];end=offset+len(page)
    next_token=signed(dict(revision=revision,query=query_hash,as_of=as_of,offset=end),secret) if end<len(items) else None
    return dict(discovery_version=1,project=project,scope='published_findings_in_scanner_universe',as_of=as_of,evaluated_at=now,
        query=dict(filters=requested,max_age_minutes=age,limit=limit),
        snapshot_expires_at=as_of+SNAPSHOT_MS,items=page,next_page_token=next_token,page_complete=next_token is None,
        coverage=dict(complete=False,historical_index_complete=False,configuration_applied=configuration_applied,reason='Published findings only; no implicit backfill or on-demand scanning',combinations=catalogue),
        excluded=dict(excluded),filters=dict(marketCategory=list(CATEGORIES),strategy=sorted({c['strategy'] for c in catalogue}),timeframe=sorted({c['timeframe'] for c in catalogue}),strategy_version=versions),
        execution_authorized=False)
