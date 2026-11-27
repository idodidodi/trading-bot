"""Narrow RSI divergence profiles; other strategies keep their own rules."""
PROFILE = 'near-5m-rsi14-v1'


def near_five_minutes(symbol, timeframe):
    return (timeframe == '5m' and isinstance(symbol, str)
            and symbol.rsplit(':', 1)[-1] in ('NEARUSD', 'NEAR/USD'))


def for_symbol(rules, symbol, timeframe):
    if not near_five_minutes(symbol, timeframe):
        return rules
    if rules.get('rsi_profile') == PROFILE:
        return rules
    return rules | dict(rsi_period=14, rsi_profile=PROFILE,
                        skill_hash=PROFILE + ':' + rules['skill_hash'])


def for_asset(rules, asset, timeframe):
    if asset.get('id') != 'NEARUSD' or asset.get('symbol') != 'NEAR/USD':
        return rules
    return for_symbol(rules, asset['symbol'], timeframe)


def superseded_alert(payload):
    target = near_five_minutes(payload.get('symbol'), payload.get('timeframe'))
    if payload.get('asset') not in (None, 'NEARUSD'):
        target = False
    return target and payload.get('rules', {}).get('rsi_period') != 14
