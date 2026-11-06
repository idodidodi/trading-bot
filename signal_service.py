"""Transactional signal outbox and authenticated durable pull subscriptions."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from signal_contract import signal_event, canonical


def publish(db, strategy, signal, **kwargs):
    event = signal_event(strategy, signal, **kwargs)
    # A transactional counter serializes commit order. BIGSERIAL can miss a late
    # commit when a faster transaction advances a subscriber's cursor past it.
    db.execute('SELECT value FROM signal_stream_clock WHERE id=1 FOR UPDATE').fetchone()
    if db.execute('SELECT 1 FROM signal_events WHERE event_id=%s',(event['event_id'],)).fetchone():return
    cursor=db.execute('UPDATE signal_stream_clock SET value=value+1 WHERE id=1 RETURNING value').fetchone()[0]
    now=int(time.time()*1000)
    event.update(cursor=cursor,published_at=now)
    db.execute('INSERT INTO signal_events VALUES(%s,%s,%s,%s,%s)',
               (cursor,event['event_id'],event['signal_id'],now,canonical(event)))


def reconcile_provisional(db, source, timeframe, candles, rules):
    """Report later pivot validation without changing any Telegram alert state."""
    rows=db.execute("""SELECT payload FROM (
        SELECT DISTINCT ON(signal_id) signal_id,payload,cursor FROM signal_events
        WHERE payload->>'strategy'='rsi' AND payload->'instrument'->>'source'=%s
        AND payload->>'timeframe'=%s AND payload->>'strategy_version'=%s
        ORDER BY signal_id,cursor DESC) latest
        WHERE payload->>'kind'='detected' AND payload->'evidence'->>'signal_status'='provisional'
        AND payload->>'origin'='live'""",(source,timeframe,rules['skill_hash'])).fetchall()
    positions={b.start:i for i,b in enumerate(candles)}
    for (event,) in rows:
        signal=event['evidence'];i=positions.get(signal['pivot2']);right=rules['pivot_right']
        if i is None or i+right>=len(candles):continue
        price=candles[i].close;following=candles[i+1:i+right+1]
        valid=all(price<b.close if signal['direction']=='bullish' else price>b.close for b in following)
        publish(db,'rsi',signal|dict(version=event['strategy_version'],
            lifecycle='structurally_confirmed' if valid else 'pivot_invalidated',observed_at=following[-1].end),
            kind='updated' if valid else 'invalidated')


def filters(value):
    allowed={'strategy','strategy_version','symbol','market','timeframe','direction','kind'}
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError('Unknown filter')
    if any(not isinstance(v,list) or not v or len(v)>100 or any(not isinstance(s,str) or not 0<len(s)<=200 for s in v) for v in value.values()):
        raise ValueError('Filters must be nonempty string lists')
    return value


def matches(event, selection):
    return all((event['instrument'].get(k) if k in ('symbol','market') else event.get(k)) in v for k,v in selection.items())


def digest(token):return hashlib.sha256(token.encode()).hexdigest()


def register(store, body):
    project=body.get('project','');mode=body.get('mode','live');selection=filters(body.get('filters',{}))
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',project) or mode not in ('live','replay'):raise ValueError('Invalid registration')
    notices=body.get('execution_notices',False)
    if type(notices) is not bool or notices and mode!='live':raise ValueError('Live notices only')
    token=secrets.token_urlsafe(32)
    with store.connect() as db:
        clock=db.execute('SELECT value FROM signal_stream_clock WHERE id=1 FOR UPDATE').fetchone()[0]
        floor=clock if mode=='live' else 0
        if db.execute('SELECT 1 FROM signal_subscriptions WHERE project=%s',(project,)).fetchone():raise ValueError('Project already registered; rotate credentials explicitly')
        db.execute('INSERT INTO signal_subscriptions(project,token_hash,filters,mode,created_at,floor,ack,delivered,execution_notices) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (project,digest(token),canonical(selection),mode,int(time.time()*1000),floor,floor,floor,notices))
    return dict(project=project,token=token,cursor=floor,mode=mode)


def subscription(db, token):
    row=db.execute('SELECT project,filters,mode,created_at,floor,ack,delivered FROM signal_subscriptions WHERE token_hash=%s AND enabled=true FOR UPDATE',(digest(token),)).fetchone()
    if not row:raise PermissionError('Invalid subscriber credentials')
    return dict(zip(('project','filters','mode','created_at','floor','ack','delivered'),row))


def consume(store, token, after=None, limit=100):
    if not 1<=limit<=500:raise ValueError('Invalid page size')
    with store.connect() as db:
        sub=subscription(db,token)
        after=sub['ack'] if after is None else after
        high=db.execute('SELECT value FROM signal_stream_clock WHERE id=1').fetchone()[0]
        if not sub['floor']<=after<=sub['delivered'] or after>high:raise ValueError('Cursor outside delivered subscription range')
        rows=db.execute('SELECT cursor,payload FROM signal_events WHERE cursor>%s ORDER BY cursor LIMIT %s',(after,limit)).fetchall()
        result=[]
        for cursor,event in rows:
            if not matches(event,sub['filters']):continue
            historical=sub['mode']=='replay' or event['origin']!='live' or event['occurred_at']<sub['created_at']
            result.append(event|dict(delivery_mode='replay' if historical else 'live'))
        end=rows[-1][0] if rows else after
        db.execute('UPDATE signal_subscriptions SET delivered=GREATEST(delivered,%s) WHERE project=%s',(end,sub['project']))
    return dict(contract_version=1,project=sub['project'],events=result,next_cursor=end,has_more=end<high)


def acknowledge(store,token,cursor):
    if type(cursor) is not int:raise ValueError('Integer cursor required')
    with store.connect() as db:
        sub=subscription(db,token)
        if not sub['floor']<=cursor<=sub['delivered']:raise ValueError('Undelivered cursor')
        db.execute('UPDATE signal_subscriptions SET ack=GREATEST(ack,%s) WHERE project=%s',(cursor,sub['project']))
    return dict(cursor=max(cursor,sub['ack']))


def administer(store,project,action):
    if action not in ('revoke','rotate'):raise ValueError('Unknown action')
    token=secrets.token_urlsafe(32)
    with store.connect() as db:
        result=db.execute('UPDATE signal_subscriptions SET enabled=%s,token_hash=%s WHERE project=%s',
                         (action=='rotate',digest(token),project))
        if not result.rowcount:raise ValueError('Unknown project')
    return dict(project=project,**({'token':token} if action=='rotate' else {'revoked':True}))


def handler(store,admin_token):
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import urlsplit,parse_qs
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass  # No bearer tokens or private URLs in logs.
        def respond(self,status,value):
            raw=canonical(value).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def dispatch(self):
            try:
                url=urlsplit(self.path);auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer ') or len(auth)>512:raise PermissionError()
                token=auth[7:]
                if self.command=='POST':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=32768:raise ValueError('Invalid body size')
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict):raise ValueError('Object required')
                    if url.path=='/v1/projects':
                        if not hmac.compare_digest(token,admin_token):raise PermissionError()
                        result=register(store,body)
                    elif url.path=='/v1/projects/credentials':
                        if not hmac.compare_digest(token,admin_token):raise PermissionError()
                        result=administer(store,body['project'],body['action'])
                    elif url.path=='/v1/execution-events':result=execution_notice(store,token,body)
                    elif url.path=='/v1/ack':result=acknowledge(store,token,body['cursor'])
                    else:self.respond(404,{'error':'Not found'});return
                elif url.path=='/v1/events':
                    query=parse_qs(url.query)
                    result=consume(store,token,int(query['after'][0]) if 'after' in query else None,int(query.get('limit',['100'])[0]))
                else:self.respond(404,{'error':'Not found'});return
                self.respond(200,result)
            except PermissionError:self.respond(401,{'error':'Unauthorized'})
            except (ValueError,KeyError,TypeError):self.respond(400,{'error':'Invalid request'})
            except Exception:self.respond(503,{'error':'Service unavailable'})
        do_GET=dispatch
        do_POST=dispatch
    return Handler


def main():
    from platform_app import Store
    from http.server import ThreadingHTTPServer
    token=os.environ.get('SIGNAL_ADMIN_TOKEN','')
    if len(token)<32:raise SystemExit('Set SIGNAL_ADMIN_TOKEN (at least 32 characters)')
    # Dedicated API process: never starts a scanner, Telegram, or execution.
    server=ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('SIGNAL_API_PORT','8090'))),handler(Store(),token))
    server.serve_forever()



def execution_notice(store,token,body):
    """Trading reports facts; only this service selects recipients and sends Telegram."""
    from chasing_runtime import enqueue
    from signal_contract import EVIDENCE
    if body.get('kind')!='demo_exit' or not re.fullmatch(r'[a-zA-Z0-9:_-]{1,100}',body.get('id','')):
        raise ValueError('Unsupported execution notice')
    if type(body.get('at')) is not int or body['at']<=0:raise ValueError('Invalid notice time')
    if not re.fullmatch(r'[A-Z0-9]{1,30}',body.get('symbol','')):raise ValueError('Invalid symbol')
    root={k:v for k,v in body['root'].items() if k in EVIDENCE}
    sig={k:v for k,v in body['signal'].items() if k in EVIDENCE}
    if sig.get('direction') not in ('bullish','bearish') or sig.get('timeframe') not in ('4h','1h','30m','15m','5m','daily','weekly','monthly'):
        raise ValueError('Invalid divergence')
    fingerprint=digest(canonical(body))
    with store.connect() as db:
        sub=subscription(db,token)
        allowed=db.execute('SELECT execution_notices FROM signal_subscriptions WHERE project=%s',(sub['project'],)).fetchone()[0]
        if not allowed or sub['mode']!='live':raise PermissionError()
        saved=db.execute('SELECT payload_hash FROM execution_notice_receipts WHERE project=%s AND id=%s',(sub['project'],body['id'])).fetchone()
        if saved:
            if saved[0]!=fingerprint:raise ValueError('Notice identity conflict')
            return dict(accepted=True)
        recipients=db.execute('SELECT bot_runtime.chasing_recipients()').fetchone()[0]
        installation=db.execute('SELECT installation FROM runtime_control WHERE id=1').fetchone()[0]
        for person in recipients:
            if not person.get('live_positions') or not person['routes'].get('live_positions'):continue
            # Match legacy identity: existing delivery journal suppresses migration duplicates.
            key=hashlib.sha256((body['id']+':'+str(person['user_id'])).encode()).hexdigest()[:32]
            state=dict(id=key,user=str(person['user_id']),root=root|dict(symbol=sig['symbol']),status='demo_exit',next_due=None)
            db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(key,installation,person['user_id'],canonical(state)))
            enqueue(db,state,dict(kind='demo_exit',at=body['at'],session=key,destination='live_positions',signal=sig,symbol=body['symbol']))
        db.execute('INSERT INTO execution_notice_receipts VALUES(%s,%s,%s)',(sub['project'],body['id'],fingerprint))
    return dict(accepted=True)

if __name__=='__main__':main()
