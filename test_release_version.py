import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_version',Path(__file__).parent/'scripts/release-version.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseVersionTests(unittest.TestCase):
    def test_numeric_increment_and_manual_minor_version(self):
        self.assertEqual(release.allocate('1.0.6',['v1.0.6','v1.0.9','v1.0.10','v1.1.0-rc1']), '1.0.11')
        self.assertEqual(release.allocate('1.1.0',['v1.0.10']), '1.1.0')
        self.assertEqual(release.allocate('1.0.6',[]), '1.0.6')

    def test_only_main_or_stable_tags_publish_and_outputs_share_one_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'app_version.py').write_text("VERSION = '1.0.6'\n")
            output=root/'output'
            env=dict(GITHUB_REF='refs/heads/main',GITHUB_OUTPUT=str(output))
            with patch.object(release.subprocess,'check_output',return_value='v1.0.6\nv1.0.7\n'):
                self.assertEqual(release.prepare(root,env),'1.0.8')
            self.assertIn('publish=true',output.read_text())
            output.write_text('')
            with patch.object(release.subprocess,'check_output') as git:
                self.assertEqual(release.prepare(root,env|dict(GITHUB_REF='refs/heads/feature')),'1.0.6')
                git.assert_not_called()
            self.assertIn('publish=false',output.read_text())
            self.assertEqual(release.prepare(root,env|dict(GITHUB_REF='refs/tags/v1.0.9')),'1.0.9')
            with self.assertRaises(ValueError):release.prepare(root,env|dict(GITHUB_REF='refs/tags/v1.0.5'))

    def test_invalid_versions_never_write_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'app_version.py';path.write_text("VERSION = '1.0.6'\n")
            for version in ('1.0.5',"1.0.7';bad",'1.0.7-rc1','01.0.7'):
                with self.assertRaises(ValueError):release.apply(root,version)
                self.assertEqual(release.base_version(root),'1.0.6')
            release.apply(root,'1.0.7')
            self.assertEqual(release.base_version(root),'1.0.7')

    def test_apply_preserves_utf8_with_windows_default_encoding(self):
        original_open = Path.open

        def windows_open(path, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
            if 'b' not in mode and encoding in (None, 'locale'):
                encoding = 'cp1252'
            if 'b' not in mode and any(flag in mode for flag in 'wax') and newline is None:
                newline = '\r\n'
            return original_open(path, mode, buffering, encoding, errors, newline)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'web').mkdir()
            (root/'app_version.py').write_bytes("# גרסה\nVERSION = '1.0.6'\n".encode('utf-8'))
            guide = root/'web/update-notes.html'
            notes = 'Pending executable release · “database is locked” · עברית\n'
            guide.write_bytes(notes.encode('utf-8'))
            changelog = root/'CHANGELOG.md'
            changes = '## Unreleased — תיקון\n“database is locked”\n'
            changelog.write_bytes(changes.encode('utf-8'))
            with patch.object(Path, 'open', windows_open):
                release.apply(root, '1.0.7')
                self.assertEqual(release.base_version(root), '1.0.7')
            self.assertEqual(guide.read_text(encoding='utf-8'), notes.replace(
                'Pending executable release', 'Executable version 1.0.7'))
            self.assertEqual(changelog.read_text(encoding='utf-8'), changes.replace(
                '## Unreleased', '## 1.0.7'))

    def test_both_platforms_required_with_matching_version_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);dist=root/'dist';dist.mkdir()
            for manifest,binary,raw,platform in [('windows-update.json','FamilyTradingBot.exe',b'MZtest',None),
                 ('linux-x86_64-update.json','FamilyTradingBot-linux-x86_64',b'\x7fELFtest','linux-x86_64')]:
                (dist/binary).write_bytes(raw)
                data=dict(version='1.0.7',size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                if platform:data['platform']=platform
                (dist/manifest).write_text(json.dumps(data))
            release.verify(root,'1.0.7')
            with self.assertRaises(ValueError):release.verify(root,'1.0.8')
            (dist/'FamilyTradingBot.exe').write_bytes(b'MZchanged')
            with self.assertRaises(ValueError):release.verify(root,'1.0.7')
            (dist/'windows-update.json').unlink()
            with self.assertRaises(FileNotFoundError):release.verify(root,'1.0.7')


if __name__=='__main__':unittest.main()
