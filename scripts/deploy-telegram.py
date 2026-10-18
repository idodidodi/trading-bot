"""Deploy only Telegram coordination, preserving dashboard APIs and Auth settings."""
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
import uuid


def main():
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    from platform_app import load_env
    load_env()
    token=os.environ.get('SUPABASE_ACCESS_TOKEN','').strip()
    if not token and sys.platform=='darwin':
        result=subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'],capture_output=True,text=True)
        if result.returncode==0:
            token=result.stdout.strip()
            if token.startswith('go-keyring-base64:'):
                token=base64.b64decode(token.split(':',1)[1]).decode()
    if not token:
        raise SystemExit('Set SUPABASE_ACCESS_TOKEN locally or sign in with the Supabase CLI. No changes applied.')
    config=json.loads((root/'cloud/browser-config.json').read_text())
    project=urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
    def query(sql):
        request=urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',
            data=json.dumps({'query':sql}).encode(),method='POST',
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=30) as response:return json.load(response)
        except Exception:
            raise SystemExit('Telegram migration request failed; inspect Supabase before retrying.') from None
    check="""select to_regprocedure('public.telegram_coordinate(uuid,jsonb)') is not null as ready,
      not has_function_privilege('authenticated','public.telegram_coordinate(uuid,jsonb)','execute') as private;"""
    query('begin;\n'+(root/'cloud/telegram-delivery.sql').read_text()+'\ncommit;')
    state=query(check)
    if not state or not all(state[0].get(k) is True for k in ('ready','private')):
        raise SystemExit('Telegram migration verification failed; function was not deployed.')
    with tempfile.TemporaryDirectory(prefix='telegram-deploy-') as folder:
        functions=Path(folder)/'supabase/functions'
        functions.mkdir(parents=True)
        shutil.copytree(root/'cloud/functions/telegram-delivery',functions/'telegram-delivery')
        result=subprocess.run(['supabase','--workdir',folder,'functions','deploy','telegram-delivery',
            '--project-ref',project,'--no-verify-jwt','--use-api'],
            env={**os.environ,'SUPABASE_ACCESS_TOKEN':token},capture_output=True,text=True)
        if result.returncode:
            raise SystemExit('Telegram migration applied, but endpoint deployment failed. Check Supabase deployment status.')
    endpoint=config['supabaseUrl'].rstrip('/')+'/functions/v1/telegram-delivery'
    def invoke(body, credential=None):
        headers={'Content-Type':'application/json'}
        if credential:headers['Authorization']='Bearer '+credential
        request=urllib.request.Request(endpoint,data=json.dumps(body).encode(),headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=15) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as exc:
            code=exc.code;exc.close();return code,{}
        except Exception:
            raise SystemExit('Endpoint deployed; smoke check could not connect. No Telegram message was sent.') from None
    if invoke({})[0]!=401:
        raise SystemExit('Endpoint authentication verification failed.')
    machine=os.environ.get('CLOUD_SYNC_TOKEN','')
    if not machine:
        raise SystemExit('Endpoint deployed and unauthorized requests rejected; set CLOUD_SYNC_TOKEN for an authenticated smoke check.')
    payload=dict(action='acquire',destination_hash=uuid.uuid4().hex*2,event_hash=uuid.uuid4().hex*2,
        body_hash=uuid.uuid4().hex*2,owner=str(uuid.uuid4()),sender_id=str(uuid.uuid4()),device='deployment-check',environment='dev')
    code,body=invoke(payload,machine)
    if code!=200 or body.get('status')!='acquired':
        raise SystemExit('Endpoint deployed; authenticated claim verification failed. No Telegram message was sent.')
    code,body=invoke(dict(payload,action='release'),machine)
    if code!=200 or body.get('status')!='released':
        raise SystemExit('Endpoint deployed; smoke claim release failed. It expires automatically. No Telegram message was sent.')
    print('Telegram migration and endpoint deployed; private access and claim/release verified. No Telegram messages sent.')


if __name__=='__main__':main()
