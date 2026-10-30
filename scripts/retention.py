"""Conservative, opt-in retention. No worker imports, dotenv loading or scheduling."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import time

DAY = 86400
ROOT = Path(__file__).resolve().parent.parent
TABLES = {
    'tiingo_requests': ('at', DAY),
    'tiingo_responses': ('fetched', DAY),
    'activity_log': ('occurred', 30 * DAY),
}
ROUTINE = ('Scanner heartbeat', 'Scan cycle started', 'Scan cycle completed')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private_json(path, value):
    data = json.dumps(value, sort_keys=True).encode()
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != data:
        raise RuntimeError('Backup verification failed')


def safe_file(root, path):
    """Reject symlinks at every level, even when they resolve inside root."""
    path = Path(os.path.abspath(path))
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Symlink excluded')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Not a regular file')
    return path


def file_identity(info):
    # Reading may update atime; compare only identity and content-change metadata.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inventory(root, logs=(), now=None):
    now = time.time() if now is None else now
    logs = {str(Path(p)) for p in logs}
    result = []
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if d not in
                          ('.git', '.venv', '.venv.python3.14-backup', '.retention', 'node_modules')
                          and not (Path(folder) / d).is_symlink()]
        for name in files:
            path = Path(folder) / name
            relative = str(path.relative_to(root))
            try:
                safe_file(root, path)
                before = path.stat()
                # Only rebuildable bytecode and operator-declared inactive logs.
                reason = ('rebuildable bytecode' if path.suffix == '.pyc' and '__pycache__' in path.parts
                          else 'declared inactive log' if relative in logs and path.suffix == '.log'
                          else None)
                minimum_age = 7 * DAY if reason == 'rebuildable bytecode' else 90 * DAY
                eligible = reason is not None and 0 < before.st_mtime < now - minimum_age
                sha = digest(path.read_bytes()) if eligible else None
                after = path.stat()
                if file_identity(before) != file_identity(after):
                    eligible, sha = False, None
                result.append(dict(path=relative, bytes=after.st_size,
                                   allocated_bytes=getattr(after, 'st_blocks', None) * 512 if hasattr(after, 'st_blocks') else None,
                                   mtime_ns=after.st_mtime_ns, sha256=sha,
                                   action='quarantine' if eligible else 'retain',
                                   reason=reason or 'dependencies unknown or protected'))
            except (OSError, ValueError):
                continue
    return result


def quarantine(root, report, now=None):
    """Copy, verify, then unlink unchanged candidates. Keep backup for restoration."""
    now = time.time() if now is None else now
    destination = root / '.retention' / str(time.time_ns())
    if (root / '.retention').is_symlink():
        raise ValueError('Symlink archive excluded')
    destination.mkdir(parents=True, mode=0o700)
    entries = []
    for row in report:
        if row['action'] != 'quarantine':
            continue
        path = safe_file(root, root / row['path'])
        # Never trust a hand-edited manifest to authorize arbitrary data deletion.
        age = 7 * DAY if path.suffix == '.pyc' and '__pycache__' in path.parts else 90 * DAY
        if not (path.suffix == '.pyc' and '__pycache__' in path.parts or
                path.suffix == '.log' and row['reason'] == 'declared inactive log'):
            raise ValueError('Protected file')
        before = path.stat()
        raw = path.read_bytes()
        if (before.st_mtime_ns != row['mtime_ns'] or digest(raw) != row['sha256'] or
                not 0 < before.st_mtime < now - age):
            raise ValueError('File changed or is too recent')
        backup = destination / (str(len(entries)) + '.gz')
        with backup.open('xb') as stream:
            os.chmod(backup, 0o600)
            stream.write(gzip.compress(raw, mtime=0))
            stream.flush()
            os.fsync(stream.fileno())
        if gzip.decompress(backup.read_bytes()) != raw:
            raise RuntimeError('Archive verification failed')
        entries.append(row | dict(backup=backup.name))
        # Persist recovery metadata BEFORE deleting its source.
        private_json(destination / 'manifest-next.json', dict(created=now, entries=entries))
        os.replace(destination / 'manifest-next.json', destination / 'manifest.json')
        # Windows does not support opening/fsyncing directories this way.
        # Backup and manifest file contents are fsynced on both platforms.
        if os.name != 'nt':
            directory_fd = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if file_identity(path.stat()) != file_identity(before):
            raise ValueError('File changed during backup')
        path.unlink()
    return dict(archive=str(destination), files=len(entries),
                source_bytes=sum(e['bytes'] for e in entries),
                archive_bytes=sum(p.stat().st_size for p in destination.iterdir()))


def restore_local(root, manifest):
    saved = json.loads(manifest.read_text())
    for row in saved['entries']:
        target = root / row['path']
        if Path(row['path']).is_absolute() or '..' in Path(row['path']).parts:
            raise ValueError('Invalid restore path')
        current = root
        for part in target.relative_to(root).parts:
            current /= part
            if current.is_symlink():
                raise ValueError('Symlink excluded')
        source = safe_file(manifest.parent, manifest.parent / row['backup'])
        raw = gzip.decompress(source.read_bytes())
        if digest(raw) != row['sha256']:
            raise ValueError('Archive checksum mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            os.chmod(target, 0o600)
            stream.write(raw)
        os.utime(target, ns=(row['mtime_ns'], row['mtime_ns']))
    return len(saved['entries'])


def database(dsn, archive=None, now=None):
    """One bounded batch per table; archives are verified before COMMIT.

    Caller supplies a dedicated maintenance connection explicitly. No DSN is logged.
    """
    import psycopg
    from psycopg import sql
    now = time.time() if now is None else now
    result = {}
    with psycopg.connect(dsn) as db:
        db.execute("SET LOCAL statement_timeout='15s'")
        db.execute("SET LOCAL lock_timeout='2s'")
        if not db.execute('SELECT pg_try_advisory_xact_lock(714269099)').fetchone()[0]:
            raise RuntimeError('Retention already running')
        result['inventory'] = [dict(schema=schema, table=table, estimated_rows=count,
                                    total_bytes=total, index_bytes=indexes)
            for schema, table, count, total, indexes in db.execute(
                "SELECT schemaname,relname,n_live_tup,pg_total_relation_size(relid),"
                "pg_indexes_size(relid) FROM pg_stat_user_tables "
                "WHERE schemaname IN ('bot_runtime','public') ORDER BY 4 DESC")]
        for table, (column, age) in TABLES.items():
            predicate = sql.SQL('{} > 0 AND {} < %s').format(sql.Identifier(column), sql.Identifier(column))
            params = [now - age]
            if table == 'activity_log':
                predicate += sql.SQL(' AND event = ANY(%s)')
                params.append(list(ROUTINE))
            qualified = sql.Identifier('bot_runtime', table)
            count = db.execute(sql.SQL('SELECT count(*) FROM {} WHERE ').format(qualified) + predicate, params).fetchone()[0]
            total = db.execute('SELECT pg_total_relation_size(%s::regclass)', ('bot_runtime.' + table,)).fetchone()[0]
            bounds = db.execute(sql.SQL('SELECT min({}),max({}) FROM {}').format(
                sql.Identifier(column), sql.Identifier(column), qualified)).fetchone()
            result[table] = dict(eligible=count, relation_bytes=total, deleted=0,
                                 oldest=bounds[0], newest=bounds[1])
            if archive is None:
                continue
            # Row locks avoid racing cache refreshes; skip busy rows.
            rows = db.execute(sql.SQL('SELECT ctid::text,row_to_json(t) FROM {} t WHERE ').format(qualified) + predicate +
                              sql.SQL(' ORDER BY {} LIMIT 1000 FOR UPDATE SKIP LOCKED').format(sql.Identifier(column)), params).fetchall()
            if not rows:
                continue
            backup = archive / f'{table}-{time.time_ns()}.json'
            private_json(backup, dict(table=table, cutoff=now-age, rows=[r[1] for r in rows]))
            db.execute(sql.SQL('DELETE FROM {} WHERE ctid = ANY(%s::tid[])').format(qualified), ([r[0] for r in rows],))
            result[table]['deleted'] = len(rows)
            result[table]['backup'] = str(backup)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Quarantine eligible local files; database expiry requires --database too')
    parser.add_argument('--inactive-log', action='append', default=[], help='Root-relative .log path explicitly known to be inactive for 90+ days')
    parser.add_argument('--database', action='store_true', help='Use explicit TRADING_RETENTION_DATABASE_URL; never load .env')
    parser.add_argument('--restore-local', type=Path, help='Restore a quarantine manifest without overwriting existing files')
    args = parser.parse_args()
    if args.restore_local:
        if args.apply or args.database:
            parser.error('Restore is a separate operation')
        print(json.dumps(dict(restored=restore_local(ROOT, args.restore_local.resolve()))))
        return
    rows = inventory(ROOT, args.inactive_log)
    report = dict(mode='apply' if args.apply else 'dry-run', local=rows)
    dsn = None
    if args.database:
        dsn = os.environ.get('TRADING_RETENTION_DATABASE_URL')
        if not dsn:
            parser.error('TRADING_RETENTION_DATABASE_URL must be explicitly configured')
        from psycopg.conninfo import conninfo_to_dict
        options = conninfo_to_dict(dsn)
        if options.get('host') not in ('localhost', '127.0.0.1', '/tmp', '/private/tmp') and options.get('sslmode') not in ('verify-full', 'verify-ca'):
            parser.error('Database requires verified TLS')
    if args.apply:
        report['cleanup'] = quarantine(ROOT, rows)
    if args.database:
        archive = Path(report['cleanup']['archive']) if args.apply else None
        report['database'] = database(dsn, archive)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
