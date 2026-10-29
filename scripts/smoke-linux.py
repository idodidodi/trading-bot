"""Native isolated dashboard smoke test; no DB, provider, Telegram, broker or cutover."""
import os
from pathlib import Path
import subprocess
import time
import urllib.request
root=Path(os.environ['XDG_DATA_HOME'])/'FamilyTradingBotPostgres'
url='http://127.0.0.1:'+os.environ['PORT']
for attempt in range(45):
    try:
        with urllib.request.urlopen(url+'/web/config.js',timeout=1) as response:
            assert 'supabaseUrl' in response.read().decode()
        break
    except OSError:time.sleep(1)
else:raise RuntimeError('Frozen dashboard did not start')
assert not list(root.rglob('*.sqlite3'))
assert not (Path(os.environ['XDG_DATA_HOME'])/'FamilyTradingBot').exists()
print('Frozen browser-only startup uses a separate root and creates no SQLite files.')
