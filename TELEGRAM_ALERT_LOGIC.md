# Telegram signal alert logic

Last reviewed: 2026-11-06 (automatic asset setup and Colmex scanner integration; other sections retain their prior review scope).

This is the single reference for the repository's Telegram signal-alert behavior. It describes local source code, not a verification of deployed production settings. Keep it updated in the same change whenever the behavior below changes, as required by `AGENTS.md`.

## Flow

Enabled asset/timeframe/strategy → valid closed-candle data → qualifying new finding → durable queue → delivery controls and duplicate checks → market/strategy destination → eligible personal destinations.

An alert is a signal notification, not proof that an order was placed.

## 1. RSI divergence

Parameters: RSI 3 with Wilder smoothing; Bollinger Bands use a 20-close SMA and ±2 population standard deviations, including the signal candle. Divergence prices are candle closes. RSI tolerance is 1 point, inclusive.

For each candidate second pivot P2, compare all eligible, already confirmed first pivots P1 of the same type, 2–60 candles apart. Pivots use strict close extrema with 2 candles to the left and 1 to the right for structural confirmation. At alert time P2 only needs its left-side test; P1 must already be confirmed without looking ahead.

| Condition | Bullish | Bearish |
| --- | --- | --- |
| Price | Close[P2] < Close[P1] | Close[P2] > Close[P1] |
| RSI | RSI[P2] ≥ RSI[P1] − 1 | RSI[P2] ≤ RSI[P1] + 1 |
| Band touch | Low[P2] ≤ lower band[P2] | High[P2] ≥ upper band[P2] |

Both pivots need valid RSI and band values. Only P2 needs a band touch. Wicks count; no RSI 30/70 threshold or band-slope filter applies.

The alert becomes eligible at P2's candle close, marked `provisional` with `alert_timing=pivot_close`. Actual delivery follows scanning and queue processing. The later structurally confirmed finding is retained without a second Telegram alert. Legacy review-slot alerts and non-provisional pending RSI alerts are cancelled by the delivery worker.

Multiple P1 references remain separate findings, but Telegram groups them by symbol, timeframe, direction, P2, and provisional/confirmed stage. Already sent or suppressed groups are not sent again.

Sources: `scanner.py` (`detect`, `scan_once`), `platform_app.py` (`rsi_divergence_matches`, `Store.insert`, `signal_alert_identity`, delivery worker), `skills/rsi-divergence/SKILL.md` (numeric parameters).

## 2. EMA Pin Bars

Uses close-based EMA 8 and EMA 21, including the signal close; seeded from the first close, excluding the first 21 candles from detection.

For every completed candle:

```text
body = abs(open - close)
lower_wick = min(open, close) - low
upper_wick = high - max(open, close)
```

Bullish requires all of:

- Lower wick > 2 × body and lower wick > upper wick.
- Upper wick ≤ 0.66 × lower wick.
- Low ≤ fast EMA OR low ≤ slow EMA.

The bullish fast-above-slow and close-above-slow conditions are deliberately disabled in the implementation. A bullish pin bar can qualify below the slow EMA or during a downtrend.

Bearish requires all of:

- Upper wick > 2 × body and upper wick > lower wick.
- Lower wick ≤ 0.66 × upper wick.
- Fast EMA < slow EMA and close < slow EMA.
- High ≥ fast EMA OR high ≥ slow EMA.

Body color is irrelevant. Alerts become eligible after the pin-bar candle closes.

Sources: `pin_bars.py`, `candle_strategy.py`, `skills/pin-bars/SKILL.md` (numeric parameters).

## 3. Bollinger Band Pin Bars

Uses Bollinger Bands 20 / 2, calculated as in RSI above, and the same strict 2× body and inclusive 0.66 opposite-wick limits.

- Bullish: dominant lower wick with low ≤ lower band.
- Bearish: dominant upper wick with high ≥ upper band.
- No EMA or body-color filter.
- Alerts become eligible after the pin-bar candle closes.

Sources: `bb_pin_bars.py`, `candle_strategy.py`.

## 4. RSI + PB

