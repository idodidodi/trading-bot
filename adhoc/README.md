# Ad hoc tools

## S&P 500 RSI + PB desktop scanner

Open **`adhoc/rsi_pb_scanner.py`** in PyCharm, select the repository's **`.venv` Python interpreter**, then right-click the file and choose **Run 'rsi_pb_scanner'**. No arguments or server are needed. The working directory can be anywhere; paths resolve from the file's location.

Alternatively, from the repository root:

```sh
.venv/bin/python adhoc/rsi_pb_scanner.py
```

Use Python 3.10+ with the repository's `requirements-runtime.txt` installed. The UI uses standard-library Tkinter, available in this project's Mac Python installation. If your own Linux interpreter lacks it, install its `python3-tk` package. Existing Alpaca credentials are read by the scan process from the repository's `.env` (or `TRADING_BOT_ENV_FILE`); they are not stored in UI preferences.

### Controls

- **Timeframes:** independently select Monthly, Weekly, Daily and 4 hours.
- **Recent candles:** 10 includes patterns confirmed within each timeframe's latest 10 closed candles; 1 includes only the latest closed candle. Monthly matches can be months old.
- **History target:** 250 by default. A target of 100 reduces download time. The engine still requires sufficient closed candles; histories below 250 are flagged as limited.
- **Alpaca feed:** Configured uses `.env`/environment settings, defaulting to SIP. SIP and IEX explicitly select that feed; the script never substitutes feeds automatically.
- **Membership:** Refresh online retrieves S&P 500 constituents, falling back visibly to the dated repository snapshot if unavailable. Repository snapshot explicitly uses `stock_universe.json`.
- **▶ Run scan:** starts a new snapshot in a separate process, leaving the UI responsive. Results appear as symbol batches complete. Do not expect an instant full-universe 4-hour scan: provider pagination can be extensive, and native 4-hour bars include extended sessions.
- **■ Stop:** interrupts this scan, saves partial results and leaves completed candle batches available. A pending network request may need to return first. **Force stop** kills the process if necessary; use Resume to rebuild a report from saved batches.
- **Resume run:** select the original output folder. This reuses its cutoff, universe, feed, timeframes, history and strategy rules, regardless of the currently displayed form settings. It retries missing/failed batches. Start a new run for a fresh market snapshot.
- **Load results:** view a previously saved output folder without downloading data.

### Reviewing results

**Stocks** lists each qualifying stock once. **Patterns** lists individual timeframe matches, RSI direction, opposite pinbar, confirmation time, age and structural-breach flags. **Coverage** shows qualified/no-signal/insufficient/unavailable/not-scanned combinations, candle counts and actual last candle dates. **Activity log** shows scan progress and errors. A structural breach describes subsequent price action; it is not an extra exclusion rule in your strategy.

Filter by ticker, direction or timeframe; optionally show only unbreached or latest-candle patterns. Pattern filters affect Stocks and Patterns; Coverage remains a data-availability view filtered by ticker and timeframe. Click a column header to sort. Double-click any row for supporting evidence. **Copy tickers** copies visible stocks; **Export visible CSV** exports the currently selected results tab in its visible order. **Open output folder** opens the saved files.

Runs are saved under `data/ad-hoc/rsi-pb-sp500-TIMESTAMP/`, including `report.md`, `qualified-stocks.csv`, `patterns.csv`, `signals.json`, `coverage.csv` and candle checkpoints. UI preferences are saved separately in `data/ad-hoc/ui-settings.json`. These paths are ignored by Git.

The UI reuses `scripts/scan-rsi-pb-sp500.py` and the existing `rsi_pb.detect` strategy. It does not start the production worker, modify bot configuration, write to the database, send Telegram alerts or place orders. Closing the window requests a graceful stop of its own scan before exiting.
