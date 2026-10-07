"""Verify local OANDA credentials and exact forex feeds before moving any assets."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_app import ROOT, Store, load_env, load_rules
from managed_assets import get, validate_asset
from oanda_feed import available_forex, environment, fetch_oanda
from scanner import check_candles, minimum_history
from cloud_sync import enqueue


def activate(store):
    supported = available_forex()
    mode = environment()
    state = get(store)
    config = state['config']
    required = minimum_history(load_rules())
    candidates = {a['symbol']: a.get('timeframes', config['timeframes']) for a in config['assets']
                  if a.get('market') == 'forex' and a.get('enabled', True) and a['provider'] in ('twelvedata', 'oanda')}
    with store.connect() as db:
        row = db.execute('SELECT payload FROM daily_universe ORDER BY session DESC LIMIT 1').fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_universe'").fetchone() else None
    if row:
        for item in json.loads(row[0])['selected']['forex']:
            candidates.setdefault(item['symbol'], config['timeframes'])
    verified, skipped = set(), {}
    for symbol, frames in candidates.items():
        if symbol not in supported:
            skipped[symbol] = 'Exact pair not offered by this OANDA account; Twelve Data retained'
            continue
        feed = dict(symbol=symbol, exchange='OANDA', feed_confirmed=True)
        try:
            for frame in frames:
                bars = check_candles(fetch_oanda(feed, frame), int(time.time() * 1000))
                if len(bars) < required:
                    raise ValueError(f'Only {len(bars)} closed {frame} candles; need {required}')
                time.sleep(1)
            verified.add(symbol)
        except ValueError as exc:
            skipped[symbol] = str(exc)
    if not verified:
        raise ValueError('No configured forex pairs passed OANDA verification; configuration was not changed')
    backup = Path(store.path).with_name('signals-before-oanda-' + str(time.time_ns()) + '.sqlite3')
    with store.connect() as source, closing(sqlite3.connect(backup)) as destination:
        source.backup(destination)
    moved = []
    for asset in config['assets']:
        if asset.get('market') == 'forex' and asset.get('enabled', True) and asset.get('symbol') in verified and asset['provider'] in ('twelvedata', 'oanda'):
            asset.update(provider='oanda', exchange='OANDA', feed_confirmed=True, timeframe_providers={}, tradingview_symbol='')
            validate_asset(asset)
            moved.append(asset['id'])
    policy = config.get('daily_universe', {})
    if policy.get('enabled'):
        policy.update(forex_provider='oanda', oanda_verified_pairs=sorted(supported - set(skipped)),
                      source='Kraken 24-hour USD turnover; OANDA verified forex; Kraken native crypto; Twelve Data remaining feeds')
    config['oanda_request_spacing_seconds'] = 1
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        revision, applied = db.execute('SELECT revision,applied_revision FROM managed_config WHERE id=1').fetchone()
        if revision != state['revision']:
            raise ValueError('Assets changed during verification; rerun activation with the latest settings')
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=?,payload=? WHERE id=1', (revision, payload))
        db.execute('INSERT INTO config_history VALUES(?,?,?)', (revision, payload, time.time()))
        enqueue(db, 'config', 'current', dict(revision=revision, applied_revision=applied, config=config))
    store.log('OANDA activated', json.dumps(dict(environment=mode, moved=moved, skipped=skipped)))
    return dict(environment=mode, moved=moved, skipped=skipped, revision=revision,
                note='Restart the local app to load OANDA credentials and the new routing. Hourly remains opt-in.')


if __name__ == '__main__':
    load_env()
    path = Path(os.environ.get('DATA_DIR', str(ROOT / 'data'))) / 'signals.sqlite3'
    if not path.exists():
        raise SystemExit('Start the local app first to initialize its asset configuration')
    try:
        print(json.dumps(activate(Store(path)), indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
