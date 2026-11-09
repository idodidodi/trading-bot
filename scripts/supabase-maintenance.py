"""Mac-side Supabase health and bounded retention; never starts a worker.

Uses the existing management credential. Health is read-only; retention defaults
to preview and archives exact rows locally before an explicit --apply deletion.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import time

from retention import TABLES, ROUTINE, private_json

ROOT = Path(__file__).resolve().parent.parent
PROJECT = 'bwejkkboophbfeiwizbu'
INSTALLATION = '8734f8e3-8629-4d5f-b0ff-350fb4a6b911'
STATE = ROOT / '.retention' / 'supabase'


def request(path, body=None):
    token = os.environ.get('SUPABASE_ACCESS_TOKEN', '').strip()
    if not token:
        saved = subprocess.run(['security', 'find-generic-password', '-s',
                                'Supabase CLI', '-a', 'supabase', '-w'],
                               capture_output=True, text=True, timeout=15)
        if saved.returncode:
            raise RuntimeError('Supabase management credential unavailable')
        token = saved.stdout.strip()
    if token.startswith('go-keyring-base64:'):
        token = base64.b64decode(token.split(':', 1)[1]).decode()
    # stdin keeps credentials and archived data out of command arguments/logs.
    config = 'url = ' + json.dumps('https://api.supabase.com/v1/' + path) + '\n'
    config += 'header = ' + json.dumps('Authorization: Bearer ' + token) + '\n'
    config += 'header = "Content-Type: application/json"\n'
    if body is not None:
        config += 'data = ' + json.dumps(json.dumps(body)) + '\n'
    result = subprocess.run(['curl', '--silent', '--show-error', '--fail-with-body',
                             '--max-time', '45', '--config', '-'],
                            input=config, capture_output=True, text=True, timeout=50)
    if result.returncode:
        # SQL error bodies may echo private archived rows. Do not print them.
        raise RuntimeError('Supabase request failed; curl code ' + str(result.returncode))
    return json.loads(result.stdout)


def query(sql):
    return request('projects/' + PROJECT + '/database/query', {'query': sql})


def readonly(sql):
    return query("BEGIN READ ONLY; SET LOCAL statement_timeout='15s'; " + sql + '; COMMIT;')


def health():
    project = request('projects/' + PROJECT)
    data = readonly("""SELECT jsonb_build_object(
      'database_bytes',pg_database_size(current_database()),
      'read_only',current_setting('default_transaction_read_only'),
      'connections',(SELECT count(*) FROM pg_stat_activity),
      'max_connections',current_setting('max_connections')::integer,
      'runtime_enabled',(SELECT enabled FROM bot_runtime.runtime_control WHERE id=1),
      'scanner_phase',(SELECT payload::jsonb->>'phase' FROM bot_runtime.scanner_status WHERE id=1),
      'heartbeat_age_seconds',(SELECT extract(epoch FROM now())-max(occurred)
        FROM bot_runtime.activity_log WHERE event='Scanner heartbeat'),
      'largest_tables',(SELECT jsonb_agg(r) FROM (
        SELECT schemaname,relname,pg_total_relation_size(relid) AS bytes,n_live_tup AS estimated_rows
        FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10) r)
      ) AS health""")[0]['health']
    return dict(observed_at=time.time(), project=PROJECT,
                project_status=project.get('status', 'UNKNOWN'), **data)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def predicate(table, now):
    # Only these three established retention targets are ever writable.
    if table not in ('tiingo_requests', 'tiingo_responses', 'activity_log'):
        raise ValueError('Protected table')
    column, age = TABLES[table]
    cutoff = float(now) - age
    if not 0 < cutoff < 1e12:
        raise ValueError('Invalid retention clock')
    clause = f't.{column}>0 AND t.{column}<{cutoff}'
    if table == 'activity_log':
        clause += ' AND t.event IN (' + ','.join(map(literal, ROUTINE)) + ')'
    return clause


def delete_sql(table, candidates, now):
    condition = predicate(table, now)
    if not candidates or len(candidates) > 1000:
        raise ValueError('Invalid batch')
    payload = literal(json.dumps(candidates, separators=(',', ':')))
    return f"""BEGIN;
      SET LOCAL standard_conforming_strings=on;
      SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='15s';
      DO $$BEGIN
        IF NOT pg_try_advisory_xact_lock(714269099) THEN RAISE EXCEPTION 'Maintenance busy'; END IF;
        IF NOT EXISTS(SELECT 1 FROM bot_runtime.runtime_control
          WHERE id=1 AND installation='{INSTALLATION}' AND enabled)
          THEN RAISE EXCEPTION 'Wrong or inactive installation'; END IF;
      END $$;
      WITH saved AS (SELECT * FROM jsonb_to_recordset({payload}::jsonb)
        AS x(tid text, version text, row jsonb)),
      candidates AS (SELECT t.ctid,t.xmin FROM bot_runtime.{table} t JOIN saved s
        ON t.ctid=s.tid::tid AND t.xmin::text=s.version AND to_jsonb(t)=s.row
        WHERE {condition} LIMIT 1000 FOR UPDATE OF t SKIP LOCKED),
      deleted AS (DELETE FROM bot_runtime.{table} t USING candidates c
        WHERE t.ctid=c.ctid AND t.xmin=c.xmin RETURNING 1)
      SELECT count(*) AS deleted FROM deleted;
      COMMIT;"""


def retain(apply=False, directory=STATE, now=None):
    now = time.time() if now is None else now
    results = {}
    for table in TABLES:
        condition = predicate(table, now)
        eligible = readonly(f'SELECT count(*) AS n FROM bot_runtime.{table} t WHERE {condition}')[0]['n']
        result = dict(eligible=eligible, deleted=0)
        results[table] = result
        if not apply or not eligible:
            continue
        rows = readonly(f"SELECT t.ctid::text AS tid,t.xmin::text AS version,to_jsonb(t) AS row "
                        f"FROM bot_runtime.{table} t WHERE {condition} ORDER BY t.{TABLES[table][0]} LIMIT 1000")
        if not rows:
            continue
        # Bound upload/download amplification; no deletion when exceeded.
        if len(json.dumps(rows).encode()) > 2_000_000:
            result['blocked'] = 'Batch exceeds 2 MB; review a smaller batch'
            continue
        archive = directory / f'retention-{time.time_ns()}-{table}.json'
        private_json(archive, dict(project=PROJECT, table=table, cutoff=now-TABLES[table][1], rows=rows))
        # Operate on the read-back, verified archive, not an unpersisted selection.
        saved = json.loads(archive.read_text())['rows']
        result['archive'] = str(archive)
        # Never automatically retry an ambiguous mutation. The archive survives.
        result['deleted'] = query(delete_sql(table, saved, now))[0]['deleted']
        result['remaining_at_least'] = max(0, eligible-result['deleted'])
    return results


def main():
    # Keep module imports portable for tests; this operational CLI runs on Mac.
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['health', 'retention'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and args.action != 'retention':
        parser.error('--apply is only for retention')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = health() if args.action == 'health' else retain(args.apply)
        report = dict(action=args.action, apply=args.apply, observed_at=time.time(), result=result)
        path = STATE / f'{args.action}-{time.time_ns()}.json'
        private_json(path, report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
