# Annual NEAR/USD divergence study

The annual study is a cached Python batch, separate from the production scanner and
Telegram rollout. It uses no account credentials, submits no orders, sends no alerts,
and does not connect to the production database.

```sh
.venv/bin/python -u annual_near_backtest.py \
  --cache data/annual-near-cache --output data/near-one-year
```

The default original-signal interval is 2025-10-31 00:00 UTC inclusive through
2026-10-31 00:00 UTC exclusive. Indicator warm-up starts 2025-09-01. The fixed data
cutoff is 2026-10-31 13:45 UTC. Bounds and actual evidence are stored with the output.
Do not interpret a censored late window as success or absence of failure.

## Sources and missing intervals

The script retrieves only the exact `NEARUSD_1.csv` member of Kraken's official public
OHLCVT archives using cached byte ranges. It reads the entire member to verify its CRC
and records a SHA-256 hash, archive manifest, declared coverage and source URLs. The full
release covers through September 2026; the overlapping Q3 release must agree exactly.
A coverage check rejects declaring missing intervals empty beyond the source manifest.
October uses paginated Kraken public trades, cached as raw responses with hashes,
opaque cursor continuity and trade-ID deduplication across page boundaries. A displayed
timestamp can round the cursor-boundary trade by less than one microsecond; older
history outside that tolerance fails validation.

Minute OHLCV is aggregated into 5m, 15m, 30m, 1h and 4h bars with UTC boundaries.
Verified absent-trade minutes carry the prior traded close, zero volume and zero trades.
Every such observation is labeled `verified_no_trades`; all output files have provenance
hashes. Empty observations are excluded from the RSI/Bollinger candle sequence and
cannot receive hypothetical entry or exit fills. A prior traded close seeds an initial
empty interval. Any missing source coverage remains an error, not a fabricated candle.

Provider documentation:
[Kraken OHLCVT archives](https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data),
[Kraken historical trades](https://support.kraken.com/articles/360047543791-downloadable-historical-market-data-time-and-sales),
[public trade pagination](https://docs.kraken.com/api-reference/market-data/get-recent-trades).
TradingView exports and direct provider data are not assumed identical; this annual run
uses one explicitly recorded native-provider dataset.

## Outputs and interpretation

- `report.md`: definitions, interval, source cutoff and counts.
- `research.md`: hypothetical return comparison and descriptive closing-volume/relative-volume rank correlations; `volume_summary.json` contains the correlation inputs' counts and results.
- `cases.md`: a readable card for every original 4h divergence, in Israel time.
- `cases.csv` and `cases.json`: every original, exact UTC times, follow-up timeframe,
  price anchors, adverse/favorable extremes, recovery, threshold and censorship status.
- `replay.json`: causal chasing event ledger, source audit, timeframe reconciliation,
  hypothetical original/first-chase outcomes, costs and volume observations.
- `report_parameters.json`: report parameters and code/replay hashes.
- `candles/`: all six normalized CSVs and their source-evidence manifest.

The causal replay checks minute closes, inspecting newly eligible same-direction signals
from smaller to larger timeframe. It stops at the first finding or the favorable-stop
threshold (default 0.5%). The actual user-configurable live threshold is separate from
the retrospective 2% adverse-move diagnostic.

For each root, the detailed report records the first lower-frame same-direction
follow-up while adverse to the original close. A follow-up that the causal strategy
would deliver is marked `strategy`. A later finding after the strategy has already
stopped is marked `observation_only_after_stop`; it is not claimed as a delivered alert
or eligible strategy entry. Roots without a lower-frame follow-up are still retained.

Before-follow-up adverse excursion uses the original 4h P2 close as its anchor. After
follow-up MAE/MFE use the minute close observable at that alert. Each search window is
12 hours, and each post-follow-up window is another 12 hours. Recovery is a later minute
close back to the follow-up anchor after an adverse observation; first favorable close
is recorded separately. Extreme timestamps identify the containing minute's end, since
OHLC bars cannot establish exact intraminute sequencing. Maximum favorable movement
after recovery excludes the recovery candle's wicks to avoid inventing their order.

The 2% further adverse threshold is configurable research metadata, not a trade stop or
proof of permanent failure. Recovery and threshold can both occur in one case. If both
threshold crossing and favorable movement happen inside one candle, the report does
not infer which happened first.

Refresh detailed reports from the saved dataset without downloading again:

```sh
.venv/bin/python annual_near_backtest.py \
  --cache data/annual-near-cache --output data/near-one-year \
  --refresh-report --window-hours 12 --failure-pct 2
```

The hypothetical return comparison uses a common original-signal horizon of three
4h bars and 10 basis points total roundtrip costs. Entries and exits use traded minute
opens; this is separate from descriptive alert-time price-path anchors. Waiting entries
have a smaller executed sample, so compare matched roots and all-root cash-inclusive
returns rather than treating conditional entry means as strategy superiority. Neither
maximum favorable excursion nor a rebound proves a realizable profitable exit policy.
Volume observations require a separate held-out predictive test before changing filters.
