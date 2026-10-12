"""Publish the Linux binary's checksum; exclude private state and credentials."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app_version import VERSION
binary=Path('dist/FamilyTradingBot-linux-x86_64')
Path('dist/linux-x86_64-update.json').write_text(json.dumps(dict(version=VERSION,platform='linux-x86_64',size=binary.stat().st_size,
    sha256=hashlib.sha256(binary.read_bytes()).hexdigest()),indent=2),encoding='utf-8')
