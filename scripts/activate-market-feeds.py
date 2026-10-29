"""Verify five market feeds and activate a saved database, or export a verified config."""
import argparse
import json
from pathlib import Path
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_app import Store, load_env
from managed_assets import get, cache_candles, for_timeframe
from market_feeds import candidates, merge, verify


def activate(store):
    state = get(store)
    assets = candidates(state['config'])
    history = verify(assets, int(time.time()*1000))
    directory=Path(store.path).parent if store.path else Path(os.environ.get('DATA_DIR',str(Path.home()/'.local/share/FamilyTradingBotPostgres/data')))
    backup = directory/'backups'/f'before-five-market-feeds-{time.time_ns()}.json'
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(state,indent=2)+'\n');backup.chmod(0o600)
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        revision, applied, raw = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if revision != state['revision'] or json.loads(raw) != state['config']:
            raise ValueError('Assets changed during verification; rerun activation')
        config = merge(json.loads(raw), assets)
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=%s,payload=%s WHERE id=1', (revision,payload))
        db.execute('INSERT INTO config_history VALUES(%s,%s,%s)', (revision,payload,time.time()))
        from rsi_pb import cancel_changed
        cancel_changed(db,json.loads(raw)['assets'],config)
    for asset in assets:
        for frame in asset['timeframes']:
            cache_candles(store, for_timeframe(asset,frame), frame, history[asset['id'],frame])
    return dict(revision=revision, backup=str(backup), candles=counts(history))


def counts(history):
    return {f'{ticker}:{frame}':len(bars) for (ticker,frame),bars in history.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--postgres', action='store_true', help='Use the explicitly configured Postgres runtime')
    source.add_argument('--config', type=Path, help='Verify a saved configuration without changing a database')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    load_env()
    if args.postgres:
        result = activate(Store())
    else:
        state = json.loads(args.config.read_text())
        config = state.get('config',state)
        assets = candidates(config)
        history = verify(assets,int(time.time()*1000))
        result = dict(config=merge(config,assets), assets=assets, candles=counts(history))
    if args.output:
        args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('config','assets')},indent=2))
