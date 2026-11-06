# Telegram alert logic

Last reviewed: 2026-11-06.

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
Existing manually reported positions keep monitoring and position notifications.
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
`demo_execution.py` (`intent`), `web/app.js` (`chasingControls`,
`cryptoAutoChaseControls`, archived controls).

## Production verification

The source suspension is awaiting deployment. Following explicit owner approval,
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

## Automatic asset setup

Local UI behavior (not verified in production): `web/app.js` (`openAssetSetup`, `automaticAsset`, `assetFrameAvailable`) configures one data connection after the user identifies the instrument. Common currency pairs, BTC/ETH/XBT pairs with explicit quote currencies, and exact supported index/commodity/futures symbols can preselect a category; unknown symbols require a category selection. US-listed stocks/ETFs default to Alpaca, crypto pairs to Kraken, and forex pairs to OANDA. Other instruments require an exact selection from the supported Yahoo/Tiingo mappings. No provider is chosen by timeframe in the normal editor.

New crypto assets default to weekly/daily/4h; monthly is unavailable on Kraken. Other new assets use supported monthly/weekly/daily/4h frames. Lower intervals remain opt-in and restricted by existing provider support. Unsupported intervals are disabled for new selections. Existing asset mappings, timeframe overrides, TradingView mappings, strategy choices and enabled state are preserved on ordinary edits. Existing CSV drafts can enter automatic setup explicitly; no bulk migration changes their venues.

Saving records the chosen instrument mapping (`feed_confirmed=true` is configuration confirmation, not evidence of successful data access). New assets default to scan enabled, with a visible toggle; existing drafts retain their enabled state during setup. Feed access, exact response identity and candle eligibility are checked by the normal scanner/provider code. The UI shows awaiting scanner data check until coverage is available and the configuration revision has been applied; it never treats a save alone as a successful connection test. The add flow uses revision-checked draft creation followed by save, so a failed final save leaves a disabled draft. No test alerts or extra scanner are started by setup. Existing first-scan baselines and alert rules still apply: new assets do not bypass candle validation, strategy qualification, deduplication, routing, or delivery controls.

Sources: `web/app.js` (`openAssetSetup`, `automaticAsset`, `assetDataStatus`, `loadAssets`), `managed_assets.py` (`change`, `validate_asset`, `for_timeframe`), `yahoo_feed.py` (`INSTRUMENTS`), `tiingo_feed.py` (`validate_feed`), provider fetch functions, `scanner.py`.

