"""Verify ES, XAU/USD and FET/USD; optionally replace USOIL with Yahoo WTI futures."""
import argparse
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_app import Store, load_env, load_rules
from managed_assets import DEFAULTS, cache_candles, for_timeframe, get, validate_asset
from scanner import check_candles, fetch_twelve_data, minimum_history
from yahoo_feed import fetch_yahoo
from tiingo_feed import fetch_tiingo
from kraken_feed import fetch_kraken


def candidates(config, yahoo_oil=False):
    existing = {a['id']: a for a in config['assets']}
    definitions = {
        'ES': dict(name='E-mini S&P 500 — Yahoo selected-contract futures', symbol='ES=F',
                   provider='yahoo', exchange='CME', market='futures'),
        'XAUUSD': dict(name='Gold — Tiingo XAU/USD', symbol='XAU/USD', provider='tiingo',
                       exchange='Tiingo', market='commodity'),
        'FETUSD': dict(name='FET/USD spot', symbol='FET/USD', provider='twelvedata', exchange='Binance',
                       market='crypto', timeframe_providers={f:'kraken' for f in ('1h','4h','daily','weekly')}),
    }
    if yahoo_oil:
        definitions['USOIL'] = dict(name='WTI oil — Yahoo selected-contract futures (CL=F)', symbol='CL=F',
                                    provider='yahoo', exchange='NYMEX', market='futures')
    result = []
    for name, definition in definitions.items():
        old = copy.deepcopy(existing.get(name, {}))
        permitted = ('csv', definition['provider']) + (('tiingo',) if name == 'USOIL' else ())
        if old.get('provider', 'csv') not in permitted:
            raise ValueError(f'{name} already uses another provider; configuration unchanged')
        asset = old | dict(id=name, portfolio=True, enabled=True, feed_confirmed=True,
                           timeframes=old.get('timeframes', DEFAULTS.copy()), tradingview_symbol='',
                           timeframe_providers={}) | definition
        asset.pop('unavailable_reason', None)
        validate_asset(asset)
        result.append(asset)
    return result


def activate(store, yahoo_oil=False):
    state = get(store)
    assets = candidates(state['config'], yahoo_oil)
    required = minimum_history(load_rules())
    history = {}
    for asset in assets:
        for frame in asset['timeframes']:
            feed = for_timeframe(asset, frame)
            fetch = {'yahoo':fetch_yahoo, 'tiingo':fetch_tiingo,
                     'kraken':fetch_kraken, 'twelvedata':fetch_twelve_data}[feed['provider']]
            bars = check_candles(fetch(feed, frame), int(time.time()*1000))
            if len(bars) < required:
                raise ValueError(f"{asset['id']} has only {len(bars)} closed {frame} candles; need {required}. Configuration unchanged")
            history[(asset['id'], frame)] = (feed, bars)
            time.sleep(1)
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(714269001)')
        revision, applied, raw = db.execute('SELECT revision,applied_revision,payload FROM managed_config WHERE id=1').fetchone()
        if revision != state['revision']:
            raise ValueError('Assets changed during verification; rerun activation')
        config = json.loads(raw)
        replacements = {a['id']: a for a in assets}
        config['assets'] = [replacements.pop(a['id'], a) for a in config['assets']]
        config['assets'].extend(replacements.values())
        revision += 1
        payload = json.dumps(config)
        db.execute('UPDATE managed_config SET revision=%s,payload=%s WHERE id=1', (revision, payload))
        db.execute('INSERT INTO config_history VALUES(%s,%s,%s)', (revision, payload, time.time()))
    for (_, frame), (feed, bars) in history.items():
        cache_candles(store, feed, frame, bars)
    return dict(revision=revision,
                candles={a['id']:{f:len(history[(a['id'],f)][1]) for f in a['timeframes']} for a in assets})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yahoo-oil', action='store_true', help='Use Yahoo CL=F futures for USOIL instead of Tiingo')
    args = parser.parse_args()
    load_env()
    try:
        print(json.dumps(activate(Store(), args.yahoo_oil), indent=2))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
