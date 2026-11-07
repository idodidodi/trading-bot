"""Preview NEAR/USD RSI 5m + cached context configuration; --apply writes it.

Run only after cloud/rsi-context-cache.sql and the matching scanner are installed.
This uses the existing revisioned asset editor, never starts a scanner or sends alerts.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from platform_app import Store,load_env
from rsi_context import configure,target
from managed_assets import change,validate_asset


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();load_env();store=Store()
    with store.connect() as db:
        revision,payload=db.execute('SELECT revision,payload FROM managed_config WHERE id=1').fetchone()
        if args.apply and not db.execute("SELECT to_regclass('bot_runtime.rsi_context_cache')").fetchone()[0]:
            raise SystemExit('Apply the additive cache migration first')
    config=json.loads(payload) if isinstance(payload,str) else payload
    asset=next(a for a in configure(config)['assets'] if target(a));validate_asset(asset)
    print(json.dumps(dict(revision=revision,asset=asset),indent=2))
    if args.apply:
        change(store,dict(action='save',revision=revision,asset=asset))
        print('Saved. The existing scanner will apply this revision; no worker started by this command.')
    else:print('Preview only; managed configuration unchanged.')


if __name__=='__main__':main()
