"""Postgres-only runtime: bounded shared pool, explicit transactions, no local fallback."""
import atexit
from contextlib import contextmanager
import logging
import os
import threading
import time

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool, PoolTimeout

DatabaseError = psycopg.Error
OperationalError = psycopg.OperationalError
RETRYABLE = (psycopg.OperationalError, psycopg.errors.SerializationFailure,
             psycopg.errors.DeadlockDetected, psycopg.errors.LockNotAvailable, PoolTimeout)
SCHEMA_VERSION = 1
_lock = threading.RLock()
_pool = None
# Pool messages can contain connection parameters; emit only our sanitized diagnostics.
logging.getLogger('psycopg.pool').setLevel(logging.CRITICAL)


def settings():
    dsn = os.environ.get('TRADING_DATABASE_URL', '').strip()
    if not dsn:
        raise ValueError('TRADING_DATABASE_URL is required; runtime has no local database fallback')
    options = conninfo_to_dict(dsn)
    local_test = (os.environ.get('TRADING_DB_TEST') == '1' and options.get('host') in ('127.0.0.1','localhost','/private/tmp','/tmp') and options.get('dbname') == 'trading_bot_test')
    if not local_test and options.get('sslmode') not in ('verify-full', 'verify-ca'):
        raise ValueError('Database TLS requires sslmode=verify-full (or verify-ca with a trusted CA)')
    return dsn


def test_database():
    try:
        options=conninfo_to_dict(os.environ.get('TRADING_DATABASE_URL',''))
        return (os.environ.get('TRADING_DB_TEST')=='1' and options.get('host') in ('127.0.0.1','localhost','/private/tmp','/tmp') and options.get('dbname')=='trading_bot_test')
    except psycopg.Error:
        return False


class Connection(psycopg.Connection):
    def executemany(self, query, params):
        cursor = self.cursor()
        cursor.executemany(query, params)
        return cursor

    def require_table(self, name):
        # All DDL is provisioned manually. Store validates schema_version on open.
        # Kept here so independently initialized strategy modules need no DDL rights.
        return None


def configure_connection(db):
    db.execute('SET extra_float_digits=3')
    db.commit()


def pool():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(settings(), min_size=0, max_size=3, max_waiting=16,
                timeout=8, reconnect_timeout=30, open=True, connection_class=Connection,
                kwargs=dict(connect_timeout=5, prepare_threshold=None,
                    options='-c search_path=bot_runtime,pg_catalog -c statement_timeout=15000 '
                            '-c lock_timeout=5000 -c idle_in_transaction_session_timeout=20000',
                    application_name='trading-bot-postgres'),
                configure=configure_connection, check=ConnectionPool.check_connection)
        return _pool


def close_pool():
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(close_pool)


@contextmanager
def connect():
    # Never retry a transaction/COMMIT here. A lost COMMIT response is ambiguous;
    # the caller must reread durable identities before performing external effects.
    with pool().connection() as db:
        yield db


def database_busy(exc):
    return isinstance(exc, RETRYABLE)


class WorkerOwned(RuntimeError):
    pass


class WorkerGuard:
    """One process owns all workers (one session + a shared pool of three).

    Reconnect resumes only the SAME durable ownership generation. If a replacement
    installation has taken over, this process is permanently fenced out. Provider
    calls never hold DB transactions; deterministic order IDs and delivery leases
    protect the unavoidable external-call/connection-loss race.
    """
    def __init__(self):
        import uuid
        self.mutex = threading.RLock()
        self.revoked = False
        self.owner = str(uuid.uuid4())
        self.db = None
        self._open()
        self.db.execute("INSERT INTO bot_runtime.worker_identity VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner",(self.owner,))
        atexit.register(self.close)

    def _open(self):
        connection = psycopg.connect(settings(), autocommit=True, connect_timeout=5,
                    prepare_threshold=None, application_name='trading-bot-owner')
        if not connection.execute('SELECT pg_try_advisory_lock(714269002)').fetchone()[0]:
            connection.close()
            raise WorkerOwned('Another installation owns the database; stop it before starting this one')
        self.db = connection

    def check(self):
        with self.mutex:
            if self.revoked:
                raise WorkerOwned('Worker ownership changed; this process must remain stopped')
            try:
                row=self.db.execute('SELECT owner FROM bot_runtime.worker_identity WHERE id=1').fetchone()
            except (psycopg.OperationalError,psycopg.InterfaceError):
                self.db.close()
                self._open()
                row=self.db.execute('SELECT owner FROM bot_runtime.worker_identity WHERE id=1').fetchone()
            if row!=(self.owner,):
                self.revoked=True
                self.db.close()
                raise WorkerOwned('A replacement installation took ownership; this process is fenced out')

    def close(self):
        with self.mutex:
            self.revoked = True
            if self.db is not None:
                self.db.close()


def assert_worker(store):
    if store.guard is None:
        raise WorkerOwned('External effects require worker ownership')
    store.guard.check()
    with store.connect() as db:
        if db.execute('SELECT effects_enabled FROM runtime_control WHERE id=1').fetchone() != (True,):
            raise WorkerOwned('External effects are paused by the manual cutover gate')


def remember_finding(db, source, signal):
    from finding_feedback import finding_id, canonical
    db.execute("""INSERT INTO finding_keys(source,evidence_hash,finding_id)
        VALUES(%s,sha256(convert_to(%s::jsonb::text,'UTF8')),%s) ON CONFLICT DO NOTHING""",
        (source, canonical(signal), finding_id(source, signal)))


def resilient_worker(function, lane):
    def run(store, *args):
        stop = args[-1]
        delay = 1
        while not stop.is_set():
            try:
                store.guard.check()
                return function(store, *args)
            except WorkerOwned:
                print(lane + ': ownership lost; processing paused until operator restart', flush=True)
                stop.set()
                return
            except RETRYABLE:
                print(f'{lane}: database unavailable; retry in {delay}s', flush=True)
                if stop.wait(delay):
                    return
                delay = min(30, delay * 2)
    return run
