"""Download public 2020 replay data into a separate historical directory.

Explicit source choices: Yahoo OHLC for cash indices/FX/NVDA; Kraken BTC/USD and NEAR/USD.
Never modifies scanner.json, .env, or the live database.
"""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import urllib.parse

from platform_app import ROOT, load_rules
from scanner import Candle, check_candles, interval_end, minimum_history

DEST = ROOT / 'data' / 'historical-2020'
UTC = timezone.utc
YAHOO = {'EURUSD': 'EURUSD=X', 'DAX': '^GDAXI', 'DOLLAR_INDEX': 'DX-Y.NYB', 'NVDA': 'NVDA'}


def download(url, filename):
    if not filename.exists():
        temp = filename.with_suffix('.download')
        subprocess.run(['curl', '-L', '--fail', '--silent', '--show-error', '--max-time', '45', '-A', 'Mozilla/5.0', url, '-o', str(temp)], check=True)
        json.loads(temp.read_text())
        temp.replace(filename)
    return json.loads(filename.read_text())


def stamp(dt):
    return int(dt.timestamp() * 1000)


def yahoo_daily(asset, symbol):
    query = urllib.parse.urlencode(dict(period1=1388534400, period2=1609459200, interval='1d'))
    url = 'https://query2.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='') + '?' + query
    data = download(url, DEST / 'raw' / (asset + '.json'))
    result = data['chart']['result'][0]
    if result['meta']['symbol'] != symbol:
        raise ValueError('Unexpected source symbol')
    quotes = result['indicators']['quote'][0]
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(result['meta']['exchangeTimezoneName'])
    bars = []
    rejected = []
    for i, t in enumerate(result['timestamp']):
        values = [quotes[k][i] for k in ('open', 'high', 'low', 'close')]
        if any(v is None or not math.isfinite(v) for v in values) or not values[2] <= min(values[0], values[3]) <= max(values[0], values[3]) <= values[1]:
            rejected.append(dict(timestamp=t, prices=values))
            continue
        # Preserve source session date; use UTC calendar-day boundaries consistently.
        date = datetime.fromtimestamp(t, zone).date()
        start = datetime(date.year, date.month, date.day, tzinfo=UTC)
        bars.append(Candle(stamp(start), stamp(start+timedelta(days=1)), *values))
    (DEST/'raw'/(asset+'-rejected.json')).write_text(json.dumps(rejected,indent=2))
    return check_candles(bars, stamp(datetime(2021,1,1,tzinfo=UTC))), url


def aggregate(bars, timeframe):
    groups = {}
    for bar in bars:
        dt = datetime.fromtimestamp(bar.start/1000, UTC)
        if timeframe == 'weekly':
            start = dt.replace(hour=0, minute=0, second=0)-timedelta(days=dt.weekday())
        elif timeframe == 'monthly':
            start = dt.replace(day=1, hour=0, minute=0, second=0)
        else:
            start = dt.replace(hour=dt.hour//4*4, minute=0, second=0)
        groups.setdefault(start, []).append(bar)
    out = []
    for start, group in sorted(groups.items()):
        end = interval_end(start, timeframe)
        # Reject incomplete 4h buckets rather than invent missing exchange candles.
        if timeframe == '4h' and (len(group)!=4 or [c.start for c in group]!=[stamp(start)+i*3600000 for i in range(4)]):
            continue
        if stamp(end)>stamp(datetime(2021,1,1,tzinfo=UTC)):
            continue
        out.append(Candle(stamp(start),stamp(end),group[0].open,max(c.high for c in group),min(c.low for c in group),group[-1].close))
    return out


def write(asset, timeframe, bars, source):
    path = DEST / f'{asset}-{timeframe}.csv'
    with path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp','closed_at','open','high','low','close'])
        for c in bars:
            writer.writerow([datetime.fromtimestamp(c.start/1000,UTC).isoformat(),datetime.fromtimestamp(c.end/1000,UTC).isoformat(),c.open,c.high,c.low,c.close])
    warmup = sum(c.end<=stamp(datetime(2020,1,1,tzinfo=UTC)) for c in bars)
    rejected_path = DEST/'raw'/(asset+'-rejected.json')
    rejected = len(json.loads(rejected_path.read_text())) if rejected_path.exists() else 0
    return dict(asset=asset,timeframe=timeframe,file=path.name,source=source,candles=len(bars),warmup_candles=warmup,required_warmup=minimum_history(load_rules()),rejected_source_rows=rejected,sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    (DEST/'raw').mkdir(parents=True,exist_ok=True)
    manifest = {'notes':['Yahoo source OHLC used without additional dividend adjustment; stock series may be split-adjusted by provider.', 'Daily candles use source session date with conservative UTC calendar-day ends; weekly buckets start Monday UTC, monthly buckets start on the first.', 'No daily-to-intraday interpolation. Non-crypto 4h history remains unavailable.', 'Colmex/Colmex Pro public historical download not located; Yahoo fallback used.'], 'files':[], 'errors':[]}
    for asset,symbol in YAHOO.items():
        try:
            bars,url = yahoo_daily(asset,symbol)
            for tf in ('daily','weekly','monthly'):
                manifest['files'].append(write(asset,tf,bars if tf=='daily' else aggregate(bars,tf),url))
            print(asset, 'daily/weekly/monthly prepared', flush=True)
        except Exception as exc:
            manifest['errors'].append(dict(asset=asset,error=type(exc).__name__))
            print(asset,'download failed', flush=True)
    (DEST/'manifest.json').write_text(json.dumps(manifest,indent=2))
    assets = [dict(id=a,provider='csv',path=str(DEST/'{asset}-{timeframe}.csv')) for a in (*YAHOO,'BTCUSD','NEARUSD')]
    (DEST/'backtest-config.json').write_text(json.dumps(dict(assets=assets,timeframes=['monthly','weekly','daily','4h']),indent=2))
    from kraken_history import main as prepare_kraken
    prepare_kraken()
    from backtest import run_backtest
    from platform_app import Store
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        report = run_backtest(Store(Path(tmp)/'replay.sqlite3'), json.loads((DEST/'backtest-config.json').read_text()), load_rules())
    (DEST/'report.json').write_text(json.dumps(report,indent=2))
    print('Historical config:', DEST/'backtest-config.json')


if __name__=='__main__':
    main()
