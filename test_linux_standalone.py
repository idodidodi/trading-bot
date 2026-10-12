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
