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

The base report includes no current-price metric, delayed entry, costs or
optimized exit strategy. Return at a horizon is direction-adjusted versus the recorded lower
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
Research and existing pure watch tests passed together: 37 tests. Production
was not changed.

## Annual verification receipt — 2026-11-27

All 52 weeks and 520 stock/weeks completed, with 148 raw matches and 37
chronologically selected entries. No download failures or unavailable parent
evaluations occurred. All four pilot weeks reproduced exactly. The selected
cohort had 19 provisional-weekly entries and 18 closed-weekly entries; all 37
five-close endpoints were fresh. Four 1h and ten 4h selected outcomes had stale
endpoints and were excluded from those horizon summaries. Entry-time and
per-parent/timeframe slot checks passed. The grouped report is in
`output/weekly-rsi-year-2026-11-27/grouped-by-stock.html` in the main checkout.

## Retest and stop/target research — 2026-11-28

Run the additive, cache-only analysis against the completed annual JSON and its
original cache directory:

```sh
python research/weekly_rsi_exits.py \
  --source output-year/results.json --cache cache --output output-year-exits
python -m unittest test_weekly_rsi_exits test_weekly_rsi_backtest -v
```

No credentials are needed. The loader checks each minute file against the
annual run's SHA256 receipt and fails on absent or changed inputs; it never
requests replacement data. Existing base outputs and entries remain intact.
`retest-exits.html` and `retest-exits.json` contain all 148 raw retest annotations,
37 selected-entry details, the complete parameter ranking, a global fitted
stock breakdown, exploratory stock-specific fits, and chronological validation.
The canonical research rules and production boundary are in
`TELEGRAM_ALERT_LOGIC.md`.

The final percentage grid uses stops 0.1/0.25/0.5/0.75/1/1.5/2/3/4/5/7.5/10/15/20% and targets
0.25/0.5/0.75/1/1.5/2/3/4/5/7.5/10/15/20%. It also includes targets derived
from 0.5/1/1.5/2/3/4/5/6/8 reward-to-risk multiples, capped at 20%. Every distinct
target is crossed with every stop, deduplicated by actual stop/target pair.
Ranking maximizes gross total P&L at equal $1,000
notional per entry; ties choose the lower stop, then lower target. This is a
finite tested grid, not a continuous optimum. Returns on overlapping hypothetical
positions are added without a capital limit; they are not portfolio returns.
No-stop, no-target, and neither-barrier controls are evaluated separately.
The initial 176-pair search used stops 0.25–10%; its winner was on the lower
boundary and did not beat the five-close baseline. The expanded grid and
one-sided controls were added after inspecting that result. This is explicitly
an adaptive historical exploration, not a preregistered optimization.
An intermediate 223-pair grid did not cross every R-derived target with every
stop; the final rectangular grid fixes that coverage gap, so controls and
brackets use identical target choices.

The temporal split uses the pre-close watch start of the 27th week. Training
entries whose five-close horizon crosses that boundary are purged before any
parameter selection. Later entries use the frozen training winner. This is a
retrospective temporal sensitivity check of an already inspected year and grid
expansion, not untouched external validation. There are only 1–6 selected entries per stock. Per-stock fitted
settings have no separate validation and are descriptive only. The observed
minute bars cannot resolve within-minute event ordering or distinguish every
no-trade interval from a missing-data interval. Extrema from the old report are
not used to infer stop/target ordering.

### Exit-study verification receipt — 2026-11-28

The final cache-only run verified 37 original stock/week input checksums and
retained all 37 selected entries and 148 raw matches. It evaluated 504 bracket
pairs and 51 controls. Retests occurred in 36/37 selected entries and 140/148 raw
matches. The fitted 15% stop / 2.5% target returned $150.49 gross at $1,000 per
entry, versus $110.75 for five-close baseline exits. A 20% stop and a no-stop
2.5% target control produced the same result: the stop never triggered, so the
data does not identify an optimal stop or unique reward/risk ratio.

The chronological check used 23 earlier entries, purged 2 overlapping outcome
windows, and evaluated 12 later entries. Its training-selected 15% / 2.5% pair
returned $12.29 in the later period, versus $177.69 for baseline exits. These are
retrospective sensitivity results, not independently validated optimal settings.
All 52 research and existing pure-watch tests passed; report totals, unchanged
entry records and source hashes were independently reconciled. The existing
annual grouped report links to `retest-exits.html`; detailed outcomes, tested
settings, retest observations and checksums are retained in `retest-exits.json`.
