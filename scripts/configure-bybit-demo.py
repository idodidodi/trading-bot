"""Administrator CLI: provision, inspect, activate or disable DEMO policy. No orders."""
import argparse
import base64
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import certifi

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from platform_app import load_env
from demo_execution import policy_valid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--apply-schema',action='store_true')
    action.add_argument('--status',action='store_true')
    action.add_argument('--activate',type=Path,metavar='POLICY_JSON')
    action.add_argument('--disable',action='store_true')
    args=parser.parse_args()
    load_env()
    token=os.environ.get('SUPABASE_ACCESS_TOKEN','')
    if not token and sys.platform=='darwin':
        r=subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'],capture_output=True,text=True)
        if r.returncode==0:
            token=r.stdout.strip()
            if token.startswith('go-keyring-base64:'):token=base64.b64decode(token.split(':',1)[1]).decode()
    if not token:raise SystemExit('Existing Supabase CLI login or private SUPABASE_ACCESS_TOKEN required')
    public=json.loads((ROOT/'cloud/browser-config.json').read_text())
    project=urllib.parse.urlparse(public['supabaseUrl']).hostname.split('.')[0]
    context=ssl.create_default_context(cafile=certifi.where())
    def query(sql):
        req=urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',
            data=json.dumps({'query':sql}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,context=context,timeout=30) as response:return json.load(response)
    if args.apply_schema:
        query((ROOT/'cloud/bybit-demo.sql').read_text())
        checks=query("""SELECT
          has_table_privilege('trading_worker','bot_runtime.demo_execution_trades','SELECT,INSERT,UPDATE') AS worker,
          NOT has_table_privilege('authenticated','bot_runtime.demo_execution_trades','SELECT') AS private,
          NOT has_table_privilege('anon','bot_runtime.demo_execution_policy','UPDATE') AS anonymous_denied""")
        if not all(checks[0].values()):raise SystemExit('Schema permission verification failed')
        print(json.dumps(checks))
    elif args.activate:
        config=json.loads(args.activate.read_text());policy_valid(config)
        literal="'"+json.dumps(config).replace("'","''")+"'::jsonb"
        query(f"""INSERT INTO bot_runtime.demo_execution_policy AS p(id,revision,enabled,activated_at,config)
          VALUES(1,1,true,{int(time.time()*1000)},{literal})
          ON CONFLICT(id) DO UPDATE SET revision=p.revision+1,enabled=true,
          activated_at=excluded.activated_at,config=excluded.config,status='{{}}'::jsonb""")
    elif args.disable:
        query('UPDATE bot_runtime.demo_execution_policy SET enabled=false,revision=revision+1 WHERE id=1')
    if not args.apply_schema:
        print(json.dumps(query('SELECT * FROM bot_runtime.demo_execution_policy'),indent=2))
        print(json.dumps(query('SELECT state,count(*) FROM bot_runtime.demo_execution_trades GROUP BY state'),indent=2))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        raise SystemExit('Demo configuration failed: '+type(exc).__name__+'; no orders submitted by this CLI') from None
