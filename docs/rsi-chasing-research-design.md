# Four-hour RSI chase research: volume, nested timeframes and profitability

Discussion and research design, 31 October 2026. Companion to the
[main chasing plan](rsi-chasing-backtest-plan.md), incorporating the follow-up voice
requirements. This is documentation only. The user has now supplied six CSV file paths
for Kraken NEAR/USD, including the added 15m timeframe. They have been audited and replayed offline. There are six eligible 4h roots, but no
complete strict minute execution windows; profitability and volume-success effects
remain unestablished. See [implementation notes](rsi-chasing-implementation.md).
The favorable-move chase stop is now a **user-editable personal Settings percentage**,
default **0.5%**, rather than a hard-coded constant. Each research run snapshots an
explicit value; “0.5% stop” examples below refer to the default, not a fixed requirement.

## 1. What the next backtest should answer

Use **4h original RSI divergences** as the primary population. The supplied TradingView
file set covers **4h, 1h, 30m, 15m, 5m and 1m**, subject to a data audit. Retain bullish and
bearish results separately. Answer four different questions rather than one combined score:

1. Does original signal-candle volume, particularly relative volume, predict subsequent
   favorable movement, adverse excursion or success under a defined outcome rule?
2. While price is moving against a 4h signal, how often does a new same-direction
   divergence appear on successively lower timeframes, and how long does it take?
3. Does waiting for those events improve entry outcomes enough to offset missed moves,
   additional delay and costs, compared with the original alert and simpler waiting rules?
4. Which limited, predeclared changes improve **held-out net performance**, and which
   merely look better in the sample used to discover them?

The live bot remains a **signal notifier**. It does not decide whether a user enters.
Backtests can define hypothetical entry/exit policies to answer profitability questions;
they cannot turn simulated fills into real I'm in clicks or authorize automatic trading.
The configurable favorable chase stop is a monitoring rule, not an assumed trade take-profit.

## 2. Small literature check and what it establishes

Sources checked on 31 October 2026. These inform test design, not a borrowed win rate.

- Blume, Easley and O'Hara develop an equilibrium model in which volume conveys
  information beyond price. This supports investigating volume as a feature; it does
  **not** show that a high-volume RSI divergence succeeds more often in these markets.
  [Market Statistics and Technical Analysis: The Role of Volume (1994)](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1994.tb04424.x).
