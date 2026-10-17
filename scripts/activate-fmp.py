"""Verify, export and activate S&P 500 daily/weekly/monthly FMP candles locally."""
import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_app import ROOT, Store, load_env, load_rules
from managed_assets import cache_candles, change, get, validate_asset
from fmp_feed import FRAMES, fetch_fmp
from scanner import minimum_history


def activate(store):
    state = get(store)
    old = next((a for a in state['config']['assets'] if a['id'] == 'SPX'), None)
    if old is None:
        raise ValueError('Add an SPX asset before activating FMP')
    asset = old | dict(provider='fmp', symbol='^GSPC', exchange='INDEX', market='index', enabled=True,
                       feed_confirmed=True, timeframes=['monthly','weekly','daily'], timeframe_providers={})
    validate_asset(asset)
    bars = {frame: fetch_fmp(asset, frame) for frame in FRAMES}
    required = minimum_history(load_rules())
    if any(len(values) < required for values in bars.values()):
        raise ValueError('FMP history is too short for all configured strategies; configuration unchanged')
    folder = ROOT / 'data/historical-fmp'
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for frame, values in bars.items():
        path = folder / f'SPX-{frame}.csv'
        with path.open('w', newline='') as output:
            writer = csv.writer(output)
            writer.writerow(['timestamp','closed_at','open','high','low','close'])
            for c in values:
                writer.writerow([datetime.fromtimestamp(c.start/1000,timezone.utc).isoformat(),
                                 datetime.fromtimestamp(c.end/1000,timezone.utc).isoformat(), c.open,c.high,c.low,c.close])
        files.append(dict(file=path.name, asset='SPX', timeframe=frame, candles=len(values),
                          source=dict(provider='FMP', symbol='^GSPC', notes=values.notes)))
    (folder/'manifest.json').write_text(json.dumps(dict(files=files),indent=2)+'\n')
    (folder/'backtest-config.json').write_text(json.dumps(dict(year=2020,timeframes=list(FRAMES),assets=[dict(
        id='SPX',provider='csv',path=str(folder/'{asset}-{timeframe}.csv'))]),indent=2)+'\n')
    backup = Path(store.path).with_name('signals-before-fmp-'+str(time.time_ns())+'.sqlite3')
    with store.connect() as source, closing(sqlite3.connect(backup)) as destination:
        source.backup(destination)
    result = change(store, dict(revision=state['revision'],action='save',asset=asset))
    for frame, values in bars.items():
        cache_candles(store, asset, frame, values)
    return dict(database=str(store.path), revision=result['revision'], history=files, backup=str(backup))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    args=parser.parse_args()
    load_env()
    if not args.database.exists():
        raise SystemExit('Start the app first to initialize its asset configuration')
    try:
        print(json.dumps(activate(Store(args.database)),indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
