import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import signal_desktop_model as model

class DesktopTests(unittest.TestCase):
    def test_opening_controller_does_not_start_process_or_create_config(self):
        with tempfile.TemporaryDirectory() as folder,patch('subprocess.Popen') as launch:
            home=Path(folder)/'fresh';controller=model.Controller(home)
            self.assertEqual(controller.status('worker'),'Stopped');self.assertFalse(home.exists());launch.assert_not_called()
    def test_separate_app_directory_and_private_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';values=model.DEFAULTS|dict(TRADING_DATABASE_URL='private',SIGNAL_ADMIN_TOKEN='x'*32)
            model.save_settings(path,values);self.assertEqual(model.read_settings(path),values)
            if os.name!='nt':self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(model.app_directory().name,'SignalService')
    def test_broker_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):model.save_settings(Path(folder)/'settings.json',model.DEFAULTS|dict(EXECUTION_ENABLED='true'))
    def test_start_uses_child_process_and_stop_is_cooperative(self):
        with tempfile.TemporaryDirectory() as folder,patch('subprocess.Popen') as launch:
            home=Path(folder);controller=model.Controller(home)
            model.save_settings(controller.config,model.DEFAULTS|dict(TRADING_DATABASE_URL='secret-dsn',SIGNAL_ADMIN_TOKEN='s'*32))
            launch.return_value.poll.return_value=None
            controller.start('api');args=launch.call_args.args[0]
            self.assertNotIn('secret-dsn',' '.join(args));self.assertNotIn('s'*32,' '.join(args))
            controller.stop('api');self.assertTrue((home/'api.stop').exists())
            launch.return_value.terminate.assert_not_called()
    def test_shutdown_has_no_removed_paper_thread_reference(self):
        self.assertNotIn('paper_thread',Path('platform_app.py').read_text())
