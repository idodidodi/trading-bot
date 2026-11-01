import io
import json
from datetime import datetime, timezone
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import psycopg
import source_update as update
from test_postgres_support import PostgresTestCase, TestStore, test_dsn


class SourceTests(unittest.TestCase):
    def test_daily_window_and_dst_follow_israel_time(self):
        for month,utc_hour in ((1,0),(7,23)):
            # Summer 02:39 is the previous UTC date at 23:39.
            day=2 if month==1 else 1
            before=datetime(2026,month,day,utc_hour,38,tzinfo=timezone.utc)
            after=before.replace(minute=39)
            self.assertFalse(update.due({},before));self.assertTrue(update.due({},after))
            from zoneinfo import ZoneInfo
            date=after.astimezone(ZoneInfo('Asia/Jerusalem')).date().isoformat()
            self.assertFalse(update.due({'completed_date':date},after))

    def test_health_timeout_is_bounded_before_supervisor_deadline(self):
        service=update.Service(Path('.'),'unused')
        expected=update.source_manifest(Path('.'))
        with patch.object(update.time,'monotonic',side_effect=[0,1,91]), \
             patch.object(update.time,'sleep'), \
             patch.object(update,'get_json',side_effect=TimeoutError) as fetch:
            self.assertFalse(service.healthy(expected))
            self.assertEqual(fetch.call_args.kwargs['timeout'],2)

    def test_source_version_is_read_from_new_source_after_update(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'app_version.py').write_text("VERSION = '8.2.0'\n")
            self.assertEqual(update.source_manifest(root)['version'],'8.2.0')

    def test_archive_hashes_and_unsafe_members_are_rejected(self):
        policy=b'{"format":1,"runtime":"postgres-source-v1"}'
        files={'source-update-policy.json':policy,'scanner.py':b'pass\n'}
        tree=[dict(path=k,sha=update.blob_hash(v),type='blob',mode='100644') for k,v in files.items()]
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);archive=d/'a.tar'
            def tar(extra=None,corrupt=False):
                with tarfile.open(archive,'w') as t:
                    for name,raw in files.items():
                        raw=raw+b'bad' if corrupt else raw
                        m=tarfile.TarInfo('repo/'+name);m.size=len(raw);t.addfile(m,io.BytesIO(raw))
                    if extra:
                        m=tarfile.TarInfo(extra);m.size=0;t.addfile(m,io.BytesIO())
            stage=d/'stage';stage.mkdir();tar()
            self.assertEqual(update.extract_verified(archive,stage,tree),sorted(files))
            tar(corrupt=True)
            with self.assertRaisesRegex(ValueError,'checksum'):update.extract_verified(archive,stage,tree)
            tar('../escape')
            with self.assertRaisesRegex(ValueError,'Unsafe'):update.extract_verified(archive,stage,tree)

    def test_only_exact_successful_linux_and_cloudflare_checks_qualify(self):
        sha='a'*40
        checks=[dict(id=i,name=n,status='completed',conclusion='success',app=dict(slug='cloudflare-workers-and-pages' if n=='Workers Builds: family-trading-bot' else 'github-actions')) for i,n in enumerate(update.REQUIRED)]
        run=dict(id=1,head_sha=sha,head_branch='main',event='push',conclusion='success')
        def response(url):
            if url.endswith('/commits/main'):return dict(sha=sha)
            if '/check-runs?' in url:return dict(total_count=len(checks),check_runs=checks)
            if '/actions/' in url:return dict(workflow_runs=[run])
            return dict(truncated=False,tree=[])
        with patch.object(update,'get_json',side_effect=response):
            self.assertEqual(update.verified_target()[0],sha)
            checks[0]['status']='in_progress'
            with self.assertRaises(ValueError):update.verified_target()
            checks[0]['status']='completed';checks[0]['conclusion']='failure'
            with self.assertRaises(ValueError):update.verified_target()
        self.assertFalse(any('windows' in name for name in update.REQUIRED))


class FakeService:
    def __init__(self, root, failure=False):self.root=root;self.calls=[];self.failure=failure
    def control(self, action):self.calls.append(action);return 'active' if action=='is-active' else ''
    def healthy(self, expected):
        return not(self.failure and (self.root/'scanner.py').read_text()=='new = True\n')


