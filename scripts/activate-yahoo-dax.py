"""Verify Yahoo delayed DAX candles, back up SQLite, then activate the exact index."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_app import Store, load_rules
from managed_assets import cache_candles, get, validate_asset
from scanner import minimum_history
from yahoo_feed import fetch_yahoo


def activate(store):
    state = get(store)
    old = next((a for a in state['config']['assets'] if a['id'] == 'DAX'), None)
    if old is None:
        raise ValueError('Add DAX before activating Yahoo')
    if old['provider'] not in ('csv', 'yahoo'):
        raise ValueError('DAX already has another API provider; configuration unchanged')
    asset = old | dict(provider='yahoo', symbol='^GDAXI', exchange='XETRA', market='index',
                       enabled=True, feed_confirmed=True, timeframe_providers={}, tradingview_symbol='')
    asset.pop('unavailable_reason', None)
    validate_asset(asset)
    history = {frame: fetch_yahoo(asset, frame) for frame in asset['timeframes']}
    required = minimum_history(load_rules())
    for frame, bars in history.items():
        if len(bars) < required:
            raise ValueError(f'Only {len(bars)} closed {frame} candles; need {required}. Configuration unchanged')
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        revision, applied, raw = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if revision != state['revision']:
            raise ValueError('Assets changed during verification; rerun activation')
        config = json.loads(raw)
        config['assets'] = [asset if a['id'] == 'DAX' else a for a in config['assets']]
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=%s,payload=%s WHERE id=1', (revision, payload))
        db.execute('INSERT INTO config_history VALUES(%s,%s,%s)', (revision, payload, time.time()))
    for frame, bars in history.items():
        cache_candles(store, asset, frame, bars)
    return dict(database='Postgres', revision=revision,
                candles={f: len(b) for f, b in history.items()},
                note='Yahoo delayed DAX cash index; restart running scanners to load the adapter')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    try:
        print(json.dumps(activate(Store()), indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
