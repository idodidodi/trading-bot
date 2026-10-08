---
name: rsi-divergence
description: Scan a user-supplied list of assets and timeframes for bullish and bearish RSI divergence confirmed by Bollinger Band touches. Use for multi-market divergence scans, including forex, indices, crypto, and stocks.
---

# RSI divergence and Bollinger Band scanner

Accept an asset list and a timeframe list. Scan every asset/timeframe combination independently. Example input: `assets: [EURUSD, DAX, dollar index, BTCUSD, NEARUSD, NVDA]; timeframes: [monthly, weekly, daily, 4h]`.

Report signals only, following the user's preference. Do not include entry, stop-loss, or target proposals, place orders, or connect execution accounts.

## Inputs and defaults

- Assets: required; resolve each name to an exact instrument, exchange, and data-provider symbol.
- Timeframes: required; accept monthly/1mo, weekly/1w, daily/1d, and 4h/4 hours. `1mo` is a calendar month, not 30 days. Never interpret monthly as one minute.
- RSI: 3 periods, Wilder smoothing, close prices.
- Bollinger Bands: 20-period simple moving average of closes, plus/minus 2 population standard deviations (`ddof=0`).
- Divergence price source: close.
- Pivot confirmation: 2 candles to the left and 1 to the right.
- Pivot spacing: 5–60 candles inclusive; compare consecutive confirmed pivots of the same type. Do not search older pivots to cherry-pick a match.
- History: aim for at least 250 closed candles per combination. Report limited history and exclude indicator warm-up candles from signal comparisons.
- Recent signals: confirmation within the last 10 closed candles. Allow user overrides for these parameters and disclose the actual values.

## Market data

Use the user's independent local platform as the signal engine. Fetch OHLC candles from the explicitly configured market-data provider, or use timestamped candle files. Calculate indicators and detect divergence locally; do not depend on TradingView charts, Pine scripts, or alerts. Check provider coverage and entitlements before claiming live support for an instrument. Do not silently switch feeds or substitute instruments. If no authorized provider or candle files are available, mark affected combinations as unscanned. Never invent prices or signals. Never request API-key values, passwords, or session cookies in chat; configure credentials locally.

Resolve ambiguous names before declaring matches. DAX cash index, futures, and CFDs are different instruments; the dollar index is not interchangeable with a dollar ETF. For crypto, disclose the venue and quote currency; do not silently substitute USDT for USD. If a suitable exact instrument is unavailable, explain the proposed substitute and get the user's choice. Continue scanning unambiguous assets independently.

Obtain timestamped open, high, low, close data with source timezone and session information. Sort chronologically, reject duplicate timestamps and malformed OHLC rows, and exclude unfinished candles. State the last completed candle and any delayed data. Treat missing scheduled candles as a data-quality issue; do not interpolate prices. Exchange closures are not missing data.

Prefer native timeframe bars. If resampling, use actual calendar months, the provider's trading-week/session conventions, and explicit 4-hour boundaries. Aggregate open=first, high=max, low=min, close=last; exclude incomplete buckets. Report timezone and session alignment. Do not manufacture 4-hour candles from daily data. Use a consistent stock split-adjustment basis for all OHLC fields and disclose it.

## Calculate indicators

For RSI, compute successive close changes; gain=max(change,0), loss=max(-change,0). With period `n` (default 3), seed average gain/loss with the arithmetic mean of the first `n` changes, then use Wilder recursion: `average = (previous_average * (n - 1) + current_value) / n`. Calculate `RSI = 100 - 100 / (1 + average_gain / average_loss)`. Use 100 when only loss is zero, 0 when only gain is zero, and 50 when both are zero. Keep initial unavailable values missing.

For each candle with 20 closes available, calculate the Bollinger midline and population standard deviation over those closes, including that candle. Upper/lower bands are midline plus/minus twice that deviation. Preserve numeric precision for comparisons and round only display values.

## Detect confirmed signals

A pivot low is a candle whose close is strictly below the closes of the preceding two candles and the following one candle. A pivot high is a candle whose close is strictly above their closes. Equal values do not form a pivot. A pivot becomes known only at the close of the first candle to its right; do not backdate confirmation to the pivot candle.

For consecutive confirmed pivot lows `P1` and `P2`, a **bullish divergence / long bias** requires all of:

1. Pivot spacing satisfies the configured range.
2. `close[P2] < close[P1]` (price makes a lower closing low).
3. `RSI[P2] > RSI[P1]` (RSI at those same price pivots makes a higher low).
4. `low[P2] <= lower_band[P2]` (the second pivot candle touches or crosses its own lower Bollinger Band).
5. Both pivots have valid RSI and band values.

For consecutive confirmed pivot highs `P1` and `P2`, a **bearish divergence / short bias** requires all of:

1. Pivot spacing satisfies the configured range.
2. `close[P2] > close[P1]` (price makes a higher closing high).
3. `RSI[P2] < RSI[P1]` (RSI at those same price pivots makes a lower high).
4. `high[P2] >= upper_band[P2]` (the second pivot candle touches or crosses its own upper Bollinger Band).
5. Both pivots have valid RSI and band values.

The band touch is required at the second pivot; the first pivot need not touch a band. Wicks count as touches. RSI need not exceed 70 or fall below 30 unless the user adds that filter. Hidden divergence is excluded by default. Do not compare unrelated RSI extrema or use a band from a different candle.

Label not-yet-confirmed patterns as provisional only if the user requests them, separate from confirmed results. A recent confirmed pattern is historical evidence, not a guarantee it remains actionable. Report whether any subsequent closed candle breached the second pivot's low (bullish) or high (bearish); call this a structural breach, not a tested trading invalidation rule.

## Report

State the scan time, data source, instrument mapping, parameters, and closed-candle cutoff. Provide a compact coverage table with one row for every requested asset/timeframe combination: confirmed bullish, confirmed bearish, no qualifying signal, insufficient history, or unavailable data. Missing data must never be reported as no signal.

For each recent confirmed signal, provide:

- Exact symbol/venue and timeframe, direction, and source.
- Both pivot timestamps and prices, and RSI values at each pivot.
- Second pivot's relevant band value and the touch inequality.
- Confirmation timestamp, age in closed candles, and any subsequent structural breach.

If both directions occurred within the recent window, include both. Deduplicate by instrument, timeframe, direction, and pivot pair. Report older signals only when requested. Summarize multi-timeframe agreement and conflict without treating correlated signals as independent evidence or inventing a confidence percentage. End with missing coverage or data requirements, if any.

## Working in this repository

Cloud tasks already run in isolated environments. Use the existing checkout; do not create a Git worktree unless the user explicitly requests one. Follow `README.md` for the independent platform: `scanner.py` fetches/reads candles, calculates indicators, and detects signals using numeric defaults from this file; `platform_app.py` runs the scanner, dashboard, and Telegram delivery. Configure assets and timeframes in `scanner.json` and restart after configuration or skill changes. TradingView files are retained only as an optional legacy integration. Do not claim a live scan succeeded without retrieving data and evaluating the rules above.
