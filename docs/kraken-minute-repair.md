# Historical Kraken minute-gap repair

`repair_kraken_minutes.py` fetches public historical trades for the **same Kraken spot
pair** as a TradingView export. It does not read credentials, contact another venue,
connect to a database, start a scanner or send Telegram/orders. It uses verified TLS
with the installed `certifi` CA bundle.

Kraken's OHLC endpoint returns only the latest 720 entries, so it cannot backfill this
October range directly. The script uses paginated historical Trades requests instead.
Kraken also documents that its candles omit intervals without trades; an absent row
therefore need not represent a lost price record.
[OHLC documentation](https://docs.kraken.com/api-reference/market-data/get-ohlc-data),
[Trades documentation](https://docs.kraken.com/api-reference/market-data/get-recent-trades),
[historical data and empty intervals](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data).

## Run

```sh
.venv/bin/python repair_kraken_minutes.py \
  --1m '/path/to/NEAR-1m.csv' \
  --pair NEAR/USD --as-of '2026-10-31T13:45:00Z' \
  --include-empty --record-overlap-differences \
  --cache data/kraken-gap-repair --output data/new-repair
```

Optional `--5m`, `--15m`, `--30m`, `--1h` and `--4h` files let the same verified minute
timeline repair missing larger intervals. `--max-pages` bounds work (default 1,000).
Requests are paced at least one second apart, with bounded retries/backoff. Pages are
cached by pair and exact cursor so reruns reuse downloads after interruptions. Choose
a new output directory; original exports are never overwritten. A nonzero return
indicates unresolved work or validation failure, not complete recovery.

For each gap, request a neighboring existing candle too, verify pair identity, cursor
advancement and chronological trade order, deduplicate trade IDs, and aggregate only
fully covered minutes. Compare existing OHLCV against the reconstructed candles.
Default behavior rejects mismatches. `--record-overlap-differences` instead records both
versions and **preserves export values**, allowing a visibly qualified repair.

Only `--include-empty` permits verified no-trade minutes to be represented as flat
observations at the preceding close with zero volume. The user explicitly approved
this policy. Rows are labeled `verified_no_trades`; they are **not recovered trades**.
Other statuses distinguish original export values, recovered trade candles and larger
aggregates. Output `manifest.json` records hashes of the inputs, outputs, script and
cached response pages, cursor evidence, empty minutes, conflicts and unresolved gaps.

## Backtest handling

`chasing_research.py` verifies repaired CSV hashes against the adjacent manifest.
Empty observations establish clock continuity but are excluded from RSI/BB candle
sequences, preserving the sparse traded-candle indicator convention. They never receive
simulated fills. Entry uses the next traded minute open strictly after alert availability.
Exit uses the first traded minute open at or after the common original-horizon deadline;
exit delay is recorded. Price extremes after the exit open are not included in MAE/MFE.
Unknown/unverified gaps still make a path incomplete.

The report distinguishes conditional executed-entry results, paired outcomes for the
same roots and per-original-root return when unentered allocations remain cash. None
is represented as a capital-constrained portfolio or an actual broker fill. Cost is a
configurable hypothetical total roundtrip assumption, not a verified account fee.

## Completed NEAR/USD run

Pinned cutoff: 31 October 2026, 13:45 UTC. Public Kraken history checked in 203 cached
pages. All **1,763** absent minute intervals were confirmed to contain **no trades**;
there were **zero lost traded candles recovered**. With the approved labeled empty
observations, remaining gaps are zero across all six exported timeframes. This also
resolved 10 missing 5m intervals, two 15m intervals and one 30m interval.

**15,596** overlapping existing minute candles were checked. **Four** differ between
TradingView and current Kraken trade history, at 21 October 04:20 and 11:15 UTC,
28 October 10:59 UTC, and 29 October 10:40 UTC. Both versions are recorded in the
manifest; original export OHLCV is retained. Feed revisions/filtering remain unverified,
so the sources must not be described as identical everywhere.

Local, ignored outputs:

- `data/kraken-near-repaired/`: six normalized CSV copies, `manifest.json`, `report.md`.
- `data/kraken-gap-repair/`: raw, cached public response pages.
- `data/chasing-near-complete-comparison/`: rerun outcome report and event ledger.

The rerun has **six complete original-entry outcomes**, **two complete chase-entry
outcomes**, and **four no-entry chase cases**, with no incomplete outcomes. This removes
the prior missing-interval blocker. Six roots and two chase entries in one asset are
too few to validate future profitability, a success probability or a volume filter.
The comparison retains hypothetical costs, source conflicts and all no-entry cases.
