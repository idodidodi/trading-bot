# Telegram alert logic — canonical reference

Reviewed **2026-11-07** for personal sound controls; other sections retain their stated review scope. This file is the single current alert-behavior reference. Historical strategy/deployment notes describe their releases; when they conflict, use the implementations cited here. Telegram delivery, credentials, recipient data and callback ownership remain in this service.

## Qualification and timing

- RSI findings are validated against the Markdown-derived rules, including close-based divergence, inclusive RSI tolerance, pivot spacing and second-pivot Bollinger-band wick touch. A provisional divergence is available at the pivot candle close. Structural confirmation after the configured right candles remains a finding but does not send another Telegram alert. Legacy clock-review slots are cancelled. Multiple first-pivot references share the logical second-pivot alert identity. Sources: `platform_app.py:83` (`validate_signal`), `platform_app.py:166` (`Store.insert`), `platform_app.py:214` (`signal_alert_identity`), `scanner.py:144` (`detect`), `platform_app.py:327` (`delivery_worker`).
- Scanner baselines, selected-universe start times, continuous-feed gaps and provider freshness gates remain in `scanner.scan_once`. First runs establish a baseline rather than flood Telegram with history. Catch-up findings are summarized, not sent as new signal alerts. No new API subscriber, replay or acknowledgment resets any baseline or queues a Telegram message.
- Pin-Bars and BB Pin-Bars use independently versioned closed-candle rules and the existing selection/baseline cutoff. A newly persisted eligible signal enqueues its existing notification only outside catch-up. BB Pin-Bars share the Pin-Bars routing category. Sources: `pin_bars.py`, `bb_pin_bars.py`, `candle_strategy.py:12` (`scan`).
- RSI + PB holds a versioned pending RSI setup and evaluates only its next expected candle, with market/session-aware scheduling. It does not substitute a later candle. Qualification, opposite-wick shape, direction, stale-data protection and missing-next-candle handling remain in `rsi_pb.py:28`, `:37`, `:82`, `:95`, and `:131`. Only new qualifying non-catch-up findings enqueue `rsi-pb:<id>` notifications. Source message rendering: `rsi_pb.message`.
- Daily momentum detection remains available in the dashboard and signal API. This change does not introduce a new momentum Telegram route or emit simulated trade outcomes from the signal service.
- `quiet_hours` is 23:30–06:30 Asia/Jerusalem, with timezone/DST conversion. Existing callers decide when to use silent delivery; the separation does not broaden that policy. Source: `platform_app.py:271` and `send_telegram`.

## Daily SMA crosses (Golden / Death cross)

- Strategy `ma-cross`: close-based SMA50 and SMA200, evaluated only on completed **daily** candles. Golden/bullish: previous SMA50 ≤ SMA200 and current SMA50 > SMA200. Death/bearish: previous SMA50 ≥ SMA200 and current SMA50 < SMA200. Equality on the current candle does not trigger. Invalid averages do not qualify.
- Require 201 valid closed candles to compare consecutive averages. No price-position, volume, or slope filter. A signal reports both averages, close, source and daily close time; it does not place an order.
- Enable once for all configured assets, including disabled assets without enabling the assets themselves; preserve subsequent strategy opt-outs. New and dynamically selected assets default to this strategy. All existing asset exclusions, market-hours/calendar rules, stock minimum price, provider validation and feed eligibility remain effective.
- Daily scanning is independent of the asset's other strategy timeframes: append a daily check when needed, running only `ma-cross` on that additional timeframe. Do not turn on other strategies for daily or run crosses on intraday/weekly/monthly bars. Missing daily feeds or fewer than 201 candles are reported as unavailable/insufficient history.
- The first eligible scan establishes a baseline without historical alerts. Subsequent cross events use the shared closed-candle strategy persistence and durable queue. Repeated scans/restarts do not enqueue an already recorded event. Catch-up findings are retained without individual Telegram notifications. Persistent below/above status is not a repeating alert; another qualifying crossing can alert.
- Queue prefix `ma-cross:`; independent `ma_cross_state` and `ma_cross_signals`. All markets use the single `other-signals` route (UI label **Other Signals**), separate from the `other.*` market categories. An empty/unavailable destination retries instead of falling back to General. Existing delivery deduplication, sound slots, retries and dismissal behavior apply.
- Personal copies require an explicitly registered `other-signals` personal route and honor that user's preference. Adding the shared destination does not automatically register personal destinations.
- `cloud/ma-cross.sql` upgrades hosted validation, route preferences, daily signal API, runtime tables and existing asset defaults. Apply before the matching worker/UI deployment. The installation's destination is held in routing settings, never hardcoded in application source.
- Local verification on 2026-11-06: Telegram `getChat` confirmed the exported **Other Signals** group identity, and a read-only check found no existing reference in the live shared routes and one personal-route reference. The prepared routing update clears that old personal assignment while retaining unrelated settings. After integrating the current production signal service, 130 targeted backend tests passed in disposable Postgres; all 41 UI tests and the static dashboard build passed. The complete backend suite passed: 545 tests, with one environment-dependent skip; this includes feed activation history requirements. No test messages were sent. These implementation/configuration changes are not yet published to production.

