"""Apply the additive multi-user dashboard sharing migration to the hosted project."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def main():
    root = Path(__file__).resolve().parent.parent
    # Reuse the project's local environment loader without printing credentials.
    import sys
    sys.path.insert(0, str(root))
    from platform_app import load_env
    load_env()
    token = os.environ.get('SUPABASE_ACCESS_TOKEN', '').strip()
    if not token:
        raise SystemExit('Set SUPABASE_ACCESS_TOKEN locally or apply cloud/sharing.sql in the Supabase SQL Editor.')
    config = json.loads((root / 'cloud/browser-config.json').read_text())
    project = urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
    query = (root / 'cloud/sharing.sql').read_text() + """
select position('dashboard_can_view' in pg_get_functiondef('public.dashboard_api_base(uuid,text,jsonb)'::regprocedure)) > 0 as api_shared,
 position('dashboard_can_view' in pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure)) > 0 as dispatcher_shared,
 exists(select 1 from pg_policies where schemaname='public' and tablename='dashboard_records' and policyname='owner_record_read' and qual like '%dashboard_can_view%') as records_shared;
"""
    request = urllib.request.Request(
        f'https://api.supabase.com/v1/projects/{project}/database/query',
        data=json.dumps({'query': query}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise SystemExit(f'Sharing migration failed (HTTP {code}); inspect Supabase before retrying.') from None
    if not isinstance(rows, list) or not any(row.get('api_shared') is True and row.get('dispatcher_shared') is True and row.get('records_shared') is True for row in rows if isinstance(row, dict)):
        raise SystemExit('Sharing migration did not pass verification; inspect Supabase before retrying.')
    print('Sharing migration applied and API/RLS access checks verified.')


if __name__ == '__main__':
    main()
