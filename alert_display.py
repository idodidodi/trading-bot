"""Public Telegram labels; never used for feed selection or delivery identities."""
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET


@lru_cache(maxsize=1)
def symbol_names():
    root = ET.parse(Path(__file__).with_name('alert-symbol-map.xml')).getroot()
    return {symbol.text: asset.attrib['name']
            for asset in root.findall('asset') for symbol in asset.findall('symbol')}


def display_name(symbol):
    ticker = symbol.rsplit(':', 1)[-1]
    return symbol_names().get(ticker, ticker)


def provider_name(source):
    key = source.split(':', 1)[0]
    return {'yahoo': 'Yahoo', 'tiingo': 'Tiingo', 'fmp': 'FMP',
            'kraken': 'Kraken', 'twelvedata': 'Twelve Data', 'oanda': 'OANDA',
            'alpaca': 'Alpaca', 'csv': 'CSV'}.get(key.lower(), key)


def public_text(text):
    """Clean every message at the send boundary, including queued older alerts."""
    lines = []
    provider = None
    for line in text.split('\n'):
        content = line.strip()
        if content.startswith('Source:'):
            value = content.split(':', 1)[1].strip()
            # Only feed IDs yield a provider. Machine/environment footers do not.
            if value.count(':') == 2 and ' · ' not in value:
                provider = provider_name(value)
            elif value.startswith('Yahoo Finance'):
                provider = 'Yahoo'
            continue
        if content.startswith('Provider:'):
            provider = content.split(':', 1)[1].strip()
            continue
        if content == '--------------------':
            continue
        # Signal headers have a symbol followed by their timeframe.
        parts = line.split(' · ')
        if len(parts) > 1 and parts[1] in ('1h', '4h', '1d', '1w', '1mo', 'daily', 'weekly', 'monthly'):
            symbol = parts[0]
            if symbol.count(':') == 2:
                provider = provider_name(symbol)
            parts[0] = display_name(symbol)
            line = ' · '.join(parts)
        lines.append(line)
    result = '\n'.join(lines).rstrip()
    if provider:
        result += '\n--------------------\nProvider: ' + provider
    return result