New crossover events also flow through the existing signal API, with SMA evidence; no execution behavior is added.

Sources: `ma_cross.py`, `scanner.py` (`scan_once`), `candle_strategy.py`, `managed_assets.py` (`ensure`, `migrate_ma_cross`), `market_feeds.py` (`verify`), `telegram_routing.py`, `telegram_personal.py`, `cloud/ma-cross.sql`, `scripts/configure-other-signals.py`, `web/app.js`.

## Routing and delivery

For existing market-specific strategies, `telegram_routing.select` chooses the market/strategy route with configured General fallback. Daily SMA crosses use the dedicated `other-signals` route and never fall back to General. It durably remembers destination choice per logical event. Unknown legacy market metadata is recovered using the existing stored evidence. `deliver_routed` can use General fallback for a destination-specific error; it does not reroute ordinary transient failures arbitrarily. Sources: `telegram_routing.py:37`, `:43`, `:48`, `:64`, `:92`.

Personal delivery uses registered recipients and existing opt-in category preferences/routes. Personal failures do not stop other recipients; per-destination dedup supports retry. Group dismissal and personal owner-bound dismissal retain their respective behavior. Sources: `telegram_personal.py:15`, `:22`, `telegram_checklist.py:12`, `:16`, `telegram_dismissal.py`.

The existing `delivery_worker` first claims pending notification work, then RSI findings, respecting retry deadlines and `migration_holds`. Claims use `FOR UPDATE SKIP LOCKED` with a 90-second reservation; failed RSI attempts retain sanitized errors and capped backoff. An empty queue waits one second. Dry-run work is recorded as dry-run and is not automatically resent on later enablement. The shared database effect gate and installation owner still protect external sends. Sources: `platform_app.py:327`, `telegram_delivery.py:36`, `postgres_store.assert_worker`, `local_settings.delivery_dry_run`.

Telegram delivery is not exactly once after an ambiguous network failure. Preserve sender identity, logical event IDs, delivery journal, message IDs, leases and pending holds during deployment. API cursor acknowledgments are unrelated to Telegram acknowledgment/dismissal. Do not bulk replay alerts to test a migration.

## Execution separation and Telegram handoff

The service no longer starts `paper_worker` or imports broker executors. Broker ownership, accounts, policy and execution data are in the separate `idodidodi/trading-executor` repository. All ordinary detection-alert paths described above remain in this service.

