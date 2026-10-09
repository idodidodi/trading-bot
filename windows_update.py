"""Hourly/manual GitHub release updates, verified before graceful replacement."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import urllib.request

from app_version import VERSION

REPOSITORY='idodidodi/trading-bot'
API=f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
_wake=threading.Event()
_lock=threading.Lock()
_state={'version':VERSION,'phase':'idle','message':'Hourly update checks enabled.'}


def supported():
    return sys.platform=='win32' and bool(getattr(sys,'frozen',False))


def status():
    with _lock:return dict(_state,supported=supported())


def request_check():
    if not supported():raise ValueError('Automatic installation is available in the standalone Windows app')
    _wake.set()
    return dict(message='Checking for updates…')


def version_parts(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d+\.\d+\.\d+',value):
        raise ValueError('Invalid release version')
    return tuple(int(n) for n in value.lstrip('v').split('.'))


def release_url(value,tag,name):
    expected=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    if value!=expected:raise ValueError('Untrusted release asset URL')
    return value


def get_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'FamilyTradingBot/'+VERSION,'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=30) as response:
        raw=response.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Release metadata too large')
    return json.loads(raw)


def download_release(root,release):
    tag=release['tag_name']
    target_version=version_parts(tag)
    if target_version<=version_parts(VERSION):return None
    if release.get('draft') or release.get('prerelease'):return None
    assets={a['name']:a for a in release['assets']}
    manifest_url=release_url(assets['windows-update.json']['browser_download_url'],tag,'windows-update.json')
    manifest=get_json(manifest_url)
    if version_parts(manifest['version'])!=target_version or not re.fullmatch('[0-9a-f]{64}',manifest['sha256']):
        raise ValueError('Invalid release manifest')
    asset=assets['FamilyTradingBot.exe']
    url=release_url(asset['browser_download_url'],tag,'FamilyTradingBot.exe')
    size=manifest['size']
    if type(size) is not int or not 0<size<=200*1024*1024 or size!=asset['size']:
        raise ValueError('Invalid executable size')
    root.mkdir(parents=True,exist_ok=True)
    temporary=root/'FamilyTradingBot.new.exe'
    digest=hashlib.sha256();total=0
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FamilyTradingBot/'+VERSION}),timeout=30) as response,temporary.open('wb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>size:raise ValueError('Executable exceeds manifest size')
                digest.update(chunk);output.write(chunk)
        if total!=size or digest.hexdigest()!=manifest['sha256']:
            raise ValueError('Executable checksum does not match release')
        with temporary.open('rb') as f:
            if f.read(2)!=b'MZ':raise ValueError('Release is not a Windows executable')
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


INSTALLER=r'''
param([int]$AppPid,[string]$Current,[string]$Staged,[string]$ExpectedHash)
$ErrorActionPreference='Stop'
Wait-Process -Id $AppPid -ErrorAction SilentlyContinue
$Backup=$Current+'.previous'
try {
    if ((Get-FileHash -LiteralPath $Staged -Algorithm SHA256).Hash.ToLower() -ne $ExpectedHash) { throw 'Checksum changed' }
    $Moved=$false
    for ($Attempt=0; $Attempt -lt 30; $Attempt++) {
        try {
            if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Force }
            Move-Item -LiteralPath $Current -Destination $Backup -Force
            $Moved=$true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $Moved) { throw 'Could not replace running app' }
    Move-Item -LiteralPath $Staged -Destination $Current -Force
    Start-Process -FilePath $Current
} catch {
    if (Test-Path -LiteralPath $Backup) { Move-Item -LiteralPath $Backup -Destination $Current -Force }
    if (Test-Path -LiteralPath $Current) { Start-Process -FilePath $Current }
}
'''


def install(staged,shutdown):
    script=staged.parent/'install-update.ps1'
    script.write_text(INSTALLER)
    digest=hashlib.sha256(staged.read_bytes()).hexdigest()
    env={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'}
    subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                      '-AppPid',str(os.getpid()),'-Current',sys.executable,'-Staged',str(staged),'-ExpectedHash',digest],
                     creationflags=subprocess.CREATE_NO_WINDOW,env=env)
    shutdown()


def worker(stop,shutdown):
    if not supported():return
    root=Path(os.environ['LOCALAPPDATA'])/'FamilyTradingBot'/'updates'
    while not stop.is_set():
        try:
            with _lock:_state.update(phase='checking',message='Checking for updates…')
            release=get_json(API)
            with _lock:_state.update(phase='downloading',message='Checking release package…')
            staged=download_release(root,release)
            if staged:
                with _lock:_state.update(phase='installing',message='Update verified. Installing and restarting…')
                install(staged,shutdown)
                return
            with _lock:_state.update(phase='idle',message='The Windows app is up to date.')
        except Exception:
            with _lock:_state.update(phase='error',message='Update check failed. The installed app continues running; retry with Check for updates.')
        _wake.wait(3600)
        _wake.clear()
