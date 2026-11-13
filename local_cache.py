"""Opt-in, disposable Linux working data; Postgres still owns all shared state.

Never use this database for worker ownership, signal identities, delivery, orders,
configuration or provider quota reservations. Clearing it only causes cold reads
and idempotent candle republishing, not replay of effects.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time


class CacheError(ValueError):
    pass


def configured():
    directory = os.environ.get('TRADING_LOCAL_CACHE_DIR', '').strip()
    if not directory:
        return None
    # Separate acknowledgements across database destinations/worker credentials.
    dsn = os.environ.get('TRADING_DATABASE_URL', '').strip()
    if not dsn:
        raise CacheError('Local cache requires an explicit Postgres destination')
    return LocalCache(directory, hashlib.sha256(dsn.encode()).hexdigest())


class LocalCache:
    def __init__(self, directory, namespace):
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise CacheError('TRADING_LOCAL_CACHE_DIR must be an absolute dedicated directory')
        self.namespace = namespace
        self.path = self.directory / 'working-cache.sqlite3'

    @contextmanager
    def connect(self):
        db = None
        try:
            if self.directory.is_symlink():
                raise CacheError('Local cache directory must not be a symlink')
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != 'nt' and self.directory.stat().st_mode & 0o077:
                raise CacheError('Local cache directory must have private permissions (0700)')
            if self.path.is_symlink():
                raise CacheError('Local cache file must not be a symlink')
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.close(fd)
            db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            # SQLite's first WAL transition can return BUSY immediately even
            # with busy_timeout set. Only retry this initialization step; never
            # replay the caller's publication transaction or an ambiguous commit.
            deadline = time.monotonic() + 30
            while True:
                try:
                    db.execute('PRAGMA journal_mode=WAL')
                    break
                except sqlite3.OperationalError as exc:
                    if exc.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('''CREATE TABLE IF NOT EXISTS responses(
                namespace TEXT, key TEXT, fetched REAL, payload TEXT,
                PRIMARY KEY(namespace,key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS candles(
                namespace TEXT, kind TEXT, feed TEXT, frame TEXT, start INTEGER,
                payload TEXT, acknowledged REAL,
                PRIMARY KEY(namespace,kind,feed,frame,start))''')
            db.execute('''CREATE TABLE IF NOT EXISTS counters(
                namespace TEXT, name TEXT, value INTEGER,
                PRIMARY KEY(namespace,name))''')
            yield db
        except (sqlite3.Error, OSError):
            raise CacheError('Local working cache unavailable; inspect disk, permissions and integrity') from None
        finally:
            if db is not None:
                db.close()

    def count(self, db, name, value=1):
        db.execute('''INSERT INTO counters VALUES(?,?,?) ON CONFLICT(namespace,name)
            DO UPDATE SET value=value+excluded.value''', (self.namespace, name, value))

    def get(self, key, now, ttl):
        with self.connect() as db:
            row = db.execute('SELECT fetched,payload FROM responses WHERE namespace=? AND key=?',
                             (self.namespace, key)).fetchone()
            if row and 0 <= now-row[0] < ttl:
                self.count(db, 'response_hits')
                return json.loads(row[1])
            self.count(db, 'response_misses')
            return None

    def put(self, key, payload, fetched):
        encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO responses VALUES(?,?,?,?)',
                       (self.namespace, key, fetched, encoded))
            # Response caches are short-lived. Candle history is not expired here.
            db.execute('DELETE FROM responses WHERE fetched<?', (fetched-86400,))
            db.commit()

    def health(self):
        with self.connect() as db:
            integrity = db.execute('PRAGMA quick_check').fetchone()[0]
            candles = db.execute('SELECT count(*) FROM candles WHERE namespace=?', (self.namespace,)).fetchone()[0]
            responses = db.execute('SELECT count(*) FROM responses WHERE namespace=?', (self.namespace,)).fetchone()[0]
            counters = dict(db.execute('SELECT name,value FROM counters WHERE namespace=?', (self.namespace,)))
        disk = shutil.disk_usage(self.directory)
        size = sum(p.stat().st_size for p in self.directory.glob('working-cache.sqlite3*') if p.is_file())
        return dict(integrity=integrity, candle_rows=candles, response_entries=responses,
                    cache_bytes=size, disk_free_bytes=disk.free, disk_total_bytes=disk.total,
                    disk_warning=disk.free < max(5*1024**3, disk.total*.1), counters=counters)

    def backup(self, destination):
        """SQLite backup API includes committed WAL; refuse overwriting any file."""
        destination = Path(destination)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with self.connect() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise CacheError('Local cache backup integrity check failed')
                finally:
                    target.close()
        except Exception:
            destination.unlink(missing_ok=True)
            raise


TABLES = {
    'candle_cache': ('feed,asset,timeframe,start,"end",open,high,low,close', 9, 2, 3),
    'backtest_candle_cache': ('feed,timeframe,start,"end",open,high,low,close', 8, 1, 2),
}


def publish_candles(store, table, rows):
    """Acknowledge locally only AFTER Postgres COMMIT. Retry writes are idempotent.

    Hold a local writer lock through publication so concurrent publishers cannot
    leave an acknowledgement for a different payload than the hosted row. Resend
    unchanged rows after 24h to repair hosted restores/out-of-band changes.
    """
    columns, width, frame_index, start_index = TABLES[table]
    rows = [tuple(row) for row in rows]
    if any(len(row) != width for row in rows):
        raise ValueError('Invalid candle row')
    updates = '"end"=excluded."end",open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close'
    if table == 'candle_cache':
        updates += ',asset=excluded.asset'
    query = f'INSERT INTO {table} ({columns}) VALUES({",".join(["%s"]*width)}) ON CONFLICT(feed,timeframe,start) DO UPDATE SET {updates}'

    def publish(changed):
        if changed:
            with store.connect() as db:
                db.executemany(query, changed)

    cache = configured()
    if cache is None:
        publish(rows)
        return
    now = time.time()
    with cache.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        changed, acknowledged = [], []
        # Read each affected series once locally, not one SQL query per candle.
        series = {(r[0], r[frame_index]) for r in rows}
        existing = {}
        for feed, frame in series:
            for start, payload, at in db.execute('''SELECT start,payload,acknowledged FROM candles
                    WHERE namespace=? AND kind=? AND feed=? AND frame=?''',
                    (cache.namespace, table, feed, frame)):
                existing[feed, frame, start] = payload, at
        for row in rows:
            key = row[0], row[frame_index], row[start_index]
            encoded = json.dumps(row, separators=(',', ':'), allow_nan=False)
            previous = existing.get(key)
            if previous and previous[0] == encoded and 0 <= now-previous[1] < 86400:
                continue
            changed.append(row)
            acknowledged.append((cache.namespace, table, *key, encoded, now))
        publish(changed)
        db.executemany('INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)', acknowledged)
        cache.count(db, 'candles_published', len(changed))
        cache.count(db, 'candle_writes_avoided', len(rows)-len(changed))
        db.commit()


def read_backtest_candles(store, feed, frame):
    """Brief read-through cache for repeated CSV replays; hosted edits refresh in 5m."""
    cache = configured()
    key = json.dumps(['backtest', feed, frame])
    now = time.time()
    if cache:
        rows = cache.get(key, now, 300)
        if rows is not None:
            return rows
    with store.connect() as db:
        rows = db.execute('SELECT start,"end",open,high,low,close FROM backtest_candle_cache WHERE feed=%s AND timeframe=%s ORDER BY start', (feed, frame)).fetchall()
    if cache and rows:
        cache.put(key, rows, now)
    return rows


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('health', 'backup'))
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    try:
        cache = configured()
        if cache is None:
            print(json.dumps({'enabled': False}))
            return 1
        if args.action == 'backup':
            if not args.destination:
                parser.error('backup requires --destination (new file)')
            cache.backup(args.destination)
        result = cache.health()
        print(json.dumps(dict(enabled=True, **result), indent=2))
        return 0 if result['integrity'] == 'ok' and not result['disk_warning'] else 2
    except (CacheError, OSError):
        print(json.dumps({'enabled': True, 'error': 'Local cache operation failed; check disk, permissions and integrity'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
