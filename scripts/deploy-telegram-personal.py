"""Provision personal alert preferences and register a verified owner group map."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid


def main():
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    from platform_app import load_env
    from telegram_routing import ROUTES,validate
    load_env()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group-map',required=True,type=Path)
    args=parser.parse_args()
    mapping=json.loads(args.group_map.read_text())
    routes=validate(mapping['routes'])
    if set(mapping['routes'])!=set(ROUTES) or not all(routes.values()) or len(set(routes.values()))!=13:
        raise SystemExit('Expected 13 distinct verified personal destinations')
    if len(mapping['groups'])!=13 or not all(g.get('members_verified') for g in mapping['groups']):
        raise SystemExit('Verify the personal group map before provisioning')
    if {g['route']:g['chat_id'] for g in mapping['groups']}!=routes:raise SystemExit('Group map mismatch')
    telegram_id=int(mapping['telegram_user_id'])
    token=os.environ.get('SUPABASE_ACCESS_TOKEN','')
    if not token:
        r=subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'],capture_output=True,text=True)
        if r.returncode==0:
            token=r.stdout.strip()
            if token.startswith('go-keyring-base64:'):token=base64.b64decode(token.split(':',1)[1]).decode()
    if not token:raise SystemExit('Supabase management credentials unavailable')
    config=json.loads((root/'cloud/browser-config.json').read_text())
    installation=str(uuid.UUID(config['installationId']))
    project=urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
    def query(sql):
        req=urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',
            data=json.dumps({'query':sql}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=45) as response:return json.load(response)
        except Exception as exc:raise SystemExit('Database provisioning failed: '+type(exc).__name__+'; verify state before retrying') from None
    before=query("select i.owner_id,pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure) as api from public.dashboard_installations i where i.id='"+installation+"'::uuid")
    if len(before)!=1:raise SystemExit('Installation unavailable')
    owner=str(uuid.UUID(before[0]['owner_id']))
    backup=root/'data/backups/before-personal-telegram.json'
    backup.parent.mkdir(parents=True,exist_ok=True)
    if not backup.exists():backup.write_text(json.dumps(before));backup.chmod(0o600)
    query((root/'cloud/telegram-personal.sql').read_text())
    route_literal=json.dumps(routes).replace("'","''")
    query("INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES('"+installation+"','"+owner+"',"+str(telegram_id)+",'"+route_literal+"'::jsonb) ON CONFLICT(installation,user_id) DO UPDATE SET telegram_user_id=excluded.telegram_user_id,routes=excluded.routes")
    result=query("select routes from public.telegram_recipients where installation='"+installation+"' and user_id='"+owner+"'")
    if len(result)!=1 or result[0]['routes']!=routes:raise SystemExit('Personal mapping verification failed')
    print('Personal Telegram schema and 13 owner destinations registered; original routes unchanged.')


if __name__=='__main__':main()
