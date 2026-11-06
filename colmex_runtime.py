"""Reconcile authenticated requests into one exporter owner; no orders or alerts."""
import copy
import json
import os
import time
from pathlib import Path
from colmex_feed import bridge, directory


def refresh(store, config):
    if not os.environ.get('COLMEX_FILES_DIR'):
        return config
    result = copy.deepcopy(config)
    try:
        reader = bridge()
        folder = directory()
        state, available = reader.status(folder)
        catalogue = reader.read_json(Path(folder)/'ColmexCatalogue.json', 1_000_000)
        with store.connect() as db:
            requests = db.execute('SELECT symbol FROM colmex_requested_symbols() ORDER BY symbol').fetchall()
        disabled = {a.get('symbol') for a in result['assets'] if a.get('provider')=='colmex' and a.get('enabled') is False}
        requested = {r[0] for r in requests} - disabled
        configured = {a['symbol'] for a in result['assets'] if a.get('enabled',True)
                      and (a.get('provider') == 'colmex' or 'colmex' in a.get('timeframe_providers',{}).values())}
        desired = requested | configured
        # Existing portfolio and unrelated owners remain managed by the helper.
        revision = reader.set_subscription(folder,'scanner',sorted(desired))
        for symbol in sorted(requested-configured):
            if any(a['id']=='COLMEX-'+symbol for a in result['assets']):
                raise ValueError('Colmex requested asset ID conflicts with a configured asset')
            result['assets'].append(dict(id='COLMEX-'+symbol,provider='colmex',symbol=symbol,
                exchange='Colmex-MT4',market='forex',feed_confirmed=True,enabled=True,
                timeframes=['4h'],scan_strategies=['rsi','pin-bars','rsi-pb'],tradingview_symbol=''))
        registry = reader.read_json(Path(folder)/'ColmexSubscribers.json', 1000000)
        reserved = configured | {s for owner,names in registry['subscribers'].items() if owner != 'scanner' for s in names}
        payload = dict(owner_disabled_symbols=sorted(disabled),reserved_symbols=sorted(reserved),status='collecting',server=state['server'],catalogue=catalogue['symbols'],
            terminal=state,desired_revision=revision['revision'],updated_at=time.time(),
            time_policy_configured=bool(os.environ.get('COLMEX_TIME_POLICY')),
            note='Collection is separate from scanner readiness; see per-asset coverage')
    except (ValueError,OSError,KeyError,TypeError) as exc:
        result = copy.deepcopy(config)
        payload = dict(status='unavailable',updated_at=time.time(),reason=str(exc),catalogue=[])
        # Do not add requested assets when reconciliation fails. Configured assets fail
        # independently through the feed reader; never fall back to another provider.
    with store.connect() as db:
        db.execute('INSERT INTO colmex_status(id,payload) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(payload),))
    return result
