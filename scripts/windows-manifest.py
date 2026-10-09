"""Publish checksums only; never include state or credentials in a release."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app_version import VERSION
binary=Path('dist/FamilyTradingBot.exe')
Path('dist/windows-update.json').write_text(json.dumps(dict(version=VERSION,size=binary.stat().st_size,
    sha256=hashlib.sha256(binary.read_bytes()).hexdigest()),indent=2))
