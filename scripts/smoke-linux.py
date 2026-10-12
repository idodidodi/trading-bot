"""Native frozen-app checks; only isolated dry-run data is used."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

root=Path(os.environ['XDG_DATA_HOME'])/'FamilyTradingBot'
url='http://127.0.0.1:'+os.environ['PORT']


def ready():
    for _ in range(60):
        try:
            with urllib.request.urlopen(url+'/health',timeout=1) as response:health=json.load(response)
            with urllib.request.urlopen(url+'/api/updates',timeout=1) as response:updates=json.load(response)
            if health['ok'] and health['dry_run'] and updates['supported'] and updates['platform']=='Linux':return
        except (OSError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('Frozen Linux app did not start: '+Path(os.environ['RUNNER_TEMP'],'family-bot.log').read_text())


def stop_app():
    # Linux onefile has a bootloader and an inner application process.
    matches=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:binary=(entry/'exe').resolve()
        except OSError:continue
        if binary==root/'bin/FamilyTradingBot':matches.append(int(entry.name))
    parents={pid:int(next(line for line in Path('/proc',str(pid),'status').read_text().splitlines() if line.startswith('PPid:')).split()[1]) for pid in matches}
    # Signal only the outer bootloader; it forwards once to the inner app.
    for pid in matches:
        if parents[pid] in matches:continue
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    # The app's bounded worker joins total 69 seconds when requests are in flight.
    for _ in range(180):
        if not any(Path('/proc',str(pid)).exists() for pid in matches):return
        time.sleep(.5)
    # An orphaned bootloader may become a zombie before the runner reaps it.
    for pid in matches:
        if not Path('/proc',str(pid)).exists():continue
        if Path('/proc',str(pid),'stat').read_text().split(') ')[1].startswith('Z'):continue
        raise RuntimeError('Frozen app did not stop gracefully: '+Path(os.environ['RUNNER_TEMP'],'family-bot.log').read_text())


ready()
settings=root/'.env'
with settings.open('a') as output:output.write('\n# smoke-test: settings must survive restart\n')
marker=root/'data'/'persistent-smoke.txt';marker.write_text('preserved')
stop_app()
with Path(os.environ['RUNNER_TEMP'],'family-bot.log').open('a') as log:
    subprocess.Popen([str(root/'bin/FamilyTradingBot')],stdout=log,stderr=log)
ready()
assert 'settings must survive restart' in settings.read_text()
assert marker.read_text()=='preserved'
stop_app()
print('Native Linux startup, update support, graceful stop and persistent-data restart passed.')
