# Telegram alert logic — canonical reference

Reviewed **2026-11-06** against this source tree. This file is the single current alert-behavior reference. Historical strategy/deployment notes describe their releases; when they conflict, use the implementations cited here. The signal/execution separation does not move Telegram delivery, credentials, recipient data, callback ownership or user-reported position alerts to the trading project.

## Qualification and timing

- RSI findings are validated against the Markdown-derived rules, including close-based divergence, inclusive RSI tolerance, pivot spacing and second-pivot Bollinger-band wick touch. A provisional divergence is available at the pivot candle close. Structural confirmation after the configured right candles remains a finding but does not send another Telegram alert. Legacy clock-review slots are cancelled. Multiple first-pivot references share the logical second-pivot alert identity. Sources: `platform_app.py:83` (`validate_signal`), `platform_app.py:166` (`Store.insert`), `platform_app.py:214` (`signal_alert_identity`), `scanner.py:144` (`detect`), `platform_app.py:327` (`delivery_worker`).
- Scanner baselines, selected-universe start times, continuous-feed gaps and provider freshness gates remain in `scanner.scan_once`. First runs establish a baseline rather than flood Telegram with history. Catch-up findings are summarized, not sent as new signal alerts. No new API subscriber, replay or acknowledgment resets any baseline or queues a Telegram message.
- Pin-Bars and BB Pin-Bars use independently versioned closed-candle rules and the existing selection/baseline cutoff. A newly persisted eligible signal enqueues its existing notification only outside catch-up. BB Pin-Bars share the Pin-Bars routing category. Sources: `pin_bars.py`, `bb_pin_bars.py`, `candle_strategy.py:12` (`scan`).
- RSI + PB holds a versioned pending RSI setup and evaluates only its next expected candle, with market/session-aware scheduling. It does not substitute a later candle. Qualification, opposite-wick shape, direction, stale-data protection and missing-next-candle handling remain in `rsi_pb.py:28`, `:37`, `:82`, `:95`, and `:131`. Only new qualifying non-catch-up findings enqueue `rsi-pb:<id>` notifications. Source message rendering: `rsi_pb.message`.
- Daily momentum detection remains available in the dashboard and signal API. This change does not introduce a new momentum Telegram route or emit simulated trade outcomes from the signal service.
- `quiet_hours` is 23:30–06:30 Asia/Jerusalem, with timezone/DST conversion. Existing callers decide when to use silent delivery; the separation does not broaden that policy. Source: `platform_app.py:271` and `send_telegram`.

## Routing and delivery

`telegram_routing.select` chooses the existing market/strategy route, with configured General fallback. It durably remembers destination choice per logical event. Unknown legacy market metadata is recovered using the existing stored evidence. `deliver_routed` can use General fallback for a destination-specific error; it does not reroute ordinary transient failures arbitrarily. Sources: `telegram_routing.py:37`, `:43`, `:48`, `:64`, `:92`.

Personal delivery uses registered recipients and existing opt-in category preferences/routes. Personal failures do not stop other recipients; per-destination dedup supports retry. Group dismissal and personal owner-bound dismissal retain their respective behavior. Sources: `telegram_personal.py:15`, `:22`, `telegram_checklist.py:12`, `:16`, `telegram_dismissal.py`.

The existing `delivery_worker` first claims pending notification work, then RSI findings, respecting retry deadlines and `migration_holds`. Claims use `FOR UPDATE SKIP LOCKED` with a 90-second reservation; failed RSI attempts retain sanitized errors and capped backoff. An empty queue waits one second. Dry-run work is recorded as dry-run and is not automatically resent on later enablement. The shared database effect gate and installation owner still protect external sends. Sources: `platform_app.py:327`, `telegram_delivery.py:36`, `postgres_store.assert_worker`, `local_settings.delivery_dry_run`.

Telegram delivery is not exactly once after an ambiguous network failure. Preserve sender identity, logical event IDs, delivery journal, message IDs, leases and pending holds during deployment. API cursor acknowledgments are unrelated to Telegram acknowledgment/dismissal. Do not bulk replay alerts to test a migration.

