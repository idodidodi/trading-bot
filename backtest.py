"""Replay historical CSV candles; never enqueue live alerts or send Telegram."""
import json
import os
from pathlib import Path
from scanner import check_candles, detect, load_config, minimum_history, read_csv, timestamp
from platform_app import ROOT, Store, load_env, load_rules

START = timestamp('2020-01-01T00:00:00Z')
END = timestamp('2021-01-01T00:00:00Z')


def replay(candles, rules, symbol, timeframe, *, provisional=False):
    # Exclude future candles before calculating indicators and confirming pivots.
    bars = check_candles(candles, END)
    signals = [s for s in detect(bars, rules, symbol, timeframe, provisional=provisional) if START <= s['confirmed_at'] < END]
    warmup = sum(c.end <= START for c in bars)
    complete = bool(bars and warmup >= minimum_history(rules) and bars[-1].end >= END - {'4h': 14400000, 'daily': 86400000, 'weekly': 604800000, 'monthly': 2678400000}[timeframe])
    return dict(status='replayed' if complete else 'partial history', candles=len(bars), warmup_candles=warmup, signals=signals,
                note='Calendar range checked; trading-session gaps are not verified.')


def run_backtest(store, config, rules, *, provisional=False):
    results = []
    for asset in config['assets']:
        for timeframe in config['timeframes']:
            row = dict(asset=asset['id'], timeframe=timeframe)
            try:
                if asset['provider'] != 'csv':
                    raise ValueError('Supply historical CSV files; live provider requests do not cover 2020.')
                row.update(replay(read_csv(asset, timeframe), rules, asset['id'], timeframe, provisional=provisional))
                csv_path = Path(asset['path'].format(asset=asset['id'], timeframe=timeframe))
                if not csv_path.is_absolute():
                    csv_path = ROOT / csv_path
                manifest_path = csv_path.parent / 'manifest.json'
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text())
                    provenance = next((f for f in manifest['files'] if f['file'] == csv_path.name), None)
                    if provenance:
                        row['source'] = provenance['source']
                        if provenance.get('rejected_source_rows'):
                            row.update(status='partial history', note=f"{provenance['rejected_source_rows']} malformed source rows excluded; gaps can affect indicator and pivot results.")
            except FileNotFoundError:
                row.update(status='unavailable', reason='Historical candle CSV missing', signals=[])
            except Exception:
                row.update(status='unavailable', reason='Historical CSV required with valid OHLC and timezone timestamps', signals=[])
            results.append(row)
    report = dict(year=2020, rules=rules, results=results)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS backtest_runs(id INTEGER PRIMARY KEY, report TEXT)')
        db.execute('INSERT INTO backtest_runs(report) VALUES(?)', (json.dumps(report),))
    store.log('Backtest completed', '2020 historical signal replay')
    return report


if __name__ == '__main__':
    load_env()
    data = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    data.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run_backtest(Store(data / 'signals.sqlite3'), load_config(), load_rules()), indent=2))