- Lo, Mamaysky and Wang test algorithmically defined chart patterns against conditional
  return distributions. In their different, daily-stock setting, volume trends add little
  incremental information in most cases. The practical lesson is to test incremental
  predictive value instead of assuming more volume is always better. Their patterns
  and volume-trend definitions are not this repository's RSI(3)/BB candle-volume rules.
  [Foundations of Technical Analysis (2000), especially volume conditioning and results](https://www.mit.edu/~wangj/pap/LoMamayskyWang00.pdf).
- Bailey and coauthors examine backtest overfitting from strategy selection. This motivates
  logging every tried filter/ladder/threshold and evaluating frozen choices on unseen
  periods; it does not supply a profitability estimate for this strategy.
  [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

The targeted search did **not establish a transferable conditional probability** for this
exact sequence: 4h RSI(3)/BB divergence → adverse movement → 1h/30m/15m/5m/1m divergences
with this pivot/tolerance logic and the default 0.5% stop. That is a limit of this small review,
not a claim that no related research exists. Do not import success percentages from
other RSI periods, hidden-divergence rules, assets, horizons or discretionary examples.

**Inference to test:** successive events across these charts can occur because all bars
come from the same price path. They are not independent votes. A lower-timeframe event
may be common simply because there are many more lower-timeframe candles. Its occurrence
is different from its predictive value. The planned tests below measure both.
Recompute RSI on each timeframe; a 4h RSI divergence neither mathematically requires
nor rules out a later 1m divergence. The same bar-based pivot spacing also spans very
different elapsed times: the configured maximum of 60 bars is **240 hours on 4h bars
versus 60 minutes on 1m bars**, before session gaps. Keep parameters fixed for
the first comparison and label any timeframe-specific tuning as another experiment.

## 3. Current first-divergence rule

The latest user clarification replaces the descending ladder. After an original 4h
alert, evaluate each minute from **1m → 5m → 15m → 30m → 1h → 4h**. The first
newly available eligible same-direction divergence ends the search and generates one
final personal Chasing alert. A 5m, 15m or 1h finding stops chasing just as a 1m finding
does. No intermediate confirmations or prescribed sequence are required.

The current implementation gates divergence selection on adverse movement relative to
the original 4h close, uses fresh closed-minute prices, and independently checks the
user's favorable-stop percentage. Within a check, the lowest eligible timeframe wins;
within one timeframe use its earliest newly available event. Events already known at
the previous check are not replayed when price later turns adverse. The final card
remains eligible for I'm in, with no search running. Only that click starts opposite
monitoring, initially using 4h bars on the five-minute schedule.

For research compare **original entry**, **first follow-up entry**, fixed-delay and
price-only controls. Any former strict descending ladder is now a historical alternative
experiment, not the live strategy. Distinguish an event becoming available after the
original from pre-existing lower-frame confluence. Keep original-root denominators,
no-follow-up and favorably stopped roots, data gaps and incomplete execution paths.

The targeted literature review did not establish a reliable published success rate for
this exact first-divergence, low-to-high, RSI(3)/BB strategy. Adjacent RSI(14) and chart-
pattern studies use different rules. Empirical evaluation must therefore use the pinned
historical data and explicit entry/exit/cost assumptions, not borrowed percentages.

## 4. TradingView CSV intake plan

TradingView documents chart-data CSV export and notes that exported history depends on
what has been loaded in the chart. Verify actual file ranges and do not assume six
exports cover identical dates. [Official export instructions](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/).

User-provided file set (names recorded for intake; contents and coverage not yet audited):

| Timeframe | Source filename |
| --- | --- |
| 4h | `KRAKEN_NEARUSD, 240_f24a3.csv` |
| 1h | `KRAKEN_NEARUSD, 60_6ef34.csv` |
| 30m | `KRAKEN_NEARUSD, 30_a4865.csv` |
| 15m | `KRAKEN_NEARUSD, 15_86ddd.csv` |
| 5m | `KRAKEN_NEARUSD, 5_461e6.csv` |
| 1m | `KRAKEN_NEARUSD, 1_74922.csv` |

Intake checks for these files:

- One exact asset/feed per dataset, with 4h, 1h, 30m, 15m, 5m and 1m files, preferably covering
  the same continuous period. Keep long prehistory for indicators and pivot references
  on **every** timeframe plus follow-up after the final eligible original. Aim for at
  least 250 warm-up candles per timeframe where available; detector minimum is separate.
- Columns for time, open, high, low, close and **volume when the provider offers it**.
  Preserve original headers; map them explicitly after inspection. Include a manifest
  giving exchange-qualified symbol, feed/provider, volume units/type, timezone, session
  settings, regular/extended hours, chart type, adjustments and export date. Use standard
  time-based candles; synthetic Heikin-Ashi/Renko prices require separate experiments.
- Prefer unmodified raw CSVs; indicator columns may help parity checks, but recompute RSI
  and BB from OHLC with repository rules. TradingView default RSI(14) is not this RSI(3).
  Do not trust retrospective indicator markings as event availability timestamps.
- Validate open-time versus close-time semantics, timezone offsets/DST, strictly ordered
  nonoverlapping rows, numeric/nonnegative volume, duplicate/conflicting revisions,
  missing bars and exclusions of unfinished candles. Missing volume is not zero volume.
- Audit the common price-coverage period and the volume-coverage period separately. State
  eligible original counts before and after each exclusion. Missing 1m data cannot be
  repaired by inventing minute paths from 4h bars. Price-only research may proceed while
  volume-conditioned results remain unavailable.
- Cross-check aggregated 1m OHLCV against higher-frame exports using their exact session
  boundaries. Sum volume only when data units/feed/session match and coverage is complete;
  never sum an indicator plot as volume. Report mismatches; do not silently overwrite
  supplied bars or force UTC-midnight 4h boundaries onto exchange-aligned exports.

Volume may represent actual exchange trade quantity, tick activity or no usable data.
TradingView explicitly documents missing volume for some symbols and that tick volume
counts price updates. Results must identify which measure was tested; volume from a
different instrument is a separate proxy experiment. [Official volume limitations](https://www.tradingview.com/support/solutions/43000481397-the-volume-indicator-on-my-chart-looks-odd-or-displays-zero-or-n-a-values/).

**Repository gap:** `scanner.Candle` and candle caches hold OHLC without volume;
`scanner.read_csv`, Python `csv_backtests.parse` and JavaScript `parseCSV` do not retain
volume. The CSV upload paths accept only 1h/4h/daily/weekly/monthly, with 20,000-row/2 MB
limits. The new `chasing_research.py` offline importer implements the separate
30m/15m/5m/1m workflow with volume preservation, synchronized streams and file hashes.
Do not alter production candle schemas or upload limits just to conduct this first study.

## 5. Volume analysis with no future information

Interpret “volume of the closing candle” initially as **total volume during original
4h P2**, known at its close. It is not volume at the exact closing tick. Store P1 volume,
P2 volume and, for structurally confirmed variants, confirmation-bar volume separately.
If the user means trading activity near the end of the 4h candle, add a separately
declared feature such as final-five-minute volume / whole-4h volume, using the supplied
1m series only when it reconciles with that 4h interval.

Predeclare a small feature set:

| Feature | Definition at the event | Reason |
| --- | --- | --- |
| Original relative volume | `RVOL20 = V_P2 / mean(previous 20 completed same-frame volumes)`; denominator excludes P2 | Compare activity with its own recent history |
| Seasonal relative volume | P2 volume / past-only typical volume for the same session slot, with a fixed trailing history | Separate unusual activity from ordinary open/close patterns |
| Pivot-volume ratio | `V_P2 / V_P1`, undefined if denominator is zero/unavailable | Test whether volume strengthens or weakens at the second pivot |
| Child relative volume | Same past-only calculation on the selected chase timeframe | Test incremental value at that later decision, not at the original 4h alert |

Record raw volume for audit, but do not pool shares, contracts, coin quantities and tick
counts as one variable. Fit transformations and seasonal baselines from past data only.
Zero or insufficient denominator means unavailable; do not backfill it from future bars.
Do not use the eventual 4h total as a feature for a 1m event occurring before that 4h close.

First report counts and MAE/MFE/return distributions across **training-defined low,
middle and high RVOL bins**, separately by asset, direction and timeframe. Show continuous
associations too, such as rank correlation with MAE and net return, rather than seeking
only a favorable cutoff. For a binary success definition, report observed rate, counts,
95% intervals and lift versus the same baseline population.

Then assess incremental predictive value: a simple baseline model with past-only price,
volatility, trend, session and direction features versus the same model plus volume.
Use chronological evaluation, calibration/Brier score for success probabilities and
held-out net expectancy for an actionable filter. Report effect sizes and uncertainty;
a volume/volatility correlation, low p-value or higher in-sample hit rate is not proof
that adding a volume filter improves this strategy. Check dependence on asset/session,
market regime, long/short side and a few unusually active candles.

No live filter, rating or percentage-confidence display is authorized by the study.
If a later probability score is proposed, require an untouched calibration/evaluation
period and reliability plots. Sparse bins show “insufficient evidence,” not a precise
personalized chance of success. The existing excursion rating is not a success probability.
For planning scale only, the simple independent-binomial approximation needs roughly
385 observations for a worst-case 95% margin of about ±5 percentage points
(`1.96^2 * 0.25 / 0.05^2`, rounded up). Correlated roots and splitting by volume bin
reduce effective sample size, so this is not a universal minimum or a guarantee of
precision. Report actual root counts and dependence-aware intervals; a small asset
sample may support exploratory findings without a reliable percentage forecast.

## 6. Conditional lower-timeframe divergence study

For each eligible 4h original, measure the probability and time to the **first**
qualifying same-direction divergence before the favorable stop or research deadline.
Break out the selected event's timeframe, direction and relative-volume cohort; these
are mutually exclusive first-hit categories. Keep all original roots in the denominator.
A sequence-transition matrix is an optional comparison experiment only; it no longer
specifies the chase behavior.

Use two comparisons:

1. **Occurrence lift:** compare against matched times with similar asset, direction,
   session, volatility, adverse depth and window length but without the parent divergence.
   Select matches using only information available at that time. Preserve dependence
   with root/time-block resampling; do not shuffle individual minute candles and destroy
   the original RSI path. A high unconditional 1m event rate can make “confirmation” trivial.
2. **Outcome lift:** among the original roots, compare predeclared entry policies and
   controls under identical execution/cost/exit conventions. Also compare at the same
   elapsed decision time where feasible, so waiting itself is not credited to divergence.
   Report both original-root coverage and paired comparisons; child occurrence alone
   does not establish better MAE, higher net expectancy or a higher success rate.

Treat the run's configured favorable stop, first-divergence completion and chosen expiry as terminal/competing
events for the search. End-of-file is right censoring, not a no-child failure. Record
paused adverse-gate time separately. If estimating probability before a deadline, use
a declared survival/competing-risk method or fully observed windows and disclose the
excluded/censored counts; do not count all incomplete windows as completed failures.
For scientific follow-up also observe the fixed post-original horizon after search stops,
without emitting further notifications. This measures the opportunity cost of stopping.

Keep pre-existing lower-frame confluence in a separate table. Distinguish “happened
later,” “became available later,” and “was found later by a delayed poll.” Compare
provisional/confirmed definitions explicitly and group several P1 pairs for one P2.

## 7. Success, profitability and candidate improvements

There is no single honest “percentage of success” until the outcome is defined. Provide
three separately labeled results, applying the main plan's after-alert/fill baselines:

- **Signal/path success:** an illustrative preregistered definition is reaching +0.5%
  favorably before -0.5% adversely, within three original 4h bars, from the selected
  after-alert observed-price baseline. Both thresholds and horizon are **research
  proposals**, not approved trade exits. Report target-first, adverse-first, timeout,
  same-minute ambiguous ordering and missing-data cases separately. Keep original-close
  chase-stop success separate from this after-alert baseline.
  Changing the user's chase-stop setting must not silently change these research outcome
  thresholds or any hypothetical trade take-profit/stop-loss values.
- **Fixed-horizon hypothetical returns:** simulate entries at next eligible minute opens
  under original/first-chase/final-1m policies; exit at the main plan's fixed common-origin
  horizons and equal-exposure horizons. Include spread/slippage, commissions and any
  applicable borrow/funding. State that 1m OHLC lacks bid/ask and actual queue position.
- **Explicit trade-policy simulation:** compare a small declared exit set, such as first
  eligible opposite divergence on a fixed chosen timeframe with a ten-4h-bar timeout,
  and a separately declared stop/target policy if wanted. A candidate sensitivity is
  1×/2× the original, already-known 4h ATR(14), fixed at origin, with the same timeout.
  These are hypothetical risk/exit conventions for testing, not live recommendations.

For each fully specified policy report trade count, no-entry count, net win rate (net
P&L > 0), breakeven count, mean/median net return, average win/loss, net expectancy,
profit factor, MAE/MFE, time in exposure and tail losses. For a gross-payoff presentation,
`expectancy = p_win * mean_win - p_loss * mean_loss - mean_cost`; retain explicit
breakeven trades rather than forcing `p_loss = 1-p_win` when breakevens exist. State units
and position notional. A higher win rate can still lose money.

Use equal-notional, unlevered per-trade comparisons first. Report portfolio drawdown only
after declaring capital, overlap/position limits, sizing and realizable short exposure;
do not sum unlimited overlapping roots into a tradable equity curve. Gap exits and
same-minute stop/target ties need conservative executable pricing or ambiguity bounds.
For final-1m entry, record that the final alert completes the search before the simulated
fill; apply a declared fill eligibility/timeout rule without conflating this with a click.

Try only a small, versioned improvement set initially: volume filter, adverse-gate buffer,
strict versus skip/direct descent, provisional versus confirmed child, and bounded
stage waiting time. Keep RSI/BB/pivot rules frozen first. Compare additions one at a
time against no-volume/no-ladder baselines, including turnover, opportunity loss and
cost stress. Choose features on development data, reserve chronological validation and
untouched holdout with enough separation for overlapping lookback/outcome windows, and
report every trial. A useful improvement must survive out-of-sample costs and uncertainty;
if evidence is weak or negative, report that rather than propose a guaranteed upgrade.
For the limited preregistered tests, declare a multiple-comparison correction such as
Holm; label larger bin/regime searches exploratory and control false discoveries if
formal significance is reported. Reusing a holdout to revise a filter makes it development
data; reserve a fresh evaluation period before claiming validated improvement.

## 8. Uncovered scenarios for the written discussion

| Scenario | Proposed handling / decision still needed |
| --- | --- |
| Price moves adversely without ever forming the next divergence | Continue checking all configured frames; do not invent a divergence. No extra live expiry is imposed; research windows are explicit. |
| Price recovers above original close but stays below the configured stop | Decide pause/resume versus continued descent; a return notification alone does not mark entry. |
| Further adverse move after the first follow-up divergence | Search stays complete, regardless of the selected timeframe; no automatic restart. |
| A new 1m signal appears before larger-frame signals | It can end chasing immediately; no intermediate stages are required. |
| A child P2 occurs before the parent, but its structural confirmation is later | Choose availability-only versus post-parent-P2 rule and label it; keep old confluence separate. |
| Only opposite-direction lower-frame divergences appear before entry | Do not treat them as same-direction chase events or as proof the user entered. Decide whether a future conflict notice is useful. |
| first-divergence completion, configured stop and I'm in occur in one minute | Persist known ordering; serialize live actions; show OHLC ambiguity bounds in research. Do not fabricate intraminute sequence. |
| User clicks I'm in on final/old card after search completion | Define card eligibility and manual-entry access; never silently reject all entry reporting or reopen chasing. |
| No more monitoring after first divergence, but a user wants to enter much later | Decide final-card expiry and entry-price observation policy; a stale candle price is not a fill. |
| Original and chase volume give conflicting indications | Separate original versus child features and decision times; no ad hoc “best volume” selection. |
| One supplied timeframe has shorter history, a session gap or different feed | Restrict synchronized cohort, report lost roots and keep missing data distinct from no signal. |
| A 4h volume threshold is met only after an earlier child signal | That future 4h volume cannot filter the earlier child; use the last completed 4h volume or wait for close. |
| Many roots/users share one lower-frame divergence | Preserve root/user isolation and evidence deduplication; report correlated observations and notification fan-out. |
| Profitability changes across volume bins but sample size is small | Publish counts/intervals, avoid a live confidence claim, and seek additional holdout history. |

## 9. Concrete next outputs for the supplied files

1. File/coverage audit and normalized, volume-preserving dataset manifest, without runtime
   connections or external effects. Record first-hit priority, adverse gate, event timing, windows and exits.
2. Root/transition event ledger with pause/stop reasons and an audit timeline for selected
   examples, demonstrating no lookahead and independent simulated/user entry records.
3. Original-versus-chase MAE report, transition occurrence/lift tables, relative-volume
   bins/associations, success-definition tables and fully specified net return comparisons.
4. Chronological validation/holdout report with trial register, uncertainty, costs and
   suggested changes ranked by evidence; mark unsupported conclusions explicitly.

No implementation, CSV import, strategy optimization, Telegram setup, live messages,
orders, GitHub push or deployment is included in this documentation update.
