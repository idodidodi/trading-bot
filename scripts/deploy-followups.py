"""Apply the outcome migration without changing Auth or older API dispatchers."""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


def main():
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from platform_app import load_env
    load_env()
    token = os.environ.get('SUPABASE_ACCESS_TOKEN', '').strip()
    if not token:
        raise SystemExit('Set SUPABASE_ACCESS_TOKEN locally or apply cloud/followups.sql in the Supabase SQL Editor.')
    config = json.loads((root/'cloud/browser-config.json').read_text())
    project = urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
    query = (root/'cloud/followups.sql').read_text() + """
select position('dashboard_api_before_followups' in pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure))>0 as api_ready,
 position('Refresh derived followups' in pg_get_functiondef('public.dashboard_finish_backtest(uuid,uuid,jsonb,jsonb)'::regprocedure))>0 as replay_ready;
"""
    request = urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/database/query',
        data=json.dumps({'query':query}).encode(),method='POST',
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        raise SystemExit(f'Follow-up migration failed (HTTP {code}); inspect Supabase before retrying.') from None
    if not isinstance(rows,list) or not any(r.get('api_ready') is True and r.get('replay_ready') is True for r in rows if isinstance(r,dict)):
        raise SystemExit('Follow-up migration verification failed; inspect Supabase before retrying.')
    print('Follow-up migration applied and API/replay hooks verified.')


if __name__ == '__main__':
    main()
