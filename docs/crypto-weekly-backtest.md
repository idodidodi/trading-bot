# Crypto weekly RSI research runner

Local research only; no scanner process, database, orders or Telegram messages.
Run from this worktree using the existing project Python environment:

```sh
python -m unittest test_crypto_weekly_backtest -v
python research/crypto_weekly_backtest.py --cache /private/tmp/crypto-rsi-data --output /private/tmp/crypto-rsi-pilot
python research/crypto_weekly_backtest.py --annual --cache /private/tmp/crypto-rsi-data --output /private/tmp/crypto-rsi-annual
```

The annual study is 2025-11-01 inclusive through 2026-11-01 exclusive; weekly
Sunday dates inside that window determine the 52 closing weeks. The pilot uses
2025-11-30, 2026-03-08, 2026-03-15 and 2026-04-05. These were fixed before outcomes.
No parameter optimization or favorable-outcome selection is performed.

## Data and selection

All candles come from Binance **spot USDT pairs**. No mixed venues. Public REST
`/api/v3/exchangeInfo` captures the current active spot USDT pair universe. Known
stablecoin bases and leveraged-token suffixes are excluded. By default, **each
historical week scans all pairs in that captured universe anew**. Eligibility
requires the trailing 250 completed weekly candles to be contiguous at that
week's period start. Newly history-eligible pairs can join subsequent weeks.
Every eligible pair is replayed causally; qualifying weekly divergence parents
and their lower-frame entries are selected by the existing strategy, not by
random sampling or future returns. Direction caps and deterministic symbol order
remain unchanged. No candidates or admission state carry across weeks.

`results.json` records each week's eligibility checks, errors, admitted symbols
and directions (`weekly` timeframe), closed matches with lower timeframe and
entry time, and explicit no-match status. Incomplete scans are labeled
`incomplete`, so missing data is not presented as a clean no-match result.
The HTML report includes these weekly records, including empty weeks.

The universe snapshot contains currently active pairs, not the historical full
market universe: delisted pairs are absent (survivorship bias). Historical trading
status is not reconstructed; available candle history is the eligibility proxy.
Historical liquidity, spreads and borrow availability are not selection inputs.
Spot short results are hypothetical directional returns.

`--sampled-pilot` explicitly reproduces the legacy seed-20261127 six-coin cohort;
it cannot be combined with `--annual`. The original four-week/six-coin report is
an older bounded pilot, not a full-universe or annual result. The new full-universe
annual mode has not yet been run. The fixed four pilot dates remain available to
bound the time period while scanning all eligible pairs by default.

Public `/api/v3/klines` supplies weekly and daily candles. Official monthly
archives supply 15m warmup and 1m replay/outcome candles; each archive is verified
against its published SHA256 `.CHECKSUM`. HTTP response cache files and SHA256
receipts preserve exact input identity. Archive timestamps from 2025 are
microseconds; REST defaults to milliseconds. Both normalize to UTC milliseconds
and exclusive candle ends. No API token or account is needed.

Primary source: https://github.com/binance/binance-public-data/blob/master/README.md
and https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md .

## Research semantics

Signal/watch exceptions are documented in `TELEGRAM_ALERT_LOGIC.md`; this file
covers research data and outputs, not an alternative live-alert specification.
The crypto week is Monday 00:00 UTC through the following Monday 00:00 UTC.
Default research window is Sunday 21:00 UTC through Tuesday 00:00 UTC, with
`--after-hours 0|24|48`. The 24-hour continuation was explicitly confirmed by the user. Crypto has no regular-session/extended-hours split.

Native 15m history warms up lower frames. UTC-aligned aggregation accepts only
complete buckets; the trailing 250 candles at an entry must be contiguous.
Weekly snapshots require six complete daily candles and every visible Sunday
minute. Completed historical weekly bars are followed by that as-of snapshot,
never by the final future weekly candle. The parent freezes at weekly close.

Outcome windows are 1h, 4h, 1d, 3d and 5d **elapsed time**, since trading is 24/7.
Entry is lower candle close. Entry candle extrema are excluded. Every subsequent
minute must be present through the horizon endpoint; otherwise the outcome is
incomplete and excluded from averages. Max gain and max DD mean maximum favorable
and adverse excursion from entry, clamped at zero, not a peak-to-trough equity
curve drawdown. Extrema timestamps identify the minute bar, not the exact tick.
Returns and prices are in USDT (not assumed USD). Delays, fees, spreads, slippage
and borrow costs are zero, per request. No present-day/current-price metric.
Later higher-frame invalidation is preserved without deleting earlier entries.
No exit strategy was specified, so fixed-horizon returns and excursions evaluate
signal behavior; they are not realized strategy P&L or a compounded portfolio.

`report.html` is standalone; `results.json` includes all signals, per-timeframe
summaries, first chronological entries per coin/week/direction (lowest timeframe
only breaks simultaneous ties), parent admission/invalidation audit, data
exclusions, seed, eligibility and receipts. Overlapping outcomes are dependent;
the selected summary reduces duplication but does not represent independent trials.
Annual mode is available; validate the pilot before spending the annual download
and replay budget. Cached responses can be reused across both modes.
