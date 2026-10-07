"""Apply the additive provider migration without changing Auth or Edge Functions."""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_app import ROOT, load_env


def deploy():
    load_env()
    token = os.environ.get('SUPABASE_ACCESS_TOKEN', '').strip()
    if not token:
        raise ValueError('Set SUPABASE_ACCESS_TOKEN locally or sign in to the Supabase dashboard.')
    config = json.loads((ROOT / 'cloud/browser-config.json').read_text())
    project = urllib.parse.urlparse(config['supabaseUrl']).hostname.split('.')[0]
    query = (ROOT / 'cloud/providers.sql').read_text()
    query += """
select public.dashboard_validate_provider_asset(
 '{"id":"DAX-ETF","provider":"alpaca","exchange":"NASDAQ","symbol":"DAX",
   "market":"etf","enabled":true,"feed_confirmed":true,"timeframes":["1h"]}'::jsonb);
select position('alpaca' in pg_get_functiondef('public.dashboard_api_base(uuid,text,jsonb)'::regprocedure)) > 0 as alpaca_enabled,
 position('dashboard_api_base' in pg_get_functiondef('public.dashboard_api(uuid,text,jsonb)'::regprocedure)) > 0 as backtest_dispatcher_preserved;
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
        raise ValueError(f'Provider migration request failed (HTTP {code}); check management access.') from None
    except (OSError, ValueError):
        raise ValueError('Provider migration request or response failed.') from None
    if not isinstance(rows, list) or not any(isinstance(row, dict) and row.get('alpaca_enabled') is True
               and row.get('backtest_dispatcher_preserved') is True for row in rows):
        raise ValueError('Migration response did not confirm Alpaca and the existing dispatcher; inspect Supabase before retrying.')
    print('Provider migration applied; Alpaca hourly validation and backtest dispatcher verified.')


if __name__ == '__main__':
    try:
        deploy()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
