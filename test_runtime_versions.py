from test_postgres_support import TestStore as Store, PostgresTestCase
import json
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from platform_app import load_env
import runtime_env as runtime
import version_info as versions


class RuntimeTests(PostgresTestCase):
    def test_cloud_sync_defaults_to_production_with_explicit_development_override(self):
        for settings,expected in [({},False),({'DEV_CLOUD_SYNC_ENABLED':'false'},False),
                                  ({'DEV_CLOUD_SYNC_ENABLED':'true'},False),
                                  ({'TRADING_BOT_ENV':'prod','DEV_CLOUD_SYNC_ENABLED':'false'},False)]:
            with self.subTest(settings=settings),patch.dict(os.environ,settings,clear=True):
                self.assertEqual(runtime.cloud_sync_enabled(),expected)


    def test_source_ignores_production_paths_ports_and_delivery_defaults(self):
        with patch.dict(os.environ, {'DATA_DIR':'/production', 'PORT':'8080', 'DRY_RUN':'false',
                                     'PAPER_TRADING_ENABLED':'true'}, clear=True):
            self.assertEqual(runtime.configure('/source'), 'dev')
            self.assertEqual(Path(os.environ['DATA_DIR']), Path('/source/data/dev'))
            self.assertEqual(os.environ['PORT'], '8082')
            self.assertEqual(os.environ['DRY_RUN'], 'true')
            self.assertEqual(os.environ['PAPER_TRADING_ENABLED'], 'false')
            self.assertFalse(runtime.cloud_sync_enabled())

    def test_executable_always_production_and_source_service_can_opt_in(self):
        with patch.dict(os.environ, {'TRADING_BOT_ENV':'dev','DATA_DIR':'/production','PORT':'8080'}, clear=True), patch('sys.frozen',True,create=True):
            self.assertEqual(runtime.configure('/bundle'), 'prod')
            self.assertEqual(Path(os.environ['DATA_DIR']), Path('/production'))
            self.assertEqual(os.environ['PORT'], '8080')
        with patch.dict(os.environ, {'TRADING_BOT_ENV':'prod'}, clear=True):
            self.assertEqual(runtime.data_directory('/source'), Path('/source/data'))

    def test_development_can_opt_in_to_telegram_without_enabling_production(self):
        with patch.dict(os.environ, {'DEV_TELEGRAM_ENABLED':'true', 'PAPER_TRADING_ENABLED':'true'}, clear=True):
            self.assertEqual(runtime.configure('/source'), 'dev')
            self.assertEqual(os.environ['DRY_RUN'], 'false')
            self.assertEqual(os.environ['PAPER_TRADING_ENABLED'], 'false')
            self.assertEqual(Path(os.environ['DATA_DIR']), Path('/source/data/dev'))
            self.assertEqual(os.environ['PORT'], '8082')
            self.assertFalse(runtime.cloud_sync_enabled())

    def test_only_production_writes_activity_log(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.db')
            with patch.dict(os.environ, {'TRADING_BOT_ENV':'dev'}), patch('builtins.print') as console:
                store.log('Development','must not persist')
                console.assert_called_once()
                self.assertRegex(console.call_args.args[0], r'^\[DEV \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}\] Development: must not persist$')
                self.assertTrue(console.call_args.kwargs['flush'])
            with store.connect() as db:
                self.assertIsNone(db.execute("SELECT 1 FROM activity_log LIMIT 1").fetchone())
            with patch.dict(os.environ, {'TRADING_BOT_ENV':'prod'}), patch('builtins.print') as console:
                store.log('Production','persist')
                console.assert_not_called()
            with patch.dict(os.environ, {'TRADING_BOT_ENV':'dev'}):
                store.log('Development','still must not persist')
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT event FROM activity_log').fetchall(), [('Production',)])

    def test_credentials_file_cannot_select_production(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch('platform_app.ROOT',Path(folder)):
            Path(folder,'.env').write_text('TRADING_BOT_ENV=prod\nDRY_RUN=false\n')
            load_env()
            self.assertEqual(runtime.environment(),'dev')


class VersionTests(PostgresTestCase):
    def test_fingerprints_detect_component_changes_and_ignore_credentials_tests(self):
        original={'web/app.js':'a','scanner.py':'b','.env':'secret','test_scanner.py':'c'}
        first=versions.fingerprints(original)
        self.assertEqual(first, versions.fingerprints(original | {'app_version.py':'ci-build-version'}))
        self.assertEqual(first,versions.fingerprints(original|{'.env':'other','test_scanner.py':'changed'}))
        self.assertNotEqual(first['ui'],versions.fingerprints(original|{'web/app.js':'new'})['ui'])
        self.assertEqual(first['scanner'],versions.fingerprints(original|{'web/app.js':'new'})['scanner'])
        self.assertNotEqual(first['scanner'],versions.fingerprints(original|{'scanner.py':'new'})['scanner'])

    def test_github_uses_one_commit_and_rejects_truncated_tree(self):
        with patch.object(versions,'get_json',side_effect=[[{'sha':'abc'}],{'tree':[{'type':'blob','path':'scanner.py','sha':'s'}]}]) as get:
            self.assertEqual(versions.github_versions()['commit'],'abc')
            self.assertIn('/git/trees/abc?',get.call_args.args[0])
        with patch.object(versions,'get_json',side_effect=[[{'sha':'abc'}],{'truncated':True}]):
            with self.assertRaises(ValueError):versions.github_versions()

    def test_offline_still_reports_running_versions(self):
        with patch.object(versions,'local_versions',return_value={'version':'test','ui':'a','scanner':'b'}), patch.object(versions,'get_json',side_effect=OSError('private URL')), patch.dict(os.environ,{},clear=True):
            result=versions.versions()
            self.assertEqual(result['local']['scanner'],'b')
            self.assertIn('error',result['github'])
            self.assertNotIn('private URL',json.dumps(result))

    def test_frozen_reads_bundle_manifest_instead_of_mutable_install_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'web').mkdir();(root/'web/version.json').write_text('{"version":"built","scanner":"immutable"}')
            versions.local_versions.cache_clear()
            try:
                with patch('sys.frozen',True,create=True),patch('sys._MEIPASS',folder,create=True):
                    self.assertEqual(versions.local_versions()['scanner'],'immutable')
            finally:versions.local_versions.cache_clear()
