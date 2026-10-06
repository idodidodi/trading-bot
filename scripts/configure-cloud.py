"""Apply additive cloud migration and the maximum JWT expiry via Management API.
SUPABASE_ACCESS_TOKEN must already be set locally; never place it in browser assets.
"""
import json
import os
from pathlib import Path
import urllib.request

project='bwejkkboophbfeiwizbu'
token=os.environ.get('SUPABASE_ACCESS_TOKEN')
if not token:raise SystemExit('Set SUPABASE_ACCESS_TOKEN locally to your Supabase management token.')
def call(path,method='GET',body=None):
    req=urllib.request.Request(f'https://api.supabase.com/v1/projects/{project}/{path}',method=method,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as r:return json.load(r)
root=Path(__file__).resolve().parent.parent
call('database/query','POST',{'query':(root/'cloud/backtests.sql').read_text()})
call('config/auth','PATCH',{'jwt_exp':604800})
value=call('config/auth')
if int(value.get('jwt_exp',0))!=604800:raise SystemExit('JWT setting not verified')
print('Backtests migration applied; access JWT expiry verified at 604800 seconds (7 days). Existing JWTs retain their original expiry until refreshed.')
