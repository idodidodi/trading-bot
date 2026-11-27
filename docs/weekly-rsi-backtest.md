# Weekly RSI strategy research

Run the isolated research tool; it never instantiates a Store, starts a worker,
accesses a database, sends Telegram messages or places orders. Production alert
semantics and the explicit research-mode exceptions are documented in
`TELEGRAM_ALERT_LOGIC.md`. Research does not modify the deployed watch.

## Run

Use the repository Python environment and configured Alpaca credentials:

```sh
python research/weekly_rsi_backtest.py --mode pilot \
  --credentials-file /path/to/private/worker.env \
  --cache /path/to/private/research-cache --output output/weekly-rsi-pilot
```

The optional credentials file is read only for ALPACA_API_KEY and
ALPACA_SECRET_KEY. Never put key values in command arguments or chat. The
tool uses the existing GET-only Alpaca data helper, with explicit SIP and split
adjustment. Cached responses contain prices and request parameters, not keys.
The cache is reusable; result JSON contains file checksums and request receipts.
Two throttled historical download processes are used by default; pass
`--download-workers 1` for serial requests. These do not start scanner workers.

Pilot: 10 explicitly selected stocks and four weekly closing sessions:
2025-11-28 (early close), 2026-03-06 and 2026-03-13 (DST boundary), and
2026-04-02 (Thursday before Good Friday). This is a data/replay validation
sample, not a statistically sufficient estimate of strategy quality.

The one-year mode evaluates all 52 weekly closing sessions in the default
2025-11-01 through 2026-11-01 exclusive range:

```sh
python research/weekly_rsi_backtest.py --mode year \
  --start 2025-11-01 --end 2026-11-01 \
  --symbols AAPL MSFT NVDA AMZN META GOOGL JPM XOM JNJ WMT \
  --credentials-file /path/to/private/worker.env \
  --cache /path/to/private/research-cache --output output/weekly-rsi-year
```

The symbol list is a fixed cohort. It must not be described as a historical
S&P 500 or full-US backtest. Supply a researched cohort explicitly; historical
membership/market-cap eligibility is not implemented. Whole selected weekly
watches continue to the following open, and their outcome windows can extend
beyond the nominal study end. No future calendar weeks are silently included.

## Data and outputs

Each stock/week obtains 275 calendar weeks of weekly warm-up, prior completed
days in the forming week, 130 calendar days of 15-minute lower warm-up and
one-minute bars covering the watch and outcome periods. Every evaluated
timeframe requires 250 bars. Current-week last-session prices are reconstructed
from visible regular-session minutes. Lower/outcome data includes 04:00–20:00
New York sessions. Scheduled time, rather than number of observed bars, defines
the 1h/4h horizons. Missing candles are never interpolated.
Weekly warm-up uses the requested cutoff's historical as-of symbol mapping,
including FB history for META. Intraday ranges use explicit contemporary symbols;
when expanding beyond the pilot cohort, audit renames/delistings within those
ranges rather than assuming a complete point-in-time security master exists.

`results.json` retains rules and detector/engine hashes, dates, source/cache
receipts, coverage, unavailable-data audit, parent lifecycle, all signal pivot
pairs, entry times/status, later parent invalidation, and fixed-horizon results.
`report.html` is a standalone filterable report. It shows chronological selected
entries, all-signal timeframe comparisons, week counts and detailed outcomes.

No current-price metric, delayed entry, costs or optimized exit strategy is
included. Return at a horizon is direction-adjusted versus the recorded lower
close. Maximum DD is adverse excursion from entry, not account drawdown.
Extrema timestamps identify the earliest minute containing the extreme. A stale
horizon endpoint has no return/exit-price result; no prices means incomplete.
Complete-row summary metrics exclude incomplete and stale endpoint outcomes.
The raw JSON retains sparse samples and coverage flags; these deserve scrutiny.

The native historical weekly/daily provider bars may contain later corrections;
this is not an as-published-feed replay. Historical stock splits are adjusted
consistently within each run but provider revisions between downloads remain a
limitation. Do not infer execution feasibility or aggregate correlated signals
into portfolio P&L without an independently specified portfolio/exit model.

## Verification

```sh
python -m unittest test_weekly_rsi_backtest -v
```

Tests use synthetic candles and the real regular RSI detector. They verify
causal prefix equivalence, future-price exclusion, weekly provisional entry,
per-timeframe limits, later invalidation retention, direction, holiday/DST
boundaries, horizon math, missing data and chronological selection.

## Pilot verification receipt — 2026-11-27

The four-week, ten-stock SIP pilot completed with zero stock/week download
failures and no unavailable parent evaluations. It produced 12 raw entries
(6 on 15m, 4 on 30m, 2 on 1h, none on 4h), of which 8 entered while the
weekly candle was provisional and 4 after it closed. Chronological selection
retained three stock/week/direction entries. Of 60 raw fixed-horizon outcomes,
56 had fresh endpoints and 4 were explicitly marked stale and excluded from
return summaries. This verifies the pilot pipeline, not strategy profitability.
The full 52-week run has not been executed. Research and existing pure watch
tests passed together: 37 tests. Production was not changed.
