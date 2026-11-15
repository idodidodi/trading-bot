"""Opt-in local PostgreSQL working store. Shared control remains on Supabase.

Schema installation and historical import are explicit operator steps, never
performed by a running scanner. A configured but unavailable store fails closed.
"""
from contextlib import contextmanager
import os
import json
import hashlib
import psycopg
from psycopg.conninfo import conninfo_to_dict


def configured():
    dsn = os.environ.get('TRADING_WORKING_DATABASE_URL', '').strip()
    return WorkingStore(dsn) if dsn else None


class WorkingStore:
    def __init__(self, dsn):
        settings = conninfo_to_dict(dsn)
        if settings.get('host') not in ('127.0.0.1', '::1', 'localhost') or settings.get('hostaddr', '') not in ('', '127.0.0.1', '::1'):
            raise ValueError('Working database must use an explicit loopback host')
        if not settings.get('dbname') or settings.get('service'):
            raise ValueError('Working database requires an explicit database, without service indirection')
        self.dsn = dsn
        shared = os.environ.get('TRADING_DATABASE_URL', '')
        if not shared:
            raise ValueError('Shared database destination is required')
        self.namespace = hashlib.sha256(shared.encode()).hexdigest()

    @contextmanager
    def connect(self):
        with psycopg.connect(self.dsn, connect_timeout=5,
                options='-c search_path=working,pg_catalog -c statement_timeout=30000 -c lock_timeout=5000') as db:
            yield db

    def get(self, key, now, ttl):
        with self.connect() as db:
            row = db.execute('SELECT fetched,payload FROM responses WHERE namespace=%s AND key=%s', (self.namespace,key)).fetchone()
            hit = bool(row and 0 <= now-row[0] < ttl)
            db.execute('INSERT INTO counters VALUES(%s,%s,1) ON CONFLICT(namespace,name) DO UPDATE SET value=counters.value+1', (self.namespace,'response_hits' if hit else 'response_misses'))
            if hit:
                return row[1]
        return None

    def put(self, key, payload, fetched):
        with self.connect() as db:
            db.execute('INSERT INTO responses VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(namespace,key) DO UPDATE SET fetched=excluded.fetched,payload=excluded.payload',
                       (self.namespace,key,fetched,json.dumps(payload,allow_nan=False)))
            db.execute('DELETE FROM responses WHERE namespace=%s AND fetched<%s',(self.namespace,fetched-86400))

    def read_candles(self, table, feed, frame):
        from local_cache import TABLES
        if table not in TABLES: raise ValueError('Invalid candle kind')
        with self.connect() as db:
            rows=db.execute('SELECT payload FROM candles WHERE namespace=%s AND kind=%s AND feed=%s AND frame=%s ORDER BY start',
                            (self.namespace,table,feed,frame)).fetchall()
        return [tuple(row[0][-6:]) for row in rows]

    def publish(self, store, table, rows, query):
        """Serialize a series batch; acknowledge only after hosted commit.

        Uncertain hosted commits are resent idempotently on retry. A local
        failure never marks a publication complete.
        """
        import time
        from local_cache import TABLES
        _, _, frame, start = TABLES[table]
        now=time.time()
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(714269101)')
            for row in rows:
                db.execute('''INSERT INTO candles VALUES(%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT(namespace,kind,feed,frame,start) DO UPDATE SET payload=excluded.payload WHERE candles.payload IS DISTINCT FROM excluded.payload''',
                    (self.namespace,table,row[0],row[frame],row[start],json.dumps(row,allow_nan=False)))
            changed=[]
            for row in rows:
                key=(self.namespace,table,row[0],row[frame],row[start])
                prior=db.execute('SELECT payload,acknowledged FROM publication WHERE namespace=%s AND kind=%s AND feed=%s AND frame=%s AND start=%s',key).fetchone()
                if not prior or prior[0]!=list(row) or not 0<=now-prior[1]<86400:
                    changed.append(row)
            if changed:
                with store.connect() as hosted:
                    with hosted.cursor() as cursor: cursor.executemany(query, changed)
                for row in changed:
                    db.execute('''INSERT INTO publication VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT(namespace,kind,feed,frame,start) DO UPDATE SET payload=excluded.payload,acknowledged=excluded.acknowledged''',
                        (self.namespace,table,row[0],row[frame],row[start],json.dumps(row,allow_nan=False),now))
            for name,value in [('candles_published',len(changed)),('candle_writes_avoided',len(rows)-len(changed))]:
                db.execute('INSERT INTO counters VALUES(%s,%s,%s) ON CONFLICT(namespace,name) DO UPDATE SET value=counters.value+excluded.value',(self.namespace,name,value))

    def health(self):
        with self.connect() as db:
            candles=db.execute('SELECT count(*) FROM candles WHERE namespace=%s',(self.namespace,)).fetchone()[0]
            responses=db.execute('SELECT count(*) FROM responses WHERE namespace=%s',(self.namespace,)).fetchone()[0]
            size=db.execute('SELECT pg_database_size(current_database())').fetchone()[0]
            counters=dict(db.execute('SELECT name,value FROM counters WHERE namespace=%s',(self.namespace,)))
        return dict(backend='postgres',integrity='not_checked',query_health='ok',candle_rows=candles,response_entries=responses,database_bytes=size,counters=counters)
