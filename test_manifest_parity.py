"""Dashboard and worker must identify the same source despite packaging version edits."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from version_info import UI_FILES,build_manifest


@unittest.skipUnless(shutil.which('node'),'Node required for cross-language manifest check')
class ManifestParityTests(unittest.TestCase):
    def test_component_hashes_match_and_ignore_packaging_version(self):
        module=(Path(__file__).parent/'cloud/version.mjs').resolve().as_uri()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'skills').mkdir()
            (root/'skills/rules.md').write_bytes('Rules: ±0.5%\r\n'.encode())
            (root/'scanner.py').write_bytes(b'print("scanner")\r\n')
            (root/'test_hidden.py').write_text('not runtime')
            for name in ('scanner.json','stock_universe.json'):(root/name).write_text('{}')
            for name in UI_FILES:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'asset\r\n')
            hashes=[]
            for release in ('1.0.6','99.0.0'):
                (root/'app_version.py').write_text(f'VERSION = "{release}"\n')
                script=f'import {{buildManifest}} from {json.dumps(module)}; console.log(JSON.stringify(await buildManifest(process.argv[1])));'
                output=subprocess.check_output(['node','--input-type=module','-e',script,str(root)],text=True,timeout=20)
                node=json.loads(output);python=build_manifest(root)
                self.assertEqual(node['scanner'],python['scanner'])
                self.assertEqual(node['ui'],python['ui'])
                self.assertEqual(node['version'],release)
                hashes.append(node['scanner'])
            self.assertEqual(hashes[0],hashes[1])
