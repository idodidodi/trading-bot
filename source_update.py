"""Verified source updates for the existing Linux service; never runs a scanner."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

REPOSITORY = 'idodidodi/trading-bot'
API = 'https://api.github.com/repos/' + REPOSITORY
UPDATE_LOCK = 714269003  # Separate from the production worker ownership lock.
REQUIRED = {'prepare', 'linux / linux', 'Workers Builds: family-trading-bot'}
TERMINAL = {'complete_first', 'stopped_favorable', 'expired', 'reported_exited', 'superseded', 'demo_exit'}
STATE_FILE = '.source-deployment.json'
POLICY = 'source-update-policy.json'
EXTRA_FILES = ('scanner.json', 'stock_universe.json', 'tradingview_template.pine',
               'requirements-runtime.txt', '.env.postgres.example', 'POSTGRES_MIGRATION.md', POLICY)


class UpdateRefused(ValueError):
    """Static, credential-free reason safe to show in updater status."""


@contextmanager
def activity_connection(store):
    """Serialize chase mutations with the updater's final idle check and stop."""
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock_shared(%s)', (UPDATE_LOCK,))
        yield db


def blockers(db):
    """Fail closed on missing schema, malformed state, or unknown activity states."""
    reasons = set()
    for (state,) in db.execute('SELECT state FROM bot_runtime.chasing_sessions'):
        if not isinstance(state, dict):
            reasons.add('unknown session'); continue
        status = state.get('status')
        if status == 'chasing':reasons.add('chase')
        elif status not in TERMINAL and status not in ('opposite', 'opposite_found'):
            reasons.add('unknown session')
        if status in ('opposite', 'opposite_found') and state.get('entry_at') is None:
            reasons.add('unknown session')
        lookback = state.get('lookback') or {}
        if lookback.get('status') not in (None, 'complete', 'cancelled'):
            reasons.add('lookback')
        position = state.get('position')
        if position and position.get('status') != 'closed':reasons.add('live position')
        elif state.get('entry_at') is not None and status != 'reported_exited':
            reasons.add('live position')
    if db.execute("SELECT EXISTS(SELECT 1 FROM bot_runtime.chasing_outbox WHERE status='pending')").fetchone()[0]:
        reasons.add('pending chase delivery')
    return sorted(reasons)


def get_json(url, timeout=20):
    request = urllib.request.Request(url, headers={'User-Agent': 'TradingBot-SourceUpdater',
        'Accept': 'application/vnd.github+json', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(16*1024*1024+1)
    if len(raw)>16*1024*1024:raise UpdateRefused('Metadata too large')
    return json.loads(raw)


def verified_target():
    commit = get_json(API+'/commits/main')['sha']
    if not re.fullmatch('[0-9a-f]{40}', commit):raise UpdateRefused('Invalid commit')
    checks = get_json(API+f'/commits/{commit}/check-runs?per_page=100')
    if checks.get('total_count', 0)>100:raise UpdateRefused('Incomplete checks')
    latest = {}
    for check in checks['check_runs']:
        if check['name'] not in latest or check['id']>latest[check['name']]['id']:
            latest[check['name']] = check
    for name in REQUIRED:
        c = latest.get(name, {})
        if c.get('status')!='completed' or c.get('conclusion')!='success':
            raise UpdateRefused('Required verification incomplete')
        provider='cloudflare-workers-and-pages' if name=='Workers Builds: family-trading-bot' else 'github-actions'
        if c.get('app',{}).get('slug')!=provider:
            raise UpdateRefused('Unexpected verification provider')
    if any(c.get('status')!='completed' or c.get('conclusion') not in ('success','skipped','neutral') for c in latest.values()):
        raise UpdateRefused('A commit check is pending or unsuccessful')
    runs = get_json(API+f'/actions/workflows/standalone-release.yml/runs?head_sha={commit}&branch=main&event=push&per_page=10')
    matching = [r for r in runs['workflow_runs'] if r['head_sha']==commit and r['head_branch']=='main' and r['event']=='push']
    if not matching or max(matching,key=lambda r:r['id']).get('conclusion')!='success':
        raise UpdateRefused('Main workflow is not successful')
    tree = get_json(API+f'/git/trees/{commit}?recursive=1')
    if tree.get('truncated'):raise UpdateRefused('Incomplete source tree')
    return commit, tree['tree']


def runtime_file(name):
    return (('/' not in name and name.endswith('.py') and not name.startswith(('test_', 'postgres_test_')))
            or name.startswith(('web/', 'skills/')) or name=='cloud/browser-config.json' or name in EXTRA_FILES)


def compatibility(tree):
    return {r['path']:r['sha'] for r in tree if r['type']=='blob' and
        (r['path']=='requirements-runtime.txt' or r['path']==POLICY or
         (r['path'].startswith('cloud/') and r['path'].endswith('.sql')))}


def blob_hash(raw):
    return hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()


def extract_verified(archive, stage, tree):
    wanted = {r['path']:r for r in tree if r['type']=='blob' and runtime_file(r['path'])}
    found = set()
    with tarfile.open(archive) as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not(member.isdir() or member.isfile()):
                raise UpdateRefused('Unsafe source archive')
            name = '/'.join(path.parts[1:])
            if member.isdir() or name not in wanted:continue
            if name in found or member.size>32*1024*1024 or wanted[name]['mode']!='100644':
                raise UpdateRefused('Unexpected source file')
            raw = source.extractfile(member).read()
            if blob_hash(raw)!=wanted[name]['sha']:raise UpdateRefused('Source checksum mismatch')
            dest = stage/name;dest.parent.mkdir(parents=True, exist_ok=True);dest.write_bytes(raw)
            found.add(name)
    if found != set(wanted):raise UpdateRefused('Missing source files')
    policy = json.loads((stage/POLICY).read_text())
    if policy != {'format': 1, 'runtime': 'signal-service-v2'}:raise UpdateRefused('Unsupported source policy')
    for path in stage.glob('*.py'):compile(path.read_bytes(), str(path), 'exec')
    return sorted(found)


def download(commit, dest):
    request = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/tarball/{commit}',
        headers={'User-Agent': 'TradingBot-SourceUpdater'})
    with urllib.request.urlopen(request, timeout=30) as response, dest.open('wb') as output:
        total = 0
        while True:
            chunk = response.read(1024*1024)
            if not chunk:break
            total += len(chunk)
            if total>100*1024*1024:raise UpdateRefused('Source archive too large')
            output.write(chunk)


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8');temp.chmod(0o600);temp.replace(path)


def replace_files(root, stage, old_files, new_files):
    for name in sorted(set(old_files)-set(new_files)):
        path=root/name
        if path.exists():path.unlink()
    for name in new_files:
        dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True)
        temp=dest.with_name(dest.name+'.update-tmp');shutil.copy2(stage/name,temp);temp.replace(dest)