Bybit's opposing-divergence close notice still uses the exact `demo_exit` renderer in `chasing_runtime.text_for`, the `live_positions` opt-in route, the original per-trade/per-user identity and original event timestamp. It says a reduce-only close is queued and explicitly does not assert submission/fill confirmation. Execution now commits that fact to its own durable outbox before the broker write. The authenticated `/v1/execution-events` receiver validates it, deduplicates project/notice ID, and transactionally creates the existing signal-service chase session/outbox entries. Only subscriptions explicitly granted execution-notice permission can call it. Recipient lookup, rendering, retry, callback state and Telegram sends stay here. Sources: `signal_service.execution_notice`, `trading-executor:demo_execution.divergence_close`, `trading-executor:signal_client.Client.flush_notifications`.

An API outage can delay this broker-generated notice until the durable handoff succeeds. The runner retains a 15-second idle interval and flushes pending notices after execution processing. This additional network boundary means identical wall-clock latency cannot be guaranteed; event time/message/routing are preserved. Neither handoff nor a Telegram message proves a broker fill.

The sole scanner also publishes opposing RSI evidence for PB-only consumers from its existing candle data. This adds API evidence only, not a Telegram route or a second scanner. New signal-contract `updated`/`invalidated` pivot lifecycle events never enqueue, retract or resend Telegram alerts. Consumer registration/backfill/replay is independent of existing alert state.

## Verification

Regression coverage includes `test_telegram.py`, `test_telegram_routing.py`, `test_telegram_personal.py`, `test_telegram_delivery.py`, `test_telegram_checklist.py`, `test_telegram_dismissal.py`, `test_signal_alert_grouping.py`, `test_chasing_runtime.py`, `test_crypto_auto_chase.py`, `test_position_monitor.py`, and the execution-notice handoff test in `test_signal_service.py`. Tests use disposable storage and mocked Telegram/broker responses. No production test messages or orders are part of verification.

## Chasing suspension

The owner requested cancellation of all chasing and use of its Telegram chats.
`chasing_runtime.CHASING_SUSPENDED` disables manual and automatic enrollment even
when an old `DIVERGENCE_CHASING_ENABLED=true` setting remains present. Signal
messages no longer attach Chase controls. Previously sent start, lookback and entry
buttons acknowledge suspension without changing state; authenticated exit reporting
for an existing position remains available.

The existing worker retires owner-owned, unentered `chasing` and `complete_first`
sessions as `expired`, clears their next deadline, cancels pending lookback and
increments their revision. It suppresses queued watch events. Telegram delivery
also gates the `chasing` and `crypto_auto_chase` routes, including rows loaded before
cancellation. No new cancellation messages are sent to those chats. Messages already
accepted by Telegram cannot be recalled by this suspension.

Demo execution rejects new `chase-4h-5m` intents. Existing submitted/open trades
continue reconciliation and exit management; RSI + PB execution remains separate.
The later Linux signals-only boundary below disables manually reported position monitoring in this service.
This change does not close positions or send test orders. Original RSI, Pin Bars
and RSI + PB signal routing and qualification are unaffected.

The dashboard shows suspension notices and offers no chase activation or chat
registration controls. The original controls remain as explicitly archived
functions; the strategy engines, historical sessions and route registrations are
retained for future reference. No Telegram groups or messages are deleted.

Reactivation requires a reviewed change to the suspension constant, an explicit
review of preferences, chat destinations and execution policy, and regression
checks. Previously cancelled sessions and suppressed alerts must not be replayed.

Sources: `chasing_runtime.py` (`enabled`, `cancel_unentered`, `tick`, `callback`,
`deliver_pending`, `worker`), `telegram_personal.py` (`deliver_personal`),
`idodidodi/trading-executor:demo_execution.py` (`intent`), `web/app.js` (`chasingControls`,
`cryptoAutoChaseControls`, archived controls).

## Production verification

The source suspension is included in the deployed `d53453c` baseline. Following explicit owner approval,
production runtime cancellation was verified on 2026-11-06: all 140 unentered
chases were retired (156 expired including previous history), zero active or
completed-unentered chases remained, manual and automatic chase preferences were
off, and the demo entry policy retained only RSI + PB. The 317 sent messages and
historical groups were retained. Live-position notification preferences remained
on; there were no open demo trade records at verification. No test alerts/orders
were sent and the existing worker was not stopped for this cancellation.

