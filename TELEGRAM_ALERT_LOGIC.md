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

## Colmex scanner integration

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
No orders were placed or closed by this correction. The startup removal is local
until its code deployment is verified.

Source: `platform_app.py` (`main` startup/shutdown), `paper_trading.py` (`worker`),
`demo_execution.py` (`_cycle`), `chasing_runtime.py` (`worker`). Do not restore
trading worker startup here when reactivating a separate trading service.


## Rebuilt asset library (2026-11-06, local implementation)

`web/app.js` (`renderAssetLibrary`, `openAssetSetup`) replaces the old asset settings page with searchable cards and a two-step identify/scanning flow. Shared Colmex forex requests and the automatic weekly stock screen occupy separate collections; their backend eligibility and ownership behavior are unchanged. Existing configured assets retain their provider, exchange, symbols, timeframe overrides, enabled state and strategy choices on normal edits. Removal requires a second explicit confirmation and retains stored signal history. No asset configuration is mutated merely by opening, searching, filtering, cancelling, or changing setup steps.

New instruments use category-based automatic defaults: US stocks/ETFs use Alpaca, spot crypto uses Kraken, forex uses OANDA, and Other requires an exact supported Yahoo/Tiingo instrument selection. Unclear symbols require the user to choose their category. Quote currencies are preserved. New crypto defaults to weekly/daily/4h; other categories default to supported monthly/weekly/daily/4h. Lower intervals remain opt-in and subject to the existing Kraken/CSV restrictions. Monthly Kraken, intraday FMP, and unsupported Colmex frames cannot be newly selected. Existing selected unsupported frames remain removable rather than silently discarded.

New assets default to scan enabled with a visible pause toggle. Existing paused CSV drafts retain their paused state when explicitly converted to automatic data. `feed_confirmed=true` records the selected instrument mapping, not a successful data-access test. The normal scanner validates response identity, candle availability and signal eligibility. Status labels distinguish setup required, paused, awaiting a scanner check, data received, and data problems; data received requires coverage for each selected timeframe, matching symbol/provider and applied revision. A paused asset is not represented as a live connection.

Saving a new asset uses revision-checked disabled-draft creation followed by a separate save. If final save fails, the draft stays paused and the dialog can retry without adding a duplicate. Revision conflicts retain the user's edits; the user can reopen to refresh. Normal first-scan baselines, alert qualification, routing, deduplication and delivery controls still apply. UI checks use fake data; no test alerts, orders or additional production scanner are started.

Sources: `web/app.js` (`automaticAsset`, `assetFrameAvailable`, `assetDataStatus`, `assetHealth`, `openAssetSetup`, `renderAssetLibrary`), `managed_assets.py` (`change`, `validate_asset`, `for_timeframe`), `yahoo_feed.py` (`INSTRUMENTS`), `tiingo_feed.py` (`validate_feed`), provider fetch functions and `scanner.py`. This rebuild has not yet been deployed.

### Cached asset suggestions (2026-11-06, local implementation)

The add flow searches a bundled catalogue in `web/app.js` (`cachedAssetCatalogue`, `assetSuggestions`), built from the `stock_universe.json` snapshot dated 2026-10-08 plus common ETF, forex, crypto and exact supported Other instrument identities. It is cached with the UI code and filtered locally by symbol/name and selected category; typing does not call a provider or mutate configuration. This is a discovery list, not a verification of current index membership, listing status or provider entitlement. Code deployments refresh the bundled list. Existing portfolio entries are marked already added, and duplicate prevention remains in final setup validation. Manual exact-symbol entry remains available when a category is selected and a symbol is absent from the cache. Selecting a suggestion fills its category and exact symbol; the same automatic provider defaults, scanner data checks and alert eligibility rules above apply. The add section is separate from management; management cards and their existing settings are unchanged.

The lookup cache also includes CRCL (Circle Internet Group), verified against Circle investor relations on 2026-11-06. This lookup addition does not add it to the automatic stock-screen universe or enable scanning; the user must complete the normal add/save flow.