def source_manifest(root):
    # The running updater may have imported the previous packaging version.
    from version_info import build_manifest
    result=build_manifest(root)
    version=root/'app_version.py'
    if version.exists():
        tree=ast.parse(version.read_text(encoding='utf-8'))
        result['version']=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='VERSION' for t in n.targets))
    return result


class Service:
    def __init__(self, root, name, db=None):self.root=root;self.name=name;self.db=db
    def control(self, action):
        return subprocess.check_output(['systemctl','--user',action,self.name],text=True,stderr=subprocess.DEVNULL,timeout=120).strip()
    def healthy(self, expected):
        from version_info import build_manifest
        if source_manifest(self.root)!=expected:return False
        port=int(os.environ.get('PORT','8080'))
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            try:
                health=get_json(f'http://127.0.0.1:{port}/health',timeout=2)
                manifest=get_json(f'http://127.0.0.1:{port}/web/version.json',timeout=2)
                if health.get('ok') and manifest==expected and self.control('is-active')=='active':
                    import psycopg
                    from postgres_store import settings
                    query="SELECT count(*) FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid WHERE a.usename=current_user AND l.locktype='advisory' AND l.classid=0 AND l.objid=714269002 AND l.objsubid=1 AND l.granted"
                    # Supavisor hides application_name. The ownership advisory lock
                    # is authoritative, and reuse avoids consuming a sixth slot.
                    if self.db is not None and not self.db.closed:
                        owners=self.db.execute(query).fetchone()[0]
                    else:
                        with psycopg.connect(settings(),connect_timeout=5,autocommit=True,options='-c statement_timeout=5000') as db:
                            owners=db.execute(query).fetchone()[0]
                    if owners==1:return True
            except Exception:pass
            time.sleep(1)
        return False