## Done/Undo dismissal and recovery


Personal alerts and shared alerts with group dismissal enabled use a server-side three-second grace period. Leaving a Telegram chat/folder does not cancel it. Pending deadlines survive worker restarts; dry-run pauses external effects. Personal callbacks require the enrolled user; shared-group callbacks require the matching message/chat.

After the grace period, Undo only acknowledges that the window ended; the listener handles deletion separately. Telegram's explicit `message can't be deleted` rejection (including its 48-hour deletion restriction) replaces the countdown with a completed/manual-delete notice and removes Undo. Already-missing messages count as deleted. Other API failures, including a failed fallback edit, keep the item pending with a persisted 30-second retry deadline. One failed item does not stop other pending deletions or callback polling. Database/ownership failures still propagate. Recovery applies to existing persisted pending messages when the updated worker runs; messages without recoverable database state are outside this repair. Production recovery has not been verified.

Sources: `telegram_dismissal.py`, `telegram_personal.py` (`process`, `next_deadline`), `telegram_checklist.py` (`api`, `_worker`); [Telegram deletion limits](https://core.telegram.org/bots/api#deletemessage).

## Colmex scanner integration

Published Colmex integration (deployed at revision `2e9c5aa`; the combined separation release still requires its own verification): `colmex_feed.py` connects the
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
portfolio and other exporter owners. A separate bridge thread in the existing worker
refreshes requests/status every 30 seconds, independent of long scans; it emits no
signals or alerts. The scanner takes the reconciled request set at each scan start.
Revoked members cease contributing demand;
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


## Linux signals-only boundary (2026-11-06 correction)

Position management belongs to the separate trading runtime. The Linux signal
entrypoint no longer starts `paper_trading.worker` (including Bybit demo execution,
reconciliation and exits) or `chasing_runtime.worker` (including position
monitoring and position notices). Their shutdown joins are removed too. The
implementation remains available for reference and a separate trading service.
Signal scanning, ordinary signal Telegram delivery, dismissal handling, market
summaries, backtests and the Colmex feed bridge remain in the signal service.

This supersedes the earlier statement that Linux should retain position management
and RSI + PB *execution*. RSI + PB signal detection and its signal alerts continue;
its demo order execution does not. On 2026-11-06, production demo execution was
disabled and position notification preferences were turned off. Manual and
automatic chasing remain off; there were no open demo trades or entered chase
positions. Alpaca paper execution was already disabled in the worker environment.
No orders were placed or closed by this correction. The boundary was published in `d53453c`; this combined separation release requires its own deployment verification.

Source: `platform_app.py` (`main` startup/shutdown), `idodidodi/trading-executor:paper_trading.py` (`worker`),
`idodidodi/trading-executor:demo_execution.py` (`_cycle`), `chasing_runtime.py` (`worker`). Do not restore
trading worker startup here when reactivating a separate trading service.

## Colmex native backfill

When `COLMEX_HISTORY_DIR` is configured, the three reviewed index symbols use
operator-imported, hash-checked Colmex H1/M5 archives plus current native H1/M5
and M1 exports. Archive imports discard the forming final row. Broker candles
retain their original timestamps; a shifted GER40 H1 candle is never relabelled
or split to fit a UTC boundary. Whole source intervals tile each UTC bucket.
Fresh native source revisions replace equal historical intervals. Native H1 is
authoritative when available; M5/M1 fill only intervals they actually cover.

Quote sessions were read from the live TGLColmex terminal on 2026-11-06: SPX
Mon–Thu 00:00–21:15, 21:30–22:00, 23:05–24:00 UTC, Friday without the final session;
GER40 weekdays 01:10–21:00 UTC; USDINDEX weekdays 01:00–22:00 UTC. Seasonal rules
use New York for SPX/USDINDEX and Berlin for GER40, corroborated by the native
archive across October/November transitions. These calendars distinguish regular
closed time only; holidays, outages and absent trading-session bars are not filled.
A missing open interval resets eligible history. Fully closed buckets are skipped;
partly open buckets retain exact UTC boundaries and only actual quoted OHLC.

Live identity, revision, freshness and UTC-policy checks still apply. New/unreviewed
symbols retain the strict M1 path. Existing minimum-history, first-scan baseline and
alert routing rules remain. Importing archives does not by itself select a provider,
activate an asset or emit historical/test alerts.

## Separation reconciliation

The Linux signal process does not start chase observation or position monitoring. Chasing stays suspended; production position-notice preferences remain off. Execution-fact API receipts retain the existing durable renderer and opt-in gates; only authenticated external executor facts may enter that delivery path. No position prices or broker states are polled by that path. Colmex native history backfill is governed by the separate rules above.

`signal_notice_delivery.worker` only delivers queued external `demo_exit` facts whose stored session is an external demo-exit receipt. It never invokes `chasing_runtime.tick` or its worker. Dry-run and missing-token settings disable this delivery worker, and existing recipient opt-ins and delivery ownership checks still apply. It does not drain legacy position-watch events.

## Discovery boundary — reviewed 2026-11-07

The optional authenticated discovery API (`signal_discovery.py`, routes in `signal_service.handler`) reads published signal events, scanner coverage, configuration and reviewed calendars. It never advances delivery cursors, changes scanner eligibility/baselines, invokes scanners, enqueues Telegram notifications or authorizes broker execution. Discovery session/freshness exclusions apply only to discovery results; they do not alter existing signal qualification or Telegram routing. Calendar/grant provisioning is manual and default-denied. Deploying discovery does not activate a scanner, change notification preferences or send test alerts.

### Personal notification preferences (local implementation, not deployed)

`web/app.js` (`personalTelegramControls`) offers **Deliver silently** for each subscribed route/category and one personal **Quiet hours** control with enable, start, end and timezone. This matches existing subscription granularity (for example Crypto · RSI), not individual findings or tickers. Unsubscribed categories disable the silence checkbox but retain its saved value. Messages continue to arrive during quiet hours.

`cloud/telegram-notification-sound.sql` stores `silent` and `quiet_hours` in `public.telegram_alert_preferences`, keyed by installation and authenticated user. The API validates category booleans, HH:MM times and timezone names, rejects equal start/end when enabled, and uses the existing revision check and per-user transaction lock. Older clients that omit the new fields preserve saved sound settings. Defaults are no per-category silence and disabled quiet hours (22:00–07:00, UTC). Viewers may change only their own settings; the worker reads them through `bot_runtime.personal_recipients()`.

`telegram_personal.py` (`personal_silent`, `deliver_personal`) evaluates wall-clock time at each personal delivery attempt using the configured IANA timezone, including daylight-saving changes. Start is inclusive and end is exclusive; start later than end crosses midnight. Repeated DST hours use the same local-time rule. Final silence is incoming silence OR per-route silence OR active personal quiet hours. Missing settings retain existing behavior; invalid manually written enabled quiet hours fail silent rather than block delivery. Retry attempts reevaluate the current preferences/time; existing eligibility, suppression and destination deduplication are unchanged. These controls affect personal copies only, not shared groups or another recipient's copies.

`telegram_delivery.py` (`deliver`) passes silence to the sender and does not consume the per-destination sound slot for an already-silent message. `platform_app.py` (`send_telegram`) maps it to Telegram's `disable_notification`; no message is intentionally delayed or omitted by a sound preference. Telegram/client mute settings can still suppress sound when these preferences permit it.

Deployment prerequisite: apply `cloud/telegram-notification-sound.sql` after the existing dashboard/personal migrations and before publishing the UI/worker. It wraps the installed API without replacing other handlers and can be rerun. Local verification uses a disposable loopback Postgres database and mocked Telegram transport; no live message or production settings verification was performed for this feature. The isolated preview uses the actual UI component with browser-local demo preferences.
