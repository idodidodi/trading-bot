import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import linux_launcher as launcher
import linux_update as updater
import windows_update as updates


class LinuxStandaloneTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','XDG absolute paths use POSIX semantics')
    def test_standard_secrets_location_and_custom_path_validation(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(launcher.settings_path(),Path.home()/'.config/FamilyTradingBot/.env')
        with patch.dict(os.environ,{'XDG_CONFIG_HOME':'relative'},clear=True):
            self.assertEqual(launcher.settings_path(),Path.home()/'.config/FamilyTradingBot/.env')
        with patch.dict(os.environ,{'XDG_CONFIG_HOME':'/tmp/config with spaces'},clear=True):
            self.assertEqual(launcher.settings_path(),Path('/tmp/config with spaces/FamilyTradingBot/.env'))
        with patch.dict(os.environ,{'TRADING_BOT_ENV_FILE':'relative.env'},clear=True):
            with self.assertRaisesRegex(ValueError,'absolute'):launcher.settings_path()

    def test_migrates_legacy_secrets_without_overwriting_or_moving_data(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'XDG_CONFIG_HOME':folder+'/config'},clear=True):
            root=Path(folder)/'application';root.mkdir()
            (root/'.env').write_text('TELEGRAM_BOT_TOKEN=legacy\n')
            (root/'data').mkdir();(root/'data/history').write_text('keep')
            settings=launcher.prepare_settings(root,Path(folder)/'bundle')
            self.assertEqual(settings.read_text(),'TELEGRAM_BOT_TOKEN=legacy\n')
            if os.name=='posix':self.assertEqual(settings.stat().st_mode&0o777,0o600)
            settings.write_text('TELEGRAM_BOT_TOKEN=new\n')
            launcher.prepare_settings(root,Path(folder)/'bundle')
            self.assertIn('new',settings.read_text())
            self.assertIn('legacy',(root/'.env').read_text())
            self.assertEqual((root/'data/history').read_text(),'keep')

    def test_new_install_is_dry_run_and_existing_secrets_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'XDG_CONFIG_HOME':folder+'/config'},clear=True):
            bundle=Path(folder)/'bundle';bundle.mkdir()
            (bundle/'.env.example').write_text('DRY_RUN=false\n')
            settings=launcher.prepare_settings(Path(folder)/'app',bundle)
            self.assertEqual(settings.read_text(),'DRY_RUN=true\n')
            launcher.prepare_settings(Path(folder)/'app',bundle)
            self.assertEqual(settings.read_text(),'DRY_RUN=true\n')

    def test_external_env_and_data_paths_and_environment_precedence(self):
        with tempfile.TemporaryDirectory(prefix='family storage ') as folder:
            base=Path(folder);settings=base/'secrets.env'
            settings.write_text('DATA_DIR="'+str(base/'external data')+'"\nDRY_RUN=true\n')
            for override in (None,str(base/'environment data')):
                env={'TRADING_BOT_ENV_FILE':str(settings)}
                if override:env['DATA_DIR']=override
                with patch.dict(os.environ,env,clear=True):
                    launcher.configure_storage(base/'application',base/'bundle')
                    expected=Path(override) if override else base/'external data'
                    self.assertEqual(Path(os.environ['DATA_DIR']),expected)
                    self.assertTrue(expected.is_dir())
                    self.assertEqual(os.environ['DRY_RUN'],'true')
            with patch.dict(os.environ,{'TRADING_BOT_ENV_FILE':str(base/'missing.env')},clear=True):
                with self.assertRaises(FileNotFoundError):launcher.prepare_settings(base,base)
            settings.write_text('DATA_DIR=relative\n')
            with patch.dict(os.environ,{'TRADING_BOT_ENV_FILE':str(settings)},clear=True):
                with self.assertRaisesRegex(ValueError,'absolute'):launcher.configure_storage(base,base)

    @unittest.skipUnless(os.name=='posix','Linux paths and loader variables use POSIX semantics')
    def test_persistent_root_and_native_environment(self):
        with patch.dict(os.environ,{'XDG_DATA_HOME':'/tmp/family linux','LD_LIBRARY_PATH':'/bundle','LD_LIBRARY_PATH_ORIG':'/system'}):
            self.assertEqual(launcher.app_root(),Path('/tmp/family linux/FamilyTradingBot'))
            self.assertEqual(launcher.external_environment()['LD_LIBRARY_PATH'],'/system')
        with patch.dict(os.environ,{'XDG_DATA_HOME':'relative'},clear=True):
            self.assertEqual(launcher.app_root(),Path.home()/'.local/share/FamilyTradingBot')
            self.assertNotIn('LD_LIBRARY_PATH',launcher.external_environment())

    def test_linux_support_requires_a_frozen_x86_64_app(self):
        with patch('sys.platform','linux'),patch('sys.frozen',True,create=True),patch('platform.machine',return_value='x86_64'):
            self.assertTrue(updates.status()['supported'])
            self.assertEqual(updates.status()['platform'],'Linux')
            with patch('platform.machine',return_value='aarch64'):self.assertFalse(updates.supported())

    def test_download_checks_linux_architecture_checksum_and_origin(self):
        binary=b'\x7fELF\x02\x01'+b'\0'*12+b'\x3e\x00'+b'test'
        release=dict(tag_name='v99.0.0',assets=[dict(name=name,size=len(binary),browser_download_url=f'https://github.com/{updates.REPOSITORY}/releases/download/v99.0.0/{name}') for name in (updater.ASSET,updater.MANIFEST)])
        manifest=dict(version='99.0.0',platform='linux-x86_64',size=len(binary),sha256=hashlib.sha256(binary).hexdigest())
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch('windows_update.get_json',return_value=manifest),patch('linux_update.urllib.request.urlopen',side_effect=lambda *args,**kw:io.BytesIO(binary)):
                staged=updater.download_release(root,release)
                self.assertEqual(staged.read_bytes(),binary)
                if os.name=='posix':self.assertTrue(staged.stat().st_mode&0o100)
            with patch('windows_update.get_json',return_value=manifest|dict(sha256='0'*64)),patch('linux_update.urllib.request.urlopen',return_value=io.BytesIO(binary)):
                with self.assertRaisesRegex(ValueError,'checksum'):updater.download_release(root,release)
                self.assertFalse((root/'FamilyTradingBot.new').exists())
            arm=binary[:18]+b'\xb7\x00'+binary[20:]
            with patch('windows_update.get_json',return_value=manifest|dict(sha256=hashlib.sha256(arm).hexdigest())),patch('linux_update.urllib.request.urlopen',return_value=io.BytesIO(arm)):
                with self.assertRaisesRegex(ValueError,'x86_64'):updater.download_release(root,release)
            release['assets'][0]['browser_download_url']='https://untrusted.invalid/program'
            with patch('windows_update.get_json',return_value=manifest),self.assertRaisesRegex(ValueError,'Untrusted'):
                updater.download_release(root,release)

    def test_installer_is_detached_before_shutdown_and_preserves_launch_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            staged=Path(folder)/'FamilyTradingBot.new'
            staged.write_bytes(b'verified binary')
            order=[]
            env={'PYINSTALLER_RESET_ENVIRONMENT':'1','PAPER_TRADING_ENABLED':'false',
                 'TRADING_BOT_ENV_FILE':'/external/settings.env','DATA_DIR':'/external/data'}
            with patch('linux_launcher.external_environment',return_value=env), \
                 patch('linux_update.subprocess.Popen',side_effect=lambda *args,**kwargs:order.append('installer')) as spawn:
                updater.install(staged,lambda:order.append('shutdown'))
            self.assertEqual(order,['installer','shutdown'])
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            self.assertTrue(spawn.call_args.kwargs['close_fds'])
            self.assertEqual(spawn.call_args.kwargs['stdin'],subprocess.DEVNULL)
            self.assertEqual(spawn.call_args.kwargs['stderr'],subprocess.STDOUT)
            self.assertTrue((staged.parent/'update.log').is_file())
            self.assertEqual(spawn.call_args.kwargs['env'],env)
            command=spawn.call_args.args[0]
            self.assertEqual(command[2],str(os.getpid()))
            self.assertEqual(command[3],sys.executable)
            self.assertEqual(command[4],str(staged))
            self.assertEqual(command[5],hashlib.sha256(staged.read_bytes()).hexdigest())

    def test_verbose_logs_retain_stdout_stderr_and_terminal_echo(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            console=io.StringIO();errors=io.StringIO()
            with patch('sys.stdout',console),patch('sys.stderr',errors):
                path=launcher.configure_verbose_logging(Path(folder))
                handler=sys.stdout.handler
                try:
                    print('scanner started',flush=True)
                    print('worker traceback',file=sys.stderr,flush=True)
                    self.assertEqual(launcher.configure_verbose_logging(Path(folder)),path)
                    self.assertEqual(os.environ['TRADING_BOT_LOG_FILE'],str(path))
                    self.assertIn('scanner started',path.read_text())
                    self.assertIn('worker traceback',path.read_text())
                    self.assertIn('scanner started',console.getvalue())
                    self.assertIn('worker traceback',errors.getvalue())
                finally:
                    handler.close()

    def test_verbose_logs_survive_closed_terminal_and_rotate(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            console=unittest.mock.Mock()
            console.fileno.side_effect=OSError('no terminal')
            console.write.side_effect=BrokenPipeError('terminal closed')
            with patch('sys.stdout',console),patch('sys.stderr',io.StringIO()):
                path=launcher.configure_verbose_logging(Path(folder))
                handler=sys.stdout.handler
                handler.maxBytes=300
                try:
                    for i in range(20):
                        print(f'{i}: '+('x'*100),flush=True)
                    self.assertIn('19: ',path.read_text())
                    self.assertEqual(len(list(path.parent.glob('runtime.log*'))),4)
                    self.assertFalse(sys.stdout.echo)
                finally:
                    handler.close()

    def test_restarted_process_does_not_duplicate_redirected_log_output(self):
        from logging.handlers import RotatingFileHandler
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runtime.log'
            handler=RotatingFileHandler(path,encoding='utf-8')
            handler.terminator=''
            try:
                with path.open('a') as inherited:
                    stream=launcher.DiagnosticStream(inherited,handler)
                    stream.write('one startup\n');stream.flush()
                self.assertEqual(path.read_text(),'one startup\n')
            finally:
                handler.close()

    @unittest.skipUnless(sys.platform=='linux','Native Linux installer needs sha256sum')
    def test_install_preserves_state_and_restarts_even_with_spaces_in_paths(self):
        with tempfile.TemporaryDirectory(prefix='family updater ') as folder:
            root=Path(folder);current=root/'FamilyTradingBot';staged=root/'FamilyTradingBot.new'
            state=root/'state.txt';state.write_text('keep portfolio and settings')
            current.write_text('#!/bin/sh\necho old > "'+str(root/'started')+'"\n');current.chmod(0o700)
            staged.write_text('#!/bin/sh\necho new > "'+str(root/'started')+'"\n')
            installer=root/'install.sh';installer.write_text(updater.INSTALLER)
            digest=hashlib.sha256(staged.read_bytes()).hexdigest()
            subprocess.run(['/bin/sh',str(installer),'99999999',str(current),str(staged),digest],check=True,capture_output=True,timeout=10)
            self.assertEqual((root/'started').read_text().strip(),'new')
            self.assertEqual(state.read_text(),'keep portfolio and settings')
            self.assertIn('echo old',current.with_suffix('.previous').read_text())
            # Tampering after download leaves the installed binary in place.
            staged.write_text('tampered')
            result=subprocess.run(['/bin/sh',str(installer),'99999999',str(current),str(staged),'0'*64],capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('echo new',current.read_text())


if __name__=='__main__':unittest.main()