def install(root, stage, target, files, db, service, bootstrap=False):
    """Idle gate covers graceful stop; this process never acquires worker ownership."""
    from version_info import build_manifest
    before=json.loads((root/STATE_FILE).read_text()) if (root/STATE_FILE).exists() else None
    if before is None and not bootstrap:raise UpdateRefused('Manual bootstrap required')
    if before and not bootstrap:
        if before['compatibility']!=target['compatibility']:raise UpdateRefused('Manual dependency/schema cutover required')
        if before['manifest']!=source_manifest(root):raise UpdateRefused('Installed source was modified locally')
    if not db.execute('SELECT pg_try_advisory_lock(%s)',(UPDATE_LOCK,)).fetchone()[0]:return 'postponed: activity transaction'
    stopped=False;changed=False;backup=None;failure=None;outcome='installed'
    old_files=[];previous_manifest=source_manifest(root)
    try:
        busy=blockers(db)
        if busy:return 'postponed: '+', '.join(busy)
        if service.control('is-active')!='active':raise UpdateRefused('Worker is not active')
        backup=root.parent/'trading-source-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup.mkdir(mode=0o700,parents=True);backup.parent.chmod(0o700)
        candidates=list(root.glob('*'))+list((root/'web').rglob('*'))+list((root/'skills').rglob('*'))+[root/'cloud/browser-config.json']
        old_files=sorted({p.relative_to(root).as_posix() for p in candidates
            if p.is_file() and not p.is_symlink() and runtime_file(p.relative_to(root).as_posix())})
        for name in old_files:
            dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/name,dest)
        if before:atomic_json(backup/STATE_FILE,before)
        stopped=True;service.control('stop')
        # Older bootstrap workers do not participate in the gate. Recheck after stop.
        busy=blockers(db)
        if busy:outcome='postponed: '+', '.join(busy)
        else:
            changed=True
            replace_files(root,stage,old_files,files)
            atomic_json(root/STATE_FILE,dict(**target,files=files,manifest=source_manifest(root),backup=str(backup)))
    except Exception as error:failure=error
    finally:
        try:db.execute('SELECT pg_advisory_unlock(%s)',(UPDATE_LOCK,))
        except Exception as error:failure=failure or error
    if not stopped:
        if failure:raise failure
        return outcome
    try:
        if failure:raise failure
        service.control('start')
        expected=source_manifest(root) if changed else previous_manifest
        if not service.healthy(expected):raise RuntimeError('Worker health verification failed')
    except Exception:
        service.control('stop')
        if changed:
            replace_files(root,backup,files,old_files)
            if before:atomic_json(root/STATE_FILE,before)
            else:(root/STATE_FILE).unlink(missing_ok=True)
        service.control('start')
        if not service.healthy(previous_manifest):raise RuntimeError('Update and rollback health verification failed')
        raise RuntimeError('Update failed; previous source restored') from None
    return outcome


def due(state, now):
    """Five-minute timer checks daily Israel window, retrying a postponed update."""
    from zoneinfo import ZoneInfo
    local=now.astimezone(ZoneInfo('Asia/Jerusalem'))
    today=local.date().isoformat()
    if state.get('completed_date')==today:return False
    # An outstanding scheduled attempt keeps retrying across midnight.
    if state.get('outcome')=='failed' or state.get('outcome','').startswith('postponed:'):return True
    return (local.hour,local.minute)>=(2,39)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--service',default='trading-bot-postgres.service')
    parser.add_argument('--bootstrap',action='store_true')
    parser.add_argument('--check',action='store_true',help='Read-only eligibility check; no installation')
    args=parser.parse_args();root=args.root.resolve()
    from platform_app import load_env
    load_env()
    import fcntl
    import psycopg
    from postgres_store import settings
    state_dir=Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'trading-bot-updater'
    state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (state_dir/'lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        status_file=state_dir/'status.json';state=json.loads(status_file.read_text()) if status_file.exists() else {}
        now=datetime.now(timezone.utc)
        if not args.bootstrap and not args.check and not due(state,now):return
        try:
            with psycopg.connect(settings(),autocommit=True,connect_timeout=5,
                 options='-c statement_timeout=15000 -c lock_timeout=5000',application_name='trading-bot-updater') as db:
                busy=blockers(db)
                if busy and not args.check:
                    outcome='postponed: '+', '.join(busy)
                    atomic_json(status_file,dict(checked_at=now.isoformat(),outcome=outcome));print(outcome);return
                commit,tree=verified_target()
                target=dict(commit=commit,compatibility=compatibility(tree))
                installed=json.loads((root/STATE_FILE).read_text()) if (root/STATE_FILE).exists() else {}
                if args.check:
                    print(json.dumps(dict(commit=commit,installed=installed.get('commit'),blockers=busy)));return
                if busy:outcome='postponed: '+', '.join(busy)
                elif installed.get('commit')==commit:outcome='unchanged'
                else:
                    if installed and not args.bootstrap:
                        comparison=get_json(API+f"/compare/{installed['commit']}...{commit}")
                        if comparison.get('status')!='ahead':raise UpdateRefused('Automatic downgrade/divergence refused')
                    if installed and not args.bootstrap and installed['compatibility']!=target['compatibility']:
                        raise UpdateRefused('Manual dependency/schema cutover required')
                    with tempfile.TemporaryDirectory(prefix='trading-source-') as temp:
                        temp=Path(temp);archive=temp/'source.tar';stage=temp/'source';stage.mkdir()
                        download(commit,archive);files=extract_verified(archive,stage,tree)
                        outcome=install(root,stage,target,files,db,Service(root,args.service,db),args.bootstrap)
                result=dict(checked_at=now.isoformat(),commit=commit,outcome=outcome)
                if outcome in ('installed','unchanged'):
                    from zoneinfo import ZoneInfo
                    result['completed_date']=now.astimezone(ZoneInfo('Asia/Jerusalem')).date().isoformat()
                atomic_json(status_file,result);print(outcome)
        except Exception as error:
            # Provider/DB exceptions can include credential-bearing URLs: never print them.
            atomic_json(status_file,dict(checked_at=now.isoformat(),outcome='failed',error=type(error).__name__,reason=str(error) if isinstance(error,UpdateRefused) else 'External operation failed'))
            print('Source update failed; inspect updater state and verify the running service.',flush=True)
            raise SystemExit(1)


if __name__=='__main__':main()
