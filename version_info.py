"""Comparable UI/scanner source fingerprints and bounded GitHub checks."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

from app_version import VERSION

REPOSITORY = 'idodidodi/trading-bot'
API = f'https://api.github.com/repos/{REPOSITORY}'
ONLINE_DASHBOARD_URL = 'https://family-trading-bot.idodidodi.workers.dev'
UI_FILES = {'web/index.html', 'web/app.js', 'web/style.css', 'web/chart.js',
            'web/favicon.svg', 'web/vendor/lightweight-charts.js'}


def component(path):
    # CI assigns executable versions at build time; code fingerprints compare source.
    if path == 'app_version.py':
        return None
    if path in UI_FILES:
        return 'ui'
    if (('/' not in path and path.endswith('.py') and not path.startswith('test_'))
            or path.startswith('skills/') or path in ('scanner.json', 'stock_universe.json')):
        return 'scanner'


def fingerprints(entries):
    result = {}
    for kind in ('ui', 'scanner'):
        rows = sorted(f'{path}:{sha}' for path, sha in entries.items() if component(path) == kind)
        result[kind] = hashlib.sha256('\n'.join(rows).encode()).hexdigest()[:16] if rows else None
    return result


def build_manifest(root):
    root = Path(root)
    entries = {}
    for path in list(root.glob('*.py')) + list((root/'skills').rglob('*')) + [root/p for p in UI_FILES | {'scanner.json', 'stock_universe.json'}]:
        if not path.is_file():continue
        name = path.relative_to(root).as_posix()
        if not component(name):continue
        raw = path.read_bytes().replace(b'\r\n', b'\n')
        entries[name] = hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()
    return dict(version=VERSION, **fingerprints(entries))


@lru_cache(maxsize=1)
def local_versions():
    from platform_app import ROOT
    if getattr(sys, 'frozen', False):
        # Read immutable bundled metadata, never another process's copied UI.
        path = Path(sys._MEIPASS)/'web/version.json'
        return json.loads(path.read_text(encoding='utf-8'))
    return build_manifest(ROOT)


def get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'FamilyTradingBot/'+VERSION,
                                               'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=4) as response:
        raw = response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:raise ValueError('Version metadata too large')
    return json.loads(raw)


def github_versions():
    commits = get_json(API+'/commits?per_page=1')
    commit = commits[0]['sha']
    tree = get_json(API+f'/git/trees/{commit}?recursive=1')
    if tree.get('truncated'):raise ValueError('Incomplete GitHub tree')
    entries = {r['path']:r['sha'] for r in tree['tree'] if r['type']=='blob'}
    return dict(commit=commit, **fingerprints(entries))


def versions(store=None):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from runtime_env import environment, cloud_sync_enabled
    from windows_update import API as release_api, status
    from local_settings import delivery_dry_run
    result = dict(environment=environment(), local=local_versions(), updates=status(), checked_at=time.time(),
                  telegram_enabled=not delivery_dry_run(store, os.environ.get('DRY_RUN', 'true').lower() == 'true'),
                  cloud_sync_enabled=cloud_sync_enabled(store))
    urls = {'github': github_versions, 'release': lambda: get_json(release_api)}
    online_url = os.environ.get('ONLINE_DASHBOARD_URL', ONLINE_DASHBOARD_URL).rstrip('/')
    if online_url.startswith('https://'):
        urls['online_ui'] = lambda: get_json(online_url+'/web/version.json')
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {key: pool.submit(fn) for key, fn in urls.items()}
        for key, task in tasks.items():
            try:result[key] = task.result()
            except Exception:result[key] = dict(error='Unavailable; check connection or configuration and retry.')
    release = result.get('release', {})
    result['release'] = dict(version=release.get('tag_name'), published_at=release.get('published_at'), error=release.get('error'))
    return result
