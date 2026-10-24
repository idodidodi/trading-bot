"""Verify Tiingo's WTI-derived series before activating the USOIL scanner entry."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_app import Store, load_env, load_rules
from managed_assets import cache_candles, for_timeframe, get, validate_asset
from scanner import minimum_history
from tiingo_feed import fetch_tiingo


def activate(store):
    state = get(store)
    old = next((a for a in state['config']['assets'] if a['id'] == 'USOIL'), None)
    if old is None:
        raise ValueError('Add USOIL before activating its feed')
    if old['provider'] != 'csv' and not (old['provider'] == 'tiingo' and old.get('symbol') == 'USOIL/USD'):
        raise ValueError('USOIL already has a different API feed; configuration unchanged')
    asset = old | dict(name='WTI oil — Tiingo tokenized derived price', market='commodity',
                       provider='tiingo', symbol='USOIL/USD', exchange='Tiingo', enabled=True,
                       feed_confirmed=True, timeframe_providers={}, tradingview_symbol='')
    asset.pop('unavailable_reason', None)
    validate_asset(asset)
    required = minimum_history(load_rules())
    history = {frame: fetch_tiingo(asset, frame) for frame in asset['timeframes']}
    for frame, bars in history.items():
        if len(bars) < required:
            raise ValueError(f'Only {len(bars)} closed {frame} candles; need {required}. Configuration unchanged')
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        revision, applied, raw = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if revision != state['revision']:
            raise ValueError('Assets changed during verification; rerun activation')
        config = json.loads(raw)
        config['assets'] = [asset if a['id'] == 'USOIL' else a for a in config['assets']]
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=%s,payload=%s WHERE id=1', (revision, payload))
        db.execute('INSERT INTO config_history VALUES(%s,%s,%s)', (revision, payload, time.time()))
    for frame, bars in history.items():
        cache_candles(store, for_timeframe(asset, frame), frame, bars)
    return dict(database='Postgres', revision=revision, candles={f: len(b) for f, b in history.items()},
                 note='Tiingo derived WTI series; distinct from FOREXCOM:USOIL. Restart the app to load adapter changes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    load_env()
    try:
        print(json.dumps(activate(Store()), indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
