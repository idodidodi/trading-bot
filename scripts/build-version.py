"""Generate public version metadata before packaging the executable."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version_info import build_manifest

(ROOT/'web/version.json').write_text(json.dumps(build_manifest(ROOT)), encoding='utf-8')
