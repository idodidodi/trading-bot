"""Apply the additive RSI + PB API/defaults upgrade and deploy replay functions."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
from platform_app import load_env
load_env()
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check',action='store_true')
args=parser.parse_args()
config=json.loads((root/'cloud/browser-config.json').read_text())
project=urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
token=os.environ.get('SUPABASE_ACCESS_TOKEN','').strip()
if not token and sys.platform=='darwin':
    result=subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'],capture_output=True,text=True)
    if result.returncode==0:
        token=result.stdout.strip()
        if token.startswith('go-keyring-base64:'):
            token=base64.b64decode(token.split(':',1)[1]).decode()
if not token:raise SystemExit('Use the existing Supabase CLI login or configure SUPABASE_ACCESS_TOKEN locally.')


def query(sql):
    req=urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',data=json.dumps({'query':sql}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        directory=root/'data/backups';directory.mkdir(parents=True,exist_ok=True)
        (directory/'rsi-pb-deploy-error.txt').write_bytes(exc.read())
        raise SystemExit(f'Cloud upgrade failed (HTTP {exc.code}); diagnostics saved privately.') from None


state=query("""select to_regprocedure('public.dashboard_api_before_csv_backtests(uuid,text,jsonb)') is not null as csv_ready,
 to_regprocedure('public.dashboard_api_before_telegram_routing(uuid,text,jsonb)') is not null as routes_ready,
 to_regprocedure('public.dashboard_can_edit(uuid)') is not null as sharing_ready,
 to_regprocedure('public.dashboard_api_before_rsi_pb(uuid,text,jsonb)') is not null as installed""")[0]
print(json.dumps(state))
if args.check:raise SystemExit()
if not all(state[k] for k in ('csv_ready','routes_ready','sharing_ready')):
    raise SystemExit('Existing CSV, sharing and Telegram routing migrations are required.')
backup=root/'data/backups/before-rsi-pb-deploy.json'
backup.parent.mkdir(parents=True,exist_ok=True)
if not backup.exists():
    backup.write_text(json.dumps(dict(
        functions=query("select p.proname,pg_get_functiondef(p.oid) as definition from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and (p.proname like 'dashboard_api%' or p.proname='dashboard_save_csv_backtest' or p.proname='dashboard_validate_scan_strategies')"),
        configurations=query("select installation,payload from public.dashboard_records where kind='config' and key='current'"))))
query((root/'cloud/rsi-pb.sql').read_text())
verified=query("""select to_regprocedure('public.dashboard_api_before_rsi_pb(uuid,text,jsonb)') is not null as installed,
 not has_function_privilege('authenticated','public.dashboard_api_before_rsi_pb(uuid,text,jsonb)','execute') as internal_private,
 not has_function_privilege('anon','public.dashboard_api(uuid,text,jsonb)','execute') as anonymous_denied,
 has_function_privilege('authenticated','public.dashboard_api(uuid,text,jsonb)','execute') as browser_api""")[0]
if not all(verified.values()):raise SystemExit('Migration access checks failed.')
print('Additive RSI + PB API, CSV validation, asset defaults and permissions verified.')
with tempfile.TemporaryDirectory(prefix='rsi-pb-deploy-') as directory:
    target=Path(directory)/'supabase/functions'
    target.mkdir(parents=True)
    for name in ('backtests','csv-backtests'):
        shutil.copytree(root/'cloud/functions'/name,target/name)
    for name in ('backtests','csv-backtests'):
        result=subprocess.run(['supabase','--workdir',directory,'functions','deploy',name,'--project-ref',project,'--no-verify-jwt','--use-api'],env={**os.environ,'SUPABASE_ACCESS_TOKEN':token},capture_output=True,text=True)
        if result.returncode:
            (root/f'data/backups/rsi-pb-{name}-deploy.log').write_text(result.stdout+result.stderr)
            raise SystemExit(f'API migration applied; {name} deployment failed. Diagnostics saved privately.')
        request=urllib.request.Request(config['supabaseUrl']+'/functions/v1/'+name,data=b'{}',headers={'Content-Type':'application/json'})
        try:
            urllib.request.urlopen(request,timeout=20)
            raise SystemExit(f'{name} accepted an unauthenticated request.')
        except urllib.error.HTTPError as exc:
            if exc.code!=401:raise SystemExit(f'{name} authentication check returned HTTP {exc.code}.') from None
        print(name+': deployed; unauthenticated requests rejected.')
print('Publish the rebuilt browser assets and upgrade scanner installations to activate live RSI + PB scanning.')
