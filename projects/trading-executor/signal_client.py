"""Versioned HTTP boundary; persist each page before acknowledging it remotely."""
import json
import urllib.parse
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Signal API redirects are forbidden')

class Client:
    def __init__(self,url,token,project):
        parsed=urllib.parse.urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Invalid API URL')
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost')):
            raise ValueError('HTTPS required outside loopback')
        self.url=url.rstrip('/');self.token=token;self.project=project
        self.opener=urllib.request.build_opener(NoRedirect())
    def request(self,path,body=None):
        req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with self.opener.open(req,timeout=15) as response:
            raw=response.read(8*1024*1024+1)
            if len(raw)>8*1024*1024:raise ValueError('Page too large')
            return json.loads(raw)
    def poll(self,store):
        with store.connect() as db:row=db.execute('SELECT project,cursor FROM stream WHERE id=1').fetchone()
        if row and row[0]!=self.project:raise ValueError('Journal belongs to another subscription')
        page=self.request('/v1/events'+('?after='+str(row[1]) if row else ''))
        if page.get('project')!=self.project:raise ValueError('Token belongs to another project')
        cursor=page['next_cursor']
        if page.get('contract_version')!=1 or type(cursor) is not int or cursor<(row[1] if row else 0):raise ValueError('Invalid stream response')
        previous=row[1] if row else -1
        with store.connect() as db:
            for event in page['events']:
                if event.get('contract_version')!=1 or event.get('kind') not in ('detected','updated','invalidated') or event.get('delivery_mode') not in ('live','replay'):
                    raise ValueError('Unsupported signal contract')
                if not previous<event['cursor']<=cursor:raise ValueError('Unordered stream page')
                previous=event['cursor']
                db.execute('INSERT OR IGNORE INTO inbox VALUES(?,?,?,?)',(event['event_id'],event['cursor'],event['signal_id'],json.dumps(event)))
            db.execute('INSERT INTO stream VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor',(self.project,cursor))
        self.request('/v1/ack',{'cursor':cursor})
        return page
    def flush_notifications(self,store):
        with store.connect() as db:rows=db.execute('SELECT id,payload FROM notifications WHERE delivered=0 ORDER BY id').fetchall()
        for key,raw in rows:
            self.request('/v1/execution-events',json.loads(raw))
            with store.connect() as db:db.execute('UPDATE notifications SET delivered=1 WHERE id=?',(key,))
