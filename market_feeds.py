"""Targeted activation of the five permanent index and commodity feeds."""
import copy

from managed_assets import DEFAULTS, SCAN_STRATEGIES, for_timeframe, validate_asset
from platform_app import load_rules
from scanner import check_candles, minimum_history

DEFINITIONS = {
    'SPX': dict(name='S&P 500 cash index — Yahoo delayed', provider='yahoo',
                symbol='^GSPC', exchange='SNP', market='index'),
    'DAX': dict(name='DAX cash index — Yahoo delayed', provider='yahoo',
                symbol='^GDAXI', exchange='XETRA', market='index'),
    'DOLLAR_INDEX': dict(name='US Dollar Index — Yahoo delayed', provider='yahoo',
                         symbol='DX-Y.NYB', exchange='NYB', market='index'),
    'XAUUSD': dict(name='Gold — Tiingo XAU/USD', provider='tiingo',
                   symbol='XAU/USD', exchange='Tiingo', market='commodity'),
    'USOIL': dict(name='WTI oil — Tiingo tokenized derived price', provider='tiingo',
                  symbol='USOIL/USD', exchange='Tiingo', market='commodity'),
}


def candidates(config):
    existing = {a['id']: a for a in config['assets']}
    result = []
    for ticker, definition in DEFINITIONS.items():
        asset = copy.deepcopy(existing.get(ticker, {}))
        asset.update(definition, id=ticker, enabled=True, feed_confirmed=True,
                     portfolio=True, timeframe_providers={}, tradingview_symbol='')
        asset.setdefault('timeframes', config.get('timeframes', DEFAULTS).copy())
        asset.setdefault('scan_strategies', SCAN_STRATEGIES.copy())
        asset.pop('unavailable_reason', None)
        validate_asset(asset)
        result.append(asset)
    return result


def merge(config, assets):
    result = copy.deepcopy(config)
    replacements = {a['id']: copy.deepcopy(a) for a in assets}
    result['assets'] = [replacements.pop(a['id'], a) for a in result['assets']]
    result['assets'].extend(replacements.values())
    return result


def verify(assets, now):
    from yahoo_feed import fetch_yahoo
    from tiingo_feed import fetch_tiingo
    import pin_bars
    import bb_pin_bars
    import rsi_pb
    required = {'rsi': minimum_history(load_rules()),
                'pin-bars': pin_bars.minimum_history(pin_bars.load_rules()),
                'bb-pin-bars': bb_pin_bars.minimum_history(bb_pin_bars.load_rules()),
                'rsi-pb': rsi_pb.minimum_history(rsi_pb.load_rules())}
    history = {}
    for asset in assets:
        need = max(required[s] for s in asset['scan_strategies'])
        for frame in asset['timeframes']:
            feed = for_timeframe(asset, frame)
            bars = {'yahoo': fetch_yahoo, 'tiingo': fetch_tiingo}[feed['provider']](feed, frame)
            if len(check_candles(bars, now)) < need:
                raise ValueError(f'{asset["id"]} {frame}: need {need} closed candles; configuration unchanged')
            history[asset['id'], frame] = bars
    return history