Start with the provisional RSI divergence described above. Group matching references by P2 and direction, then examine only the immediately next expected trading candle (respecting the strategy's session/calendar calculation).

That candle must be a pin bar, using the 2× body and 0.66 opposite-wick limits. Both same-direction and opposite-direction variants qualify. No EMA or additional band-touch filter is applied to that following pin bar.

- Same-direction variant: bullish divergence followed by a bullish pin bar, or bearish divergence followed by a bearish pin bar.
- Existing opposite-direction variant: bullish divergence followed by a bearish pin bar, or bearish divergence followed by a bullish pin bar.
- Both use the existing `rsi-pb` strategy, queue prefix, and market-specific RSI + PB group. Both fan out to all registered users with a configured, enabled RSI + PB route; existing opt-outs remain effective.
- Saved signals carry `pinbar_relation` (`same` or `opposite`); Telegram and dashboard text identify the variant. Historical records without that field are displayed using the two directions.
- The signal direction remains the divergence direction; pin-bar direction is recorded separately.
- Send only after the following candle closes and qualifies.
- Do not search later candles if the expected next candle fails.
- Missing expected data leaves the setup pending; a detected gap is reported as unavailable.

The expanded qualification changes the rules version. Existing version-based scanner behavior establishes a new baseline and retires old-version pending setups rather than replaying historical alerts. Hosted CSV replay implements both variants too.

Optional follow-on Bybit demo execution is implemented as described below. It is separate from Telegram delivery eligibility and is not activated by merely deploying the signal detector.

Source: `rsi_pb.py` (`setups`, `next_start`, `qualify`, `scan`).

## Daily SMA crosses (Golden / Death cross)

- Strategy `ma-cross`: close-based SMA50 and SMA200, evaluated only on completed **daily** candles. Golden/bullish: previous SMA50 ≤ SMA200 and current SMA50 > SMA200. Death/bearish: previous SMA50 ≥ SMA200 and current SMA50 < SMA200. Equality on the current candle does not trigger. Invalid averages do not qualify.
- Require 201 valid closed candles to compare consecutive averages. No price-position, volume, or slope filter. A signal reports both averages, close, source and daily close time; it does not place an order.
- Enable once for all configured assets, including disabled assets without enabling the assets themselves; preserve subsequent strategy opt-outs. New and dynamically selected assets default to this strategy. All existing asset exclusions, market-hours/calendar rules, stock minimum price, provider validation and feed eligibility remain effective.
- Daily scanning is independent of the asset's other strategy timeframes: append a daily check when needed, running only `ma-cross` on that additional timeframe. Do not turn on other strategies for daily or run crosses on intraday/weekly/monthly bars. Missing daily feeds or fewer than 201 candles are reported as unavailable/insufficient history.
- The first eligible scan establishes a baseline without historical alerts. Subsequent cross events use the shared closed-candle strategy persistence and durable queue. Repeated scans/restarts do not enqueue an already recorded event. Catch-up findings are retained without individual Telegram notifications. Persistent below/above status is not a repeating alert; another qualifying crossing can alert.
- Queue prefix `ma-cross:`; independent `ma_cross_state` and `ma_cross_signals`. All markets use the single `other-signals` route (UI label **Other Signals**), separate from the `other.*` market categories. An empty/unavailable destination retries instead of falling back to General. Existing delivery deduplication, sound slots, retries and dismissal behavior apply.
- Personal copies require an explicitly registered `other-signals` personal route and honor that user's preference. Adding the shared destination does not automatically register personal destinations.
- `cloud/ma-cross.sql` upgrades hosted validation, route preferences, daily signal API, runtime tables and existing asset defaults. Apply before the matching worker/UI deployment. The installation's destination is held in routing settings, never hardcoded in application source.
- Local verification on 2026-11-06: Telegram `getChat` confirmed the exported **Other Signals** group identity, and a read-only check found no existing reference in the live shared routes and one personal-route reference. The prepared routing update clears that old personal assignment while retaining unrelated settings. All 119 targeted backend tests passed in disposable Postgres, including the routing cleanup and migration reruns; all 44 UI tests and the static dashboard build passed. No test messages were sent. These implementation/configuration changes are not yet published to production.

Sources: `ma_cross.py`, `scanner.py` (`scan_once`), `candle_strategy.py`, `managed_assets.py` (`ensure`, `migrate_ma_cross`), `telegram_routing.py`, `telegram_personal.py`, `cloud/ma-cross.sql`, `scripts/configure-other-signals.py`, `web/app.js`.

## 5. Newness, baseline, and data eligibility

- Scanning depends on the configured assets, timeframes, enabled strategies, selection windows, and scanner schedule.
- Use valid completed candles with enough indicator history. Enforced freshness limits, missing candles in continuous feeds, and polling gaps beyond available history can block processing.
- RSI and standalone pin-bar first scans normally establish a latest-closed-candle baseline without flooding Telegram with historical matches.
- Weekly-screen-selected RSI assets can include the latest closed candle on their first scan, bounded by selection timing.
- RSI + PB can register the latest divergence on its first scan and wait for its next candle.
- Subsequent scans process eligible findings newer than their saved cutoff. Selection start times can further restrict eligibility.
- Catch-up findings are saved without individual strategy alerts: RSI uses `summarized`; pin-bar strategies skip notification queue insertion. This does not prohibit separate operational catch-up summaries.
- Backtests do not constitute live alert delivery.

Sources: `scanner.py`, `candle_strategy.py`, `rsi_pb.py`.

### Automatic asset setup

Local UI behavior (not verified in production): `web/app.js` (`openAssetSetup`, `automaticAsset`, `assetFrameAvailable`) configures one data connection after the user identifies the instrument. Common currency pairs, BTC/ETH/XBT pairs with explicit quote currencies, and exact supported index/commodity/futures symbols can preselect a category; unknown symbols require a category selection. US-listed stocks/ETFs default to Alpaca, crypto pairs to Kraken, and forex pairs to OANDA. Other instruments require an exact selection from the supported Yahoo/Tiingo mappings. No provider is chosen by timeframe in the normal editor.

New crypto assets default to weekly/daily/4h; monthly is unavailable on Kraken. Other new assets use supported monthly/weekly/daily/4h frames. Lower intervals remain opt-in and restricted by existing provider support. Unsupported intervals are disabled for new selections. Existing asset mappings, timeframe overrides, TradingView mappings, strategy choices and enabled state are preserved on ordinary edits. Existing CSV drafts can enter automatic setup explicitly; no bulk migration changes their venues.

Saving records the chosen instrument mapping (`feed_confirmed=true` is configuration confirmation, not evidence of successful data access). New assets default to scan enabled, with a visible toggle; existing drafts retain their enabled state during setup. Feed access, exact response identity and candle eligibility are checked by the normal scanner/provider code. The UI shows awaiting scanner data check until coverage is available and the configuration revision has been applied; it never treats a save alone as a successful connection test. The add flow uses revision-checked draft creation followed by save, so a failed final save leaves a disabled draft. No test alerts or extra scanner are started by setup. Existing first-scan baselines and alert rules above still apply.

Sources: `web/app.js` (`openAssetSetup`, `automaticAsset`, `assetDataStatus`, `loadAssets`), `managed_assets.py` (`change`, `validate_asset`, `for_timeframe`), `yahoo_feed.py` (`INSTRUMENTS`), `tiingo_feed.py` (`validate_feed`), provider fetch functions, `scanner.py`.

### Colmex scanner integration

Local implementation (not yet production-deployed): `colmex_feed.py` connects the
scanner to the separate `colmex-mt4` package (`mt4/reader.py`). Colmex is a dedicated
primary provider with exact broker symbol identity, including `#`, and exchange
`Colmex-MT4`; no cross-provider fallback or assumed TradingView match is allowed.
The operator's 2026-11-06 UTC confirmation is recorded in `mt4/timezone-policy.json`.
Each read checks terminal/catalogue freshness, connection/configuration state, active
symbol and subscription revision, valid completed M1 OHLC, and live quote time.
Scanner integration supports 1h/4h/daily UTC aggregation. H4 starts at 00/04/08/12/16/20 UTC.
Only complete buckets in the most recent contiguous suffix are eligible. Missing
minutes or whole buckets reset that suffix; no market-break calendar is inferred.
The existing strategy minimum-history and first-scan baseline rules still apply.
Current SPX history only produced two eligible H4 bars in the read-only smoke test;
this does not meet strategy history requirements. Unknown gaps remain a readiness
blocker. Unavailable Colmex reads retry in 30 seconds. Other provider schedules remain.

`cloud/colmex.sql` exposes authenticated `/api/colmex` requests to installation
members (including viewers), with owner identity taken from auth.uid(), per-user
revision checks, 10 requested exact broker-classified catalogue forex pairs per user, and a 50-symbol
shared collection capacity. Removal works offline. No arbitrary owner IDs or paths
are accepted. `colmex_runtime.py` reconciles active members' requests plus enabled
configured assets under one exporter owner named `scanner`, preserving the existing
portfolio and other exporter owners. Revoked members cease contributing demand;
an explicitly disabled configured Colmex symbol takes precedence over requests.
Requested pairs are scanned as shared 4H forex assets using RSI, Pin Bars and RSI+PB.
Existing alert routing and personal category preferences apply: requesting a pair
is not Telegram enrollment and does not make its alerts exclusive to the requester.
Removing a request stops its contribution, not other users' or portfolio demand.
No Telegram chat command is introduced; subscribers use the authenticated Assets UI.

Verified locally: exporter live reload; reader/time-mapping/gap tests; authenticated
Postgres ownership, revision, offline removal and capacity tests; synthetic scanner
baseline and signal persistence; UI request controls. Tests use disposable data,
not production signals or Telegram delivery. Deployment requires the SQL migration,
package and worker configuration, and the hosted UI together. No production asset
selection or alert route has been switched by this implementation.


## 6. Queue and delivery controls

RSI uses the `signals` queue. EMA Pin Bars, BB Pin Bars, RSI + PB, and daily SMA crosses use `notification_queue`. The worker processes due notifications before RSI entries. Rows must be pending, due for retry, and not subject to a migration hold.

Dry-run marks processed queue entries `dry-run` without sending; those entries are not automatically replayed merely by turning delivery on later. Development Telegram settings override the fallback dry-run flag. Real delivery requires a valid bot token, destination, and active Postgres worker ownership.

Sources: `platform_app.py` (delivery worker), `local_settings.py`, `telegram_delivery.py`, `postgres_store.py`.

## 7. Destination routing and personal copies

Market groups are forex, crypto, stocks, and other. Stock/equity/ETF aliases normalize to stocks. Market-specific strategy routes are RSI, Pin Bars, and RSI + PB; EMA and BB Pin Bars share the Pin Bars route. Daily SMA crosses use the independent Other Signals route for all markets.

For the market-specific routes, choose the configured market/strategy destination, otherwise General (configured General route, then the fallback chat). Persist the selected destination for the event so retries use the saved route. A recognized destination-unavailable error can switch delivery to General; arbitrary send failures do not trigger this fallback.

After shared delivery, attempt personal copies for registered recipients with the matching route and enabled preference. Personal copies have no fallback to someone else's General group. Eligibility and destination are persisted per user/event; a suppressed personal event stays suppressed after re-enabling. Where the supplied payload has detection/confirmation timestamps, findings predating enrollment are excluded. Notification-queue strategy deliveries currently pass text/event metadata without that timestamp payload, so that enrollment-time check does not apply identically to them.

Sources: `telegram_routing.py`, `telegram_personal.py`.

## 8. Duplicate prevention, sound, and retries

- Finding IDs prevent duplicate queue insertion. RSI additionally groups different P1 references for the same P2 alert.
- Delivery deduplication is per bot/chat destination and event ID. A completed delivery is suppressed on subsequent attempts.
- Delivery reservations last 90 seconds and prevent simultaneous sends for the same event/destination.
- Signal alerts can request sound overnight. At most one delivery per destination per calendar minute obtains the sound slot; others are sent silently. A failed attempt can consume that slot. Telegram/client settings determine whether sound is actually heard.
- Non-signal notifications use the separate quiet-hours policy.
- Failed queue attempts use exponential backoff beginning at 2 seconds and capped at 300 seconds; active delivery reservations can postpone actual resend. There is no fixed retry-count cutoff in this worker.
- Personal-delivery failures can retry the event while per-destination deduplication protects successful recipients.
- Delivery is not exactly-once: a timeout/crash after Telegram accepts a message but before acceptance is durably recorded can cause a duplicate.

Sources: `platform_app.py` (delivery worker), `telegram_delivery.py`, `telegram_routing.py`, `telegram_personal.py`.

## Done/Undo dismissal and recovery

Personal alerts and shared alerts with group dismissal enabled use a server-side three-second grace period. Leaving a Telegram chat/folder does not cancel it. Pending deadlines survive worker restarts; dry-run pauses external effects. Personal callbacks require the enrolled user; shared-group callbacks require the matching message/chat.

After the grace period, Undo only acknowledges that the window ended; the listener handles deletion separately. Telegram's explicit `message can't be deleted` rejection (including its 48-hour deletion restriction) replaces the countdown with a completed/manual-delete notice and removes Undo. Already-missing messages count as deleted. Other API failures, including a failed fallback edit, keep the item pending with a persisted 30-second retry deadline. One failed item does not stop other pending deletions or callback polling. Database/ownership failures still propagate. Recovery applies to existing persisted pending messages when the updated worker runs; messages without recoverable database state are outside this repair. Production recovery verified on 2026-11-02 for source commit `922b197c9372dd72091e86766970875f79a00864`: the existing Linux worker resolved all three pending records (two completed notices, one deletion), with zero remaining pending records and no new listener errors. This verification applies to the deployed dismissal modules, not every other local rule in this document.

Sources: `telegram_dismissal.py`, `telegram_personal.py` (`process`, `next_deadline`), `telegram_checklist.py` (`api`, `_worker`); [Telegram deletion limits](https://core.telegram.org/bots/api#deletemessage).

## 9. RSI + PB confirmation chasing and Bybit demo entries

User-confirmed entry rules:

- Scope is strictly 4h and 1h: a 4h signal watches 4h and 1h confirmations; a 1h signal watches 1h only (the requested 1h + 1h is one watch, not two entries). Either available timeframe independently suffices. No other signal or confirmation timeframe participates in demo chasing.
- For divergence → same-direction PB, a bullish confirmation has `close > open`; bearish has `close < open`. Doji candles do not confirm this variant.
- For divergence → inverted PB → same-direction PB, the final PB itself confirms and triggers entry on close. It must be the immediately next candle on a permitted confirmation timeframe. It uses the existing PB shape rule (dominant wick > 2× body; other wick ≤ 66% of dominant wick), in the divergence direction. Body color is irrelevant to this pin-bar shape; no additional confirmation candle is required.
- Use only completed candles beginning at or after the RSI + PB pin bar's close. The pin bar itself cannot trigger entry.
- Enter in the divergence direction on Bybit demo, targeting position notional of 25% of current unified-account equity.
- TP and SL each target 1% from actual entry: long TP = entry × 1.01, SL = entry × 0.99; short TP = entry × 0.99, SL = entry × 1.01.

Implementation choices, pending any further user adjustment:

- Both requested execution variants are implemented: **divergence → same-direction PB** (`same`) and **divergence → inverted PB → same-direction PB** (`opposite_same`). The default/example configuration enables both variants when execution itself is enabled. A plain divergence → inverted PB cannot trigger an entry; the previous `opposite` execution setting is rejected.
- Telegram emits the existing RSI + PB setup alert at the first PB's close and fans it out through the existing user routes. For an inverted PB setup, execution then requires the final same-direction PB. This confirmation does not generate a duplicate setup alert.
- For the chained variant, check only the immediately next full Bybit candle on each permitted timeframe, starting at the first native UTC boundary at or after the inverted PB closes. If the 1h candle fails, a permitted 4h candle may still qualify at its close. If both immediate candles fail (or the sole 1h candle fails for a 1h signal), cancel the setup. Missing expected data never authorizes substituting a later candle.
- Pending waits outside 4h/1h are cancelled. Old 1h waits drop the former 30m watch. Already-submitted positions continue reconciliation and protection regardless of the new timeframe restriction.
- Confirmation uses the explicitly mapped Bybit contract's public candles, which can differ from the original scanner feed. Private trading requests use only `https://api-demo.bybit.com`; public price data uses `https://api.bybit.com` without credentials.
- The earliest qualifying closed candle wins. A confirmation first processed more than five minutes after its close expires without entry; this prevents stale restart/backlog entries. The direct variant otherwise keeps watching until confirmation or configuration cancellation; the chained variant has only the immediate-candle opportunities described above.
- Only newly inserted, non-catch-up findings enroll. Enabling execution does not replay old findings. Unmapped assets retain normal Telegram behavior but do not trade.
- One durable order ID per finding prevents two entries from two confirming timeframes. All users' Telegram copies share this one demo entry; they do not place separate trades.

Execution and recovery:

- Use the existing paper worker, not an additional scanner or Telegram listener. Worker ownership and the database external-effects gate apply to broker writes.
- Explicit asset-to-contract mappings are required. Discover current instrument limits before each entry; support is restricted to trading USDT linear perpetuals in one-way position mode.
- Size from fresh account equity and the current ask (buy) or bid (sell), rounding quantity down to the exchange step. Reject quantities outside exchange limits or below minimum notional. Require available balance of at least the 25% equity allocation. No leverage or margin-mode changes are made. Market fills can differ from the quote, so actual notional can differ from the 25% target.
- Existing positions or open orders on that contract block a new entry; do not merge with or reverse them. Unresolved submissions/protection or review states block additional new entries across the executor.
- Entry orders carry provisional full-position TP/SL based on the quote. After a terminal order response confirms actual filled quantity and average entry, reset TP/SL around that fill price, rounded to the nearest exchange price tick. Verify position size, direction, and TP/SL from the position snapshot before marking the trade active.
- Terminal partial fills are protected for the actual filled quantity. A zero-fill terminal order is rejected. A position changed externally enters review rather than having its protection overwritten.
- Persist `submitting` before calling the broker. A timeout, crash, or uncertain acceptance is reconciled by the original order ID; never blindly resubmit. Missing order history is not evidence of rejection. A fill not yet visible in positions remains unresolved.
- Failed protection remains in reconciliation and blocks new entries; full-position stop updates are read back before retrying. A previously observed position with a fresh zero-size snapshot is marked closed. No automatic re-entry occurs.
- State is stored in `bot_runtime.bybit_chases`. Credential changes send existing records to review to avoid acting on a different account.
- Setting `enabled: false` cancels waiting entries but continues reconciliation of submitted entries while the configuration file and same credentials remain available. Do not remove the configuration or credentials while positions require monitoring.
- Execution configuration/schema failures during enrollment are logged without rolling back the Telegram alert.

Activation and verified limits (2026-11-02):

1. Provision `cloud/bybit-chase.sql` after the worker role exists, following `POSTGRES_MIGRATION.md`. No production schema was changed during implementation.
2. Copy `bybit-chase.example.json` to the installation's reviewed configuration and point `BYBIT_CHASE_CONFIG` to it. Set `enabled: true` only when that installation is ready to execute demo entries. No execution configuration is currently activated by this work.
3. Supply the existing private `family_trading_bot_bybit_demo_api_key` and `family_trading_bot_bybit_demo_api_secret` through the worker environment. Never put them in JSON or source control.
4. Read-only demo-account access and public instrument metadata were verified. The example maps current scanner IDs NEARUSD, SOLUSD, DOGEUSD to NEARUSDT, SOLUSDT, DOGEUSDT, respectively. FET's mapping could not be verified and is excluded. Other assets require explicit mappings; forex/stocks are not silently substituted with crypto contracts.
5. All 94 backend regression tests passed in disposable Postgres. No actual orders or live Telegram test messages were sent. Mocked execution/database tests do not establish live fills, stop acceptance, or production deployment status. Regression coverage includes scanner, routing, execution, existing paper trading, restricted 4h/1h scope, and the immediate-next-PB entry rule.

Sources: `bybit_chase.py`, `rsi_pb.py` (`scan`), `paper_trading.py` (`worker`), `cloud/bybit-chase.sql`. API references: [demo service](https://bybit-exchange.github.io/docs/v5/demo), [order submission](https://bybit-exchange.github.io/docs/v5/order/create-order), [position protection](https://bybit-exchange.github.io/docs/v5/position/trading-stop), [instrument metadata](https://bybit-exchange.github.io/docs/v5/market/instrument).
