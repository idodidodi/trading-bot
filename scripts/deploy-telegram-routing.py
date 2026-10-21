"""Deploy Telegram routing from current source and import a local group map."""
import base64
import argparse
import shutil
import tempfile
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
from telegram_routing import ROUTES,validate
load_env()
config=json.loads((root/'cloud/browser-config.json').read_text())
project=urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
installation=config['installationId']
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--group-map', type=Path, default=root/'telegram-group-map.json')
args=parser.parse_args()
mapping=json.loads(args.group_map.read_text())
routes=validate(mapping['routes'])
assert set(mapping['routes'])==set(ROUTES) and all(routes.values())
assert {g['route']:g['chat_id'] for g in mapping['groups']}==routes
assert len(set(routes.values()))==9

token=os.environ.get('SUPABASE_ACCESS_TOKEN','').strip()
if not token:
 r=subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'],capture_output=True,text=True)
 if r.returncode==0:
  token=r.stdout.strip()
  if token.startswith('go-keyring-base64:'):token=base64.b64decode(token.split(':',1)[1]).decode()
if not token:raise SystemExit('Supabase management credentials unavailable')

def request_json(url,body=None,credential=None):
 headers={'Content-Type':'application/json'}
 if credential:headers['Authorization']='Bearer '+credential
 request=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
 try:
  with urllib.request.urlopen(request,timeout=45) as response:return json.load(response)
 except urllib.error.HTTPError as exc:
  code=exc.code
  # Retain diagnostics only in a private ignored file; never show tokens/URLs.
  (root/'data/telegram-routing-deploy-error.txt').write_bytes(exc.read())
  raise SystemExit(f'Deployment request failed (HTTP {code}); diagnostic saved privately') from None

def query(sql):
 return request_json(f'https://api.supabase.com/v1/projects/{project}/database/query',{'query':sql},token)
def literal(s):return "'"+s.replace("'","''")+"'"

bot=os.environ.get('TELEGRAM_BOT_TOKEN','')
assert bot, 'Local Telegram bot token missing'
identity=request_json('https://api.telegram.org/bot'+bot+'/getMe')
assert identity.get('ok') and identity['result']['username'].lower()==mapping['bot_username'].lower(), 'Bot does not match group map'
for group in mapping['groups']:
 chat=request_json('https://api.telegram.org/bot'+bot+'/getChat',{'chat_id':group['chat_id']})
 assert chat.get('ok') and str(chat['result']['id'])==group['chat_id'], 'Group unavailable'
print('Verified bot identity and read access to all nine Telegram groups; no messages sent.')

checks=query("select id,owner_id from public.dashboard_installations where id="+literal(installation)+'::uuid')
assert len(checks)==1, 'Installation is not present'
backup=query("select pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure) as dashboard_api, to_regprocedure('public.telegram_read_routes(uuid)') is not null as routing_installed")
backup_dir=root/'data/backups';backup_dir.mkdir(exist_ok=True)
backup_path=backup_dir/'before-telegram-routing-deploy.json'
if not backup_path.exists():backup_path.write_text(json.dumps(backup))
query((root/'cloud/telegram-routing.sql').read_text())
saved=query("begin; insert into public.telegram_routes as r(installation,revision,routes) values("+literal(installation)+"::uuid,1,"+literal(json.dumps(routes))+"::jsonb) on conflict on constraint telegram_routes_pkey do update set revision=r.revision+1,routes=excluded.routes where r.routes is distinct from excluded.routes; select public.telegram_read_routes("+literal(installation)+"::uuid) as routing; commit;")
verified=query("select public.telegram_read_routes("+literal(installation)+"::uuid) as routing, not has_function_privilege('authenticated','public.telegram_read_routes(uuid)','execute') as machine_private, not has_table_privilege('authenticated','public.telegram_routes','select') as table_private, position('dashboard_api_before_telegram_routing' in pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure))>0 as api_ready")
assert len(verified)==1 and all(verified[0][k] for k in ('machine_private','table_private','api_ready'))
assert validate(verified[0]['routing']['routes'])==routes
print('Supabase migration applied; nine routes saved at revision',verified[0]['routing']['revision'],'and access restrictions verified.')
with tempfile.TemporaryDirectory(prefix='telegram-routing-sync-') as folder:
 target=Path(folder)/'supabase/functions/sync'
 target.parent.mkdir(parents=True)
 shutil.copytree(root/'cloud/functions/sync',target)
 r=subprocess.run(['supabase','--workdir',folder,'functions','deploy','sync','--project-ref',project,'--no-verify-jwt','--use-api'],env={**os.environ,'SUPABASE_ACCESS_TOKEN':token},capture_output=True,text=True)
if r.returncode:
 (root/'data/telegram-routing-function-deploy.log').write_text(r.stdout+r.stderr)
 raise SystemExit('Routes saved, but sync deployment failed; diagnostic saved privately')
print('Updated sync Edge Function deployed.')
endpoint=config['supabaseUrl']+'/functions/v1/sync'
try:
 request_json(endpoint,{'routing_only':True},'invalid-diagnostic-token')
 raise AssertionError('Unauthenticated request accepted')
except SystemExit as exc:
 if 'HTTP 401' not in str(exc):raise
response=request_json(endpoint,{'routing_only':True},os.environ['CLOUD_SYNC_TOKEN'])
assert validate(response['telegram_routing']['routes'])==routes
print('Live sync verification passed: unauthorized rejected and authenticated read returns all nine routes.')