class DatabaseTests(PostgresTestCase):
    def setUp(self):self.store=TestStore()
    def add(self,state):
        with self.store.connect() as db:
            db.execute('INSERT INTO chasing_sessions VALUES(%s,%s,%s,%s)',('test','00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001',json.dumps(state)))
    def test_all_activity_types_and_unconfirmed_exits_block(self):
        for state,expected in [({'status':'chasing'},'chase'),
            ({'status':'complete_first','lookback':{'status':'pending'}},'lookback'),
            ({'status':'opposite','entry_at':10,'position':{'status':'awaiting_reference'}},'live position'),
            ({'status':'opposite_found','entry_at':10,'position':{'status':'exit_alerted'}},'live position'),
            ({'status':'unknown'},'unknown session')]:
            self.add(state)
            with self.store.connect() as db:
                self.assertIn(expected,update.blockers(db));db.execute('DELETE FROM chasing_sessions')
        self.add({'status':'reported_exited','entry_at':10,'position':{'status':'closed'}})
        with self.store.connect() as db:self.assertEqual(update.blockers(db),[])

    def test_real_shared_gate_prevents_exclusive_update(self):
        with update.activity_connection(self.store), psycopg.connect(test_dsn(),autocommit=True) as db:
            self.assertFalse(db.execute('SELECT pg_try_advisory_lock(%s)',(update.UPDATE_LOCK,)).fetchone()[0])
        with psycopg.connect(test_dsn(),autocommit=True) as db:
            self.assertTrue(db.execute('SELECT pg_try_advisory_lock(%s)',(update.UPDATE_LOCK,)).fetchone()[0])
            db.execute('SELECT pg_advisory_unlock(%s)',(update.UPDATE_LOCK,))

    def test_missing_schema_or_connection_failure_never_stops_service(self):
        with tempfile.TemporaryDirectory() as d:
            db=Mock();db.execute.side_effect=[Mock(fetchone=lambda:(True,)),RuntimeError('unavailable'),Mock()]
            service=FakeService(Path(d))
            with self.assertRaises(RuntimeError):
                update.install(Path(d),Path(d),{},[],db,service,bootstrap=True)
            self.assertEqual(service.calls,[])

    def test_incompatible_schema_and_dependency_baseline_never_stops_service(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);service=FakeService(root)
            (root/update.STATE_FILE).write_text(json.dumps(dict(compatibility={'requirements':'old'})))
            with self.assertRaisesRegex(ValueError,'Manual'):
                update.install(root,root,dict(compatibility={'requirements':'new'}),[],Mock(),service)
            self.assertEqual(service.calls,[])

    def test_active_chase_never_stops_service(self):
        self.add({'status':'chasing'})
        with tempfile.TemporaryDirectory() as d, psycopg.connect(test_dsn(),autocommit=True) as db:
            root=Path(d);service=FakeService(root)
            outcome=update.install(root,root,{},[],db,service,bootstrap=True)
            self.assertIn('postponed',outcome);self.assertEqual(service.calls,[])

    def test_install_preserves_private_state_and_rolls_back_bad_startup(self):
        for failure in (False,True):
            with tempfile.TemporaryDirectory() as d, psycopg.connect(test_dsn(),autocommit=True) as db:
                root=Path(d)/'install';stage=Path(d)/'stage';root.mkdir();stage.mkdir()
                (root/'scanner.py').write_text('old = True\n');(stage/'scanner.py').write_text('new = True\n')
                (root/'data').mkdir();(root/'data/private').write_text('retain')
                service=FakeService(root,failure)
                if failure:
                    with self.assertRaisesRegex(RuntimeError,'restored'):
                        update.install(root,stage,dict(commit='new',compatibility={}),['scanner.py'],db,service,bootstrap=True)
                    self.assertEqual((root/'scanner.py').read_text(),'old = True\n')
                else:
                    self.assertEqual(update.install(root,stage,dict(commit='new',compatibility={}),['scanner.py'],db,service,bootstrap=True),'installed')
                    self.assertEqual(json.loads((root/update.STATE_FILE).read_text())['commit'],'new')
                self.assertEqual((root/'data/private').read_text(),'retain')
                self.assertEqual(service.calls.count('stop'),2 if failure else 1)

    def test_database_disconnect_after_stop_still_restores_and_restarts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'install';stage=Path(d)/'stage';root.mkdir();stage.mkdir()
            (root/'scanner.py').write_text('old = True\n');(stage/'scanner.py').write_text('new = True\n')
            db=Mock();db.execute.return_value.fetchone.return_value=(True,)
            def execute(query,*args):
                if 'pg_advisory_unlock' in query:raise RuntimeError('database disconnected')
                return Mock(fetchone=lambda:(True,))
            db.execute.side_effect=execute;service=FakeService(root)
            with patch.object(update,'blockers',return_value=[]),self.assertRaisesRegex(RuntimeError,'restored'):
                update.install(root,stage,dict(commit='new',compatibility={}),['scanner.py'],db,service,bootstrap=True)
            self.assertEqual((root/'scanner.py').read_text(),'old = True\n')
            self.assertEqual(service.calls[-1],'start')

    def test_bootstrap_rechecks_activity_after_stopping_old_worker(self):
        with tempfile.TemporaryDirectory() as d, psycopg.connect(test_dsn(),autocommit=True) as db:
            root=Path(d);service=FakeService(root)
            original=service.control
            def control(action):
                if action=='stop':self.add({'status':'chasing'})
                return original(action)
            service.control=control
            self.assertIn('postponed',update.install(root,root,{},[],db,service,bootstrap=True))
            self.assertFalse((root/update.STATE_FILE).exists())
            self.assertEqual(service.calls,['is-active','stop','start'])
