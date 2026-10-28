"""Verify five market feeds and activate a saved database, or export a verified config."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_app import Store, load_env
from managed_assets import get, cache_candles, for_timeframe
from cloud_sync import enqueue
from market_feeds import candidates, merge, verify


def activate(store):
    state = get(store)
    assets = candidates(state['config'])
    history = verify(assets, int(time.time()*1000))
    backup = Path(store.path).parent/'backups'/f'before-five-market-feeds-{time.time_ns()}.sqlite3'
    backup.parent.mkdir(parents=True, exist_ok=True)
    with store.connect() as source, closing(sqlite3.connect(backup)) as destination:
        source.backup(destination)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revision, applied, raw = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if revision != state['revision'] or json.loads(raw) != state['config']:
            raise ValueError('Assets changed during verification; rerun activation')
        config = merge(json.loads(raw), assets)
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1', (revision,payload))
        db.execute('INSERT INTO config_history VALUES(?,?,?)', (revision,payload,time.time()))
        enqueue(db, 'config', 'current', dict(revision=revision,applied_revision=applied,config=config))
    for asset in assets:
        for frame in asset['timeframes']:
            cache_candles(store, for_timeframe(asset,frame), frame, history[asset['id'],frame])
    return dict(revision=revision, backup=str(backup), candles=counts(history))


def counts(history):
    return {f'{ticker}:{frame}':len(bars) for (ticker,frame),bars in history.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database', type=Path)
    source.add_argument('--config', type=Path, help='Verify a saved configuration without changing a database')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    load_env()
    if args.database:
        if not args.database.exists():
            raise SystemExit('Database must already exist')
        result = activate(Store(args.database))
    else:
        state = json.loads(args.config.read_text())
        config = state.get('config',state)
        assets = candidates(config)
        history = verify(assets,int(time.time()*1000))
        result = dict(config=merge(config,assets), assets=assets, candles=counts(history))
    if args.output:
        args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('config','assets')},indent=2))