## Callbacks, chasing and reported positions

The checklist callback worker retains the sole `getUpdates` consumer and durable offset. Done/Undo, owner checks, deadline cleanup and group/personal keyboard behavior remain in `telegram_checklist.py:66`, `:91`, `:107`, `telegram_personal.py:89`, `:128`, and `telegram_dismissal.py`.

Chasing is opt-in and remains within the existing signal worker. `chasing_runtime.proposal` rejects ineligible root stages/frames, old/pre-registration roots and unconfigured routes. Personal Chase/Chase + look back and crypto automatic watches retain their current lifecycle and settings revisions. Crypto automatic watches descend toward 5m under `crypto_chase_policy.py`; they do not infer broker entry from a signal. Sources: `chasing_runtime.py:41`, `:58`, `:63`, `:130`, `divergence_chasing.py`, `chase_lookback.py`.

User-reported “I'm in” / “I've exited” actions remain alert-only. A post-click closed-minute price is a reference, not an inferred fill. Stops, targets, opposite-divergence alerts, extension/trailing alerts and fresh-price requirements remain in `position_monitor.py` and `divergence_chasing.step`. Removing that module from this service would change explicitly preserved Telegram behavior. It has no broker client or order writes. It is not the trading project's account/position journal.

Chase events remain durable in `chasing_outbox`. `deliver_pending` rechecks current opt-in routes, suppresses stale events, preserves selected chat/owner, uses the existing renderer and dismissal keyboard, retries failures, and avoids attaching “I've exited” to a demo exit without a reported personal position. The chase worker retains its one-second loop; pre-entry/position observation deadlines remain in the lifecycle functions. Sources: `chasing_runtime.py:22`, `:243`, `:346`, `:400`, `:455`, `:550`.

## Execution separation and Telegram handoff

The service no longer starts `paper_worker` or imports broker executors. Broker ownership, accounts, policy and execution data are in `projects/trading-executor/`. All ordinary detection-alert paths described above remain in this service.

Bybit's opposing-divergence close notice still uses the exact `demo_exit` renderer in `chasing_runtime.text_for`, the `live_positions` opt-in route, the original per-trade/per-user identity and original event timestamp. It says a reduce-only close is queued and explicitly does not assert submission/fill confirmation. Execution now commits that fact to its own durable outbox before the broker write. The authenticated `/v1/execution-events` receiver validates it, deduplicates project/notice ID, and transactionally creates the existing signal-service chase session/outbox entries. Only subscriptions explicitly granted execution-notice permission can call it. Recipient lookup, rendering, retry, callback state and Telegram sends stay here. Sources: `signal_service.execution_notice`, `projects/trading-executor/demo_execution.divergence_close`, `projects/trading-executor/signal_client.Client.flush_notifications`.

An API outage can delay this broker-generated notice until the durable handoff succeeds. The runner retains a 15-second idle interval and flushes pending notices after execution processing. This additional network boundary means identical wall-clock latency cannot be guaranteed; event time/message/routing are preserved. Neither handoff nor a Telegram message proves a broker fill.

The sole scanner also publishes opposing RSI evidence for PB-only consumers from its existing candle data. This adds API evidence only, not a Telegram route or a second scanner. New signal-contract `updated`/`invalidated` pivot lifecycle events never enqueue, retract or resend Telegram alerts. Consumer registration/backfill/replay is independent of existing alert state.

## Verification

Regression coverage includes `test_telegram.py`, `test_telegram_routing.py`, `test_telegram_personal.py`, `test_telegram_delivery.py`, `test_telegram_checklist.py`, `test_telegram_dismissal.py`, `test_signal_alert_grouping.py`, `test_chasing_runtime.py`, `test_crypto_auto_chase.py`, `test_position_monitor.py`, and the execution-notice handoff test in `test_signal_service.py`. Tests use disposable storage and mocked Telegram/broker responses. No production test messages or orders are part of verification.
