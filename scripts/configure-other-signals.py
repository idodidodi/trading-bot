"""Preview or apply the dedicated Other Signals route; never send Telegram messages.

Requires cloud/ma-cross.sql first for --apply. Keep the group map ignored/private.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write reviewed routing settings after publication approval')
    parser.add_argument('--group-map', type=Path, default=ROOT/'telegram-group-map.json')
    args = parser.parse_args()
    from platform_app import load_env
    load_env()
    from telegram_routing import validate
    mapping = json.loads(args.group_map.read_text())
    chat = validate(mapping['routes'])['other-signals']
    if not chat:
        raise SystemExit('Missing Other Signals destination in the private group map')
    config = json.loads((ROOT/'cloud/browser-config.json').read_text())
    project = config['supabaseUrl'].split('//')[1].split('.')[0]
    installation = literal(config['installationId']) + '::uuid'
    token = os.environ.get('SUPABASE_ACCESS_TOKEN', '')
    if not token:
        result = subprocess.run(['security','find-generic-password','-s','Supabase CLI','-a','supabase','-w'], capture_output=True, text=True)
        if result.returncode == 0:
            token = result.stdout.strip()
            if token.startswith('go-keyring-base64:'):
                token = base64.b64decode(token.split(':',1)[1]).decode()
    if not token:
        raise SystemExit('Supabase management credential unavailable')

    def query(sql):
        request = urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',
            data=json.dumps({'query':sql}).encode(), headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f'Management query failed (HTTP {exc.code}); routing not verified') from None
        except urllib.error.URLError:
            raise SystemExit('Management connection failed; routing not verified') from None

    def inspect():
        return query(f"""SELECT public.telegram_read_routes({installation}) AS routing,
          (SELECT count(*) FROM public.telegram_recipients r, jsonb_each_text(r.routes) kv
           WHERE r.installation={installation} AND kv.value={literal(chat)} AND kv.key<>'other-signals') AS personal_conflicts""")[0]

    before = inspect()
    routes = before['routing']['routes']
    conflicts = [key for key,value in routes.items() if value == chat and key != 'other-signals']
    print('Destination:', chat, '· Other Signals')
    print('Shared route keys to clear:', ', '.join(conflicts) or 'none')
    print('Personal route references to clear:', before['personal_conflicts'])
    print('Shared routing revision:', before['routing']['revision'])
    if not args.apply:
        print('Preview only. No production settings changed.')
        return
    # Merge under the same installation-row lock used by the dashboard editor.
    # Do not replace unrelated destinations from the possibly stale local map.
    query(f"""BEGIN;
      SELECT 1 FROM public.dashboard_installations WHERE id={installation} FOR UPDATE;
      DO $$ BEGIN
       IF NOT 'other-signals'=ANY(public.telegram_route_keys()) THEN
        RAISE EXCEPTION 'Apply cloud/ma-cross.sql first';
       END IF;
      END $$;
      INSERT INTO public.telegram_routes AS r(installation,revision,routes)
      VALUES({installation},1,jsonb_build_object('other-signals',{literal(chat)}::text))
      ON CONFLICT(installation) DO UPDATE SET revision=r.revision+1,
       routes=(SELECT coalesce(jsonb_object_agg(k,CASE WHEN v={literal(chat)} AND k<>'other-signals' THEN '' ELSE v END),'{{}}')
               FROM jsonb_each_text(r.routes) kv(k,v))||excluded.routes;
      UPDATE public.telegram_recipients r SET routes=(
       SELECT coalesce(jsonb_object_agg(k,v),'{{}}') FROM jsonb_each_text(r.routes) kv(k,v)
       WHERE v<>{literal(chat)} OR k='other-signals')
       WHERE r.installation={installation} AND EXISTS(
        SELECT 1 FROM jsonb_each_text(r.routes) kv WHERE kv.value={literal(chat)} AND kv.key<>'other-signals');
      COMMIT;""")
    after = inspect()
    saved = after['routing']['routes']
    if saved.get('other-signals') != chat or any(v == chat and k != 'other-signals' for k,v in saved.items()) or after['personal_conflicts']:
        raise SystemExit('Routing verification failed')
    print('Verified dedicated route at revision', after['routing']['revision'], '· no messages sent.')


if __name__ == '__main__':
    main()
