"""Independent execution journal. SQLite WAL; no signal-service table access."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

DatabaseError=sqlite3.DatabaseError
OperationalError=sqlite3.OperationalError
RETRYABLE=(sqlite3.OperationalError,)

SCHEMA='''
CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,
 reconciled INTEGER NOT NULL DEFAULT 0,activated_at INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO control VALUES(1,0,0,0);
CREATE TABLE IF NOT EXISTS stream(id INTEGER PRIMARY KEY CHECK(id=1),project TEXT NOT NULL,cursor INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY,cursor INTEGER UNIQUE NOT NULL,signal_id TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS momentum_history(market TEXT,symbol TEXT,payload TEXT NOT NULL,PRIMARY KEY(market,symbol));
CREATE TABLE IF NOT EXISTS momentum_reports(id INTEGER PRIMARY KEY,created INTEGER,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS momentum_reviews(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_trades(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_status(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS demo_execution_trades(id TEXT PRIMARY KEY,state TEXT NOT NULL,payload TEXT NOT NULL,updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS demo_execution_policy(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,enabled INTEGER NOT NULL,activated_at INTEGER NOT NULL,config TEXT NOT NULL,status TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS activity_log(id INTEGER PRIMARY KEY,occurred REAL,event TEXT,details TEXT);
CREATE TABLE IF NOT EXISTS migration_receipts(digest TEXT PRIMARY KEY,manifest TEXT NOT NULL);
'''

class Connection(sqlite3.Connection):
    def execute(self,query,params=()):return super().execute(query.replace('%s','?'),params)
    def require_table(self,name):pass

class Store:
    def __init__(self,path):
        self.path=Path(path).resolve();self.guard=None
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:db.executescript(SCHEMA)
        os.chmod(self.path,0o600)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10,factory=Connection)
        try:
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:db.close()
    def log(self,event,details):
        with self.connect() as db:db.execute('INSERT INTO activity_log(occurred,event,details) VALUES(?,?,?)',(time.time(),event,details))
    def acquire_worker(self):
        if self.guard is not None and not self.guard.closed:return
        # OS lock is released on crash; never use expiring leases across broker writes.
        import sys
        self.guard=open(str(self.path)+'.owner','a+b')
        try:
            if sys.platform=='win32':
                import msvcrt
                self.guard.seek(0);self.guard.write(b'0');self.guard.flush();self.guard.seek(0)
                msvcrt.locking(self.guard.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.guard.close();self.guard=None
            raise RuntimeError('Another execution owner is running') from None
    def close(self):
        if self.guard:self.guard.close();self.guard=None
    def state(self):
        with self.connect() as db:return db.execute('SELECT enabled,reconciled,activated_at FROM control WHERE id=1').fetchone()
    def events(self):
        with self.connect() as db:rows=db.execute('SELECT payload FROM inbox ORDER BY cursor').fetchall()
        # Updates/invalidation are independent events; only latest state can enter.
        latest={}
        for (raw,) in rows:
            e=json.loads(raw);latest[e['signal_id']]=e
        return list(latest.values())
    def signals(self,strategy,now=None):
        now=int(time.time()*1000) if now is None else now
        _,_,activation=self.state()
        return [e for e in self.events() if e['strategy']==strategy and e['kind']=='detected'
                and e['origin']=='live' and e['delivery_mode']=='live' and e['published_at']>=activation
                and e['occurred_at']>=activation and e['occurred_at']<=now]


def assert_worker(store):
    if store.guard is None or store.guard.closed:raise RuntimeError('Execution ownership required')
    enabled,reconciled,_=store.state()
    if not enabled or not reconciled or os.environ.get('EXECUTION_ENABLED')!='true':
        raise RuntimeError('Execution is disabled; complete reviewed cutover first')

@contextmanager
def activity_connection(store):
    with store.connect() as db:yield db


def paper_trading_enabled(store):
    return bool(store.state()[0] and os.environ.get('PAPER_TRADING_ENABLED')=='true' and os.environ.get('EXECUTION_ENABLED')=='true')
