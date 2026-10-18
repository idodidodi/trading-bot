"""Verify Linux release assets and replace the binary after graceful shutdown."""
import hashlib
from datetime import datetime, timedelta
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request
from zoneinfo import ZoneInfo

ASSET='FamilyTradingBot-linux-x86_64'
MANIFEST='linux-x86_64-update.json'


def next_update_at(now):
    """Next 05:00 in Israel, independent of host timezone and DST changes."""
    local = datetime.fromtimestamp(now, ZoneInfo('Asia/Jerusalem'))
    target = local.replace(hour=5, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.timestamp()


def supported():
    return sys.platform=='linux' and platform.machine().lower() in ('x86_64','amd64') and bool(getattr(sys,'frozen',False))


def download_release(root,release):
    from windows_update import VERSION,version_parts,release_url,get_json,log_update
    import re
    tag=release['tag_name']
    version=version_parts(tag)
    if version<=version_parts(VERSION) or release.get('draft') or release.get('prerelease'):return None
    assets={a['name']:a for a in release['assets']}
    manifest=get_json(release_url(assets[MANIFEST]['browser_download_url'],tag,MANIFEST))
    if version_parts(manifest['version'])!=version or manifest.get('platform')!='linux-x86_64' or not re.fullmatch('[0-9a-f]{64}',manifest['sha256']):
        raise ValueError('Invalid Linux release manifest')
    asset=assets[ASSET]
    url=release_url(asset['browser_download_url'],tag,ASSET)
    size=manifest['size']
    if type(size) is not int or not 0<size<=200*1024*1024 or size!=asset['size']:raise ValueError('Invalid executable size')
    root.mkdir(parents=True,exist_ok=True)
    staged=root/'FamilyTradingBot.new'
    digest=hashlib.sha256();total=0
    log_update('Downloading executable '+'.'.join(map(str,version))+f'; {size} bytes expected')
    progress_at=5*1024*1024
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FamilyTradingBot/'+VERSION}),timeout=30) as response,staged.open('wb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>size:raise ValueError('Executable exceeds manifest size')
                digest.update(chunk);output.write(chunk)
                if total >= progress_at:
                    log_update(f'Download progress: {total}/{size} bytes')
                    progress_at=total+5*1024*1024
        if total!=size or digest.hexdigest()!=manifest['sha256']:raise ValueError('Executable checksum does not match release')
        with staged.open('rb') as f:header=f.read(20)
        if len(header)<20 or header[:6]!=b'\x7fELF\x02\x01' or header[18:20]!=b'\x3e\x00':
            raise ValueError('Release is not a Linux x86_64 executable')
        staged.chmod(0o700)
        log_update('Download complete; size, SHA-256 and Linux x86_64 header verified')
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise


INSTALLER=r'''#!/bin/sh
set -eu
pid=$1
current=$2
staged=$3
expected=$4
log() { printf '[installer %s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
start_app() {
    log 'Starting managed executable'
    "$current" &
    log "Restart launched; PID $!"
}
log "Waiting for old application PID $pid to exit"
while kill -0 "$pid" 2>/dev/null; do sleep 1; done
log 'Old application exited; rechecking staged checksum'
backup="$current.previous"
if ! actual=$(sha256sum -- "$staged"); then
    log 'Cannot read staged checksum; restarting installed version'
    start_app
    exit 1
fi
actual=${actual%% *}
if [ "$actual" != "$expected" ]; then
    log 'Staged checksum changed; restarting installed version'
    start_app
    exit 1
fi
if ! chmod 700 "$staged" || ! cp -p -- "$current" "$backup"; then
    log 'Cannot prepare executable backup; restarting installed version'
    start_app
    exit 1
fi
if mv -f -- "$staged" "$current"; then
    log 'Executable replaced; previous version retained'
    start_app
else
    log 'Replacement failed; restoring previous executable'
    mv -f -- "$backup" "$current"
    start_app
    exit 1
fi
'''


def install(staged,shutdown):
    from linux_launcher import external_environment
    script=staged.parent/'install-update.sh'
    script.write_text(INSTALLER,encoding='utf-8')
    digest=hashlib.sha256(staged.read_bytes()).hexdigest()
    log_path=Path(os.environ.get('TRADING_BOT_LOG_FILE',str(staged.parent/'update.log')))
    descriptor=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(descriptor,'ab',buffering=0) as output:
        subprocess.Popen(['/bin/sh',str(script),str(os.getpid()),sys.executable,str(staged),digest],
                         env=external_environment(),close_fds=True,start_new_session=True,
                         stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
    shutdown()
