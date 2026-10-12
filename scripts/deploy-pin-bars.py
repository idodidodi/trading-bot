"""Apply only the Pin-Bars/CSV migrations using the existing Supabase login."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
from platform_app import load_env
load_env()
token=os.environ.get('SUPABASE_ACCESS_TOKEN','').strip()
if not token and sys.platform=='darwin':
    result=subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'],capture_output=True,text=True)
    if result.returncode==0:
        token=result.stdout.strip()
        if token.startswith('go-keyring-base64:'):token=base64.b64decode(token.split(':',1)[1]).decode()
if not token:raise SystemExit('Supabase deployment needs an existing CLI login or locally configured SUPABASE_ACCESS_TOKEN.')
config=json.loads((root/'cloud/browser-config.json').read_text())
project=urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
def query(sql):
    request=urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',data=json.dumps({'query':sql}).encode(),method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close();raise SystemExit(f'Migration request failed (HTTP {code}); inspect project state before retrying.') from None
    except OSError:raise SystemExit('Migration request failed; check network access.') from None
checks="""select to_regprocedure('public.dashboard_api_before_pin_bars(uuid,text,jsonb)') is not null as pin_installed,
to_regprocedure('public.dashboard_api_before_csv_backtests(uuid,text,jsonb)') is not null as csv_installed,
to_regprocedure('public.dashboard_can_edit(uuid)') is not null as sharing_ready,
position('dashboard_api_before_csv_backtests' in pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure))>0 as csv_active;"""
state=query(checks)[0]
if '--check' in sys.argv:
    print(json.dumps(state));sys.exit()
if not state['sharing_ready']:raise SystemExit('Existing sharing migration required; no changes applied.')
# Never replace a newer dispatcher by blindly reapplying an older migration.
sql='begin;\n'
if not state['pin_installed']:sql+=(root/'cloud/pin-bars.sql').read_text()+'\n'
if not state['csv_installed']:sql+=(root/'cloud/csv-backtests.sql').read_text()+'\n'
sql+='commit;\n'+checks
verified=query(sql)[0]
if not all(verified.get(k) for k in ('pin_installed','csv_installed','csv_active')):
    raise SystemExit('Migration did not verify the active API; inspect the dispatcher chain.')
print('Pin-Bars and CSV backtest migrations applied; sharing and active dispatcher verified.')
