# UI-only deployment and durable data sync

Status: proposed implementation plan, October 6, 2026. No deployment, credentials, settings, or live database changes have been made by this planning task.

## Intended result

Keep candle fetching, detection, backtests, and Telegram delivery on the local computer. Keep SQLite as the source of scanner-generated data and add an online mirror. Publish a small, authenticated UI that reads the mirror and lets the owner manage live assets and their individual timeframe selections, and submit ratings and comments on live and backtest findings, including the EURUSD rows supplied in the request. Online feedback remains writable when the local computer is off; it synchronizes back to SQLite later. The local UI reads SQLite and saves feedback locally without internet access.

Every asset/timeframe check records its outcome, including unavailable data and failures. Successful checks also persist the validated closed candles used for detection. Repeated checks upload a new check record and only new or revised candles, rather than repeatedly uploading the whole history. Backtest runs, results, source metadata, and historical candles are included.

The online UI will explicitly distinguish the latest scan time, latest candle close, latest successful sync, and a stale/offline scanner. A successful sync does not establish that the market feed is current.

## Proposed hosting

- Static HTML/CSS/JavaScript on Cloudflare Pages.
- Managed PostgreSQL, authentication, a read API and narrowly scoped feedback writes through Supabase.
- A small authenticated ingestion function and transactional database procedure for local uploads. These are data plumbing; the scanner and backtest engine do not run online.
- A single shared UI bundle served locally by the existing Python server and online by the static host. A data adapter selects local or online read and feedback endpoints.

This is a recommendation, not a committed provider choice. Verify current storage, request, retention, and plan limits before choosing an account; this plan does not assume a free tier is sufficient. Cloudflare supports static deployments without a dedicated UI server. Supabase supports browser reads governed by authentication and row-level security.

Sources: [Cloudflare Pages](https://developers.cloudflare.com/pages/), [Supabase row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security), [Supabase API security](https://supabase.com/docs/guides/api/securing-your-api).

## Data model

Use additive, versioned SQLite migrations and matching PostgreSQL tables. Give exported objects stable IDs and include an installation ID, schema version, and source sequence. Do not use unqualified local integer IDs as cloud identities.

| Entity | Contents and identity |
| --- | --- |
| installations | Installation ID, heartbeat, scan interval, sync status, rules hash. No local filesystem paths or secrets. |
| instruments / feeds | Asset, exact provider symbol/exchange, quote currency, session timezone, adjustment basis, bucket convention. Distinguish live and historical feed identities. |
| candles | Unique feed + timeframe + candle start; end, OHLC, source hash, revision, observed time. Upsert changed values with revision ordering. |
| scan_runs | Stable run ID, start/end, rules version, overall status; aborted runs remain visible. |
| scan_checks | One record per asset/timeframe attempt: success/failure, count, latest close, detected signal count, sanitized reason, timing. Preserve attempts with no candles or signals. |
| signals | Existing deduplication identity and evidence, linked to scan/check and feed; delivery status and monotonic revision. |
| activity_log | Stable event ID, timestamp, event class, structured sanitized details. |
| delivery_attempts | Attempt ID, signal ID, timestamp, outcome and sanitized error. Never trigger delivery from cloud data. |
| backtest_runs | Run ID, year/range, rule parameters/hash, source manifest hashes, dataset identity, completion state and timing. |
| backtest_results | Run + feed/timeframe; coverage, warm-up count, partial/unavailable status, validation exclusions and signal count. |
| backtest_signals | Run + signal identity, evidence and confirmation time; separated from the live alert queue. |
| asset_configurations | Owner/installation, stable asset ID, feed mapping, enabled/archived state, per-asset timeframe list, desired revision, locally applied revision and validation state. |
| configuration_history | Durable change ID, author/origin, base revision, desired configuration and application acknowledgement; supports conflict handling and audit. |
| finding_feedback | Current owner rating (null or 1–5 stars), comment, canonical finding ID, installation/user ownership and accepted revision. Preserve the finding evidence and rules that identify it. |
| feedback_history | Immutable edits with unique event ID, origin, author, base revision, accepted revision, timestamps and original evidence; includes conflicting edits pending resolution. |
| feedback_pull_cursor (local only) | Last cloud feedback sequence durably applied to SQLite. Separate from the scanner upload sequence. |
| sync_outbox (local only) | Durable pending events with source sequence, entity ID, revision, retry metadata and acknowledgement. |
| ingest_receipts (cloud only) | Installation + event ID/sequence for duplicate-safe processing and acknowledgements. |

Store timestamps as UTC instants, render them in Asia/Jerusalem with DST, and show the timezone in the UI. Preserve source calendar conventions in feed metadata. Provider corrections produce revisions rather than silently changing the provenance of an old replay. Preserve the input revision/hash used by each backtest so later candle corrections do not change what that run means.

## Local write and sync flow

1. Start and persist a scan run/check before the provider call. When a check finishes, transactionally commit validated candles, its outcome, new signals/checkpoint, and corresponding outbox records. Record failures even if the provider returns no candles. Recovery marks interrupted checks as aborted.
2. Keep provider requests and online HTTP requests outside SQLite write transactions. Refactor existing writes, which currently use separate transactions, so the exported state and outbox cannot diverge after a crash.
3. A single background sync worker reads bounded batches from SQLite. Initial limits: 100 events or 256 KiB per batch, one request in flight, 10-second request timeout. Split large historical imports into chunks; never accumulate the full backlog in RAM.
4. An authenticated cloud function validates the installation, event schema, object ownership and batch limits. It applies writes and ingestion receipts in one PostgreSQL transaction and returns acknowledgements only after commit.
5. Mark local events acknowledged only after receiving a matching receipt. Retry timeouts, disconnections and retryable server responses with exponential backoff and jitter, up to five minutes. Surface permanent schema/auth errors as sync failures; retain affected events for repair.
6. Replayed events are harmless. Stable unique IDs prevent duplicate checks/signals/backtests; monotonically increasing revisions prevent an older retry from overwriting newer candles or delivery state. Avoid using arrival time to decide update precedence.
7. Keep all unacknowledged events on disk. Monitor queue age/count and free disk space. Disk or SQLite write failure is a visible storage failure; never pretend the check was safely recorded. Cloud unavailability must not block local detection or Telegram delivery.

PostgreSQL supports conflict-aware inserts; the ingestion procedure must additionally enforce the revision and receipt rules above. [PostgreSQL INSERT documentation](https://www.postgresql.org/docs/current/sql-insert.html).

## Initial import and backtests

- Take a consistent SQLite backup using SQLite's backup API before migration; preserve existing rows, `.env`, and `scanner.json`. New credential names are documented in `.env.example`; the user supplies their values locally.
- Import existing signals, scanner status, logs and backtest database rows. Import `data/historical-2020/report.json` too: the current UI can read it without there being a database run. Use a content hash to deduplicate equivalent imported reports.
- Import historical CSVs in chunks with their manifests, exact feed identity and data-quality limitations. Do not upload multi-gigabyte raw Kraken archives just to display a dashboard. Retain local source archives for reproducibility.
- Establish a consistent snapshot/high-water mark for initial export, then replay subsequent outbox events. Queue new changes while importing so scanning can continue. Do not rebuild or replace the existing database.
- Mark cloud backtest runs complete only after all result/signal chunks are acknowledged. Hide incomplete results from the default completed-run list, but show their upload status.
- Route future backtest CLI runs and history-preparation runs through one persistence/export service. The current temporary-database preparation path must not disappear before its result has entered the durable local export pipeline.
- Running or viewing a backtest never adds live signals to Telegram's delivery queue. The online UI can browse historical results but cannot run computations on an offline local computer.

## UI for low-memory machines

Use plain HTML, CSS and small JavaScript modules; retain the current tables and navigation. Fetch data for the active tab only. Avoid loading report JSON, all historical candles, or all logs into the page. Authentication code loads only where needed.

| View | Initial data and behavior |
| --- | --- |
| Assets | Paginated asset list, enabled state, provider/TradingView mappings, per-asset timeframe controls, comma-separated bulk entry and saved/applied status. |
| Overview | Small summary + latest coverage, 50 live signals per page; show scanner heartbeat and sync delay. |
| Logs | 100 events per page, time/asset/status filters, cursor pagination. Separate event time from upload time. |
| Backtests | Run list and summaries first; fetch 50 signals per page for the selected run. Clearly preserve partial/unavailable coverage. |
| Candle history | Query a selected asset/timeframe/date range; default 200 candles, cap interactive requests at 500. Charts are optional and loaded only when opened. |
| Sync | Last acknowledgement, pending count, oldest pending event, sanitized last error. Local view can show worker state; cloud view derives lag from receipts and heartbeats. |

Live and backtest finding rows end with a **Feedback** column: five clickable star boxes and a comment field. Stars save immediately; comments autosave after a short typing pause or on blur. Use the same interaction locally and online. Show saving, saved locally, pending sync, saved online, retry/error and conflict states accurately. Preserve the draft during automatic refresh and navigation; refresh must not replace an active editor. Use accessible keyboard controls and plain-text rendering for comments.

Local JSON endpoints and online reads share the same field names and pagination contract. Use indexed keyset pagination, not increasingly large offsets. Avoid full-table counts on every refresh; maintain compact summary rows. Export larger datasets as streamed CSV, not a giant browser array.

Refresh the active tab every 30–60 seconds, pause refresh when the document is hidden, cancel previous requests before a new one starts, and retain at most the current page plus one adjacent page. Use ordinary paginated tables rather than keeping thousands of DOM rows alive. Format dates with `Intl.DateTimeFormat` and explicit Asia/Jerusalem timezone.

Set bounded local HTTP concurrency and query limits; the current ThreadingHTTPServer must not create unrestricted request threads. Keep the sync worker to one thread with a bounded queue. Do not run an always-on Node process or a second local database for the UI.

Separate scanner memory from UI memory: current CSV parsing/detection reads entire datasets. For large historical replays, stream CSVs and indicator state with bounded rolling windows, retaining Wilder RSI state and pivot state across chunks. Chunking must match the existing full-history engine; arbitrarily truncating history can change RSI and signal results. Perform large imports/replays one feed at a time.

Proposed measurable budgets, to validate on a 1 GB RAM machine:

- Core UI assets at most 100 KiB compressed, excluding separately loaded authentication/chart modules.
- Browser JavaScript heap at most 30 MiB for a table view after repeated navigation; browser total process memory is a separate measurement.
- Incremental local UI + sync overhead at most 20 MiB RSS; target combined local process at most 256 MiB during ordinary scanning, measured separately from bulk history import.
- Normal list responses at most 100 KiB and historical import batches at most 256 KiB. Stable memory after 100 tab changes and a 24-hour sync/scan run.

These are acceptance targets, not measurements of the current app.

## Authentication, retention and capacity

Default to a private online dashboard. Signed-in owners may read their installation's data and write ratings/comments and managed live-asset configuration through separate validated endpoints. Enforce owner access, rating bounds, a 4,000-character comment limit, finding existence and expected revision. The server derives the author from authentication, not the submitted payload. Keep history append-only and deny browser writes to candles, evidence, rules, strategy rules, secrets and delivery state. Asset/timeframe configuration is writable only through its dedicated owner endpoint. Deny anonymous access. Give the local uploader a revocable machine token scoped to its installation; keep privileged database credentials only in the managed ingestion function. Never put Telegram tokens, provider API keys, webhook secrets, full `.env`, or privileged cloud keys in a browser bundle or exported error text. Supabase explicitly warns that secret/service-role keys bypass RLS and must not be exposed. [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys).

Index candle feed/timeframe/start, check installation/time, log installation/time, signal installation/time and backtest run/confirmation. Fetch only required columns and enforce page/range caps on the server as well as the client.

At the configured six assets × four timeframes × five-minute polling, there are 6,912 checks/day, or about 207,360 checks in 30 days. Unchanged candles do not multiply with each check. Plan initially for 30 days of detailed checks and 90 days of activity/delivery logs, with older check summaries retained. Keep signals, backtests and their required candles/provenance. Measure actual bytes per row and index overhead before selecting a database plan. Retention must never delete unacknowledged outbox data or candles required by retained backtests.

Market-data redistribution permissions are a deployment constraint: start with authenticated personal access. Verify each provider's terms before allowing public data access. This does not require changing the already downloaded local files.

## Implementation order

1. Add migration/backup tooling, stable IDs, normalized candle/check/backtest storage and a durable transactional outbox. Keep cloud sync disabled by default.
2. Add paginated local read endpoints and the shared lightweight UI. Preserve the existing local port, navigation and Israel time formatting.
3. Add cloud SQL schema, owner read policies, narrowly scoped feedback and asset-configuration write policies/endpoints and the authenticated transactional ingestion endpoint. Validate asset-management endpoints locally too. Test isolated credentials/data before connecting the live installation.
4. Add bounded scanner upload and bidirectional feedback and asset-configuration sync, retry/recovery, heartbeat and initial import. Import both database backtests, the saved 2020 report/CSVs, and existing feedback/current history. Reconcile row counts and content hashes.
5. Deploy static UI assets, configure authentication and online adapter, then enable the uploader. No scanner, Telegram worker, credentials file or local data directory is published with the UI.
6. Measure memory/latency, validate outage behavior and retention, then tune batch sizes and polling. Keep deployment configuration separate from live scanner configuration.

## Completion checks and rollback

- Online and local overview/logs/backtests agree after sync, including existing 2020 results and partial-source warnings.
- Every check has a persisted outcome; validated closed candles are stored and queryable. Duplicate polling does not create duplicate candles.
- Disconnect during upload, kill/restart the local app, and resend batches: no missing committed events or duplicate objects. Out-of-order updates cannot regress state.
- Simulate cloud outage: scanning, local UI and Telegram continue; the cloud UI displays stale data, and queued changes catch up after reconnection.
- Test historical import while scanning, provider candle corrections and interrupted backtest uploads. Retained runs remain tied to the input data they used.
- Verify owners can submit feedback, while browser writes to scanner-generated data are denied and installation credentials cannot access another installation. Confirm secrets are absent from assets, payloads and logs.
- Save online feedback with the local app stopped, then restart and verify its arrival in SQLite. Save local feedback while the internet is down and verify it reaches the online UI after reconnecting. Duplicate retries add no duplicate history; simultaneous local/online edits preserve both opinions and show a conflict. Equivalent backtest replays retain feedback; changed evidence/rules are separate findings.
- Meet the memory budgets and stable-memory run above; test database queries with 30 days of check history and the full historical candle set.

Rollback by disabling cloud sync and reverting the UI deployment. Leave the additive local schema and data intact; do not delete pending export data. Local SQLite, detection and Telegram remain operational. Restore from the verified migration backup only if a local migration fails.

## Finding feedback (October 6, 2026 addition)

The local dashboard now supports 1–5 star quality ratings and comments on live findings and backtest findings (last table column). SQLite stores current feedback in `finding_feedback` and every edit in `feedback_history`. A finding ID hashes its source (live/backtest) and canonical complete signal evidence, including rules. Equivalent replays share feedback; changed evidence or strategy settings do not silently reuse it. A rating may be null, comments may be empty, and edits preserve older opinions. `/api/feedback/export` exports the history with original evidence for later strategy analysis.

Extend the online design above before deploying feedback. The online UI must permit authenticated owners to edit feedback and managed live-asset/timeframe configuration through dedicated endpoints, while signal evidence, strategy rules, credentials and delivery state remain protected. Provide corresponding cloud feedback/current-history tables, installation ownership policies, and an authenticated feedback write endpoint. Include feedback in initial import, preserve its evidence/rule version, and retain it beyond finding-view and log retention windows.

Feedback requires bidirectional sync: edits made online while the local computer is offline must eventually reach SQLite, and local edits must reach online storage. Use durable event identities, expected revisions and duplicate-safe receipts; surface concurrent edits for resolution rather than silently dropping one. Commit edits and pending sync events atomically. Preserve immutable history on both sides and show local-save/pending-sync/online-acknowledged status separately. Do not report online persistence until the cloud commits and acknowledges it. Never send feedback changes to Telegram or automatically change strategy settings.

The cloud assigns a monotonic feedback change sequence. Pull changes in bounded pages through the existing sync worker; commit the applied feedback/history and pull cursor in the same SQLite transaction. Imported cloud events must not create new outbound edits. Local offline edits keep their base cloud revision and stable event ID; chain multiple queued local edits in order. A revision conflict returns the current accepted value and retains the competing draft/history for explicit resolution. Do not use last-arriving timestamps to silently choose a winner.

For online autosave, retain a bounded pending draft/event in browser storage during a transient outage, with clear “not saved online” status. Limit storage to pending edits, not candle/report caches; scope it to the signed-in user and clear acknowledged drafts. Re-authenticate before retrying after session expiry. Include an idempotency key with each submission and reuse it on retry. Never show “saved online” for an unacknowledged draft.

Retain feedback and its evidence/history beyond log retention windows so it can support later strategy review. Export ratings/comments together with source, timeframe, exact signal evidence and rules version. Strategy changes remain an explicit separate action after analysis.

The current local feedback implementation and JSON export do not yet supply a deployed cloud endpoint or sync worker. Provider/account selection and deployment are still required to complete online storage.


## Live asset and per-asset timeframe management

Both local and online UIs offer an Assets view. Owners can add, edit, enable, disable and archive live assets. Each asset has its own timeframe selection; new assets start with **monthly, weekly, daily and 4h**. Changing one asset must not change other assets. The initial supported choices are the scanner's existing four intervals. Additional candle intervals require a provider capability check and explicit detection/calendar support; do not offer an interval that cannot actually be fetched or constructed correctly.

Candle interval and polling frequency are separate settings. This request changes which candle intervals are scanned for each asset; it does not automatically change the existing five-minute polling schedule. Label the controls “Candle timeframes” to avoid confusing 4-hour candles with checking the provider every four hours.

Bulk entry accepts comma-separated tickers, for example `NVDA, EURUSD, BTCUSD`. Trim whitespace, remove empty entries, deduplicate canonical identities and preserve exchange/quote qualifiers. Show a lightweight preview with per-item validation, already-added assets, unresolved/ambiguous symbols and the proposed default timeframes. Save valid entries as configured or draft assets; invalid/unresolved entries have explicit reasons and must not silently become active scans. A ticker alone is not sufficient to choose an exact provider instrument, exchange, currency or TradingView feed. Reuse verified mappings; require selection where a mapping is ambiguous. Keep batch and active-asset limits bounded and show the projected provider request load before applying a batch. No provider credentials are submitted from the browser.

Migrate the existing shared timeframe list into explicit per-asset defaults. Retain `scanner.json` unchanged as the initial configuration/import fallback; thereafter managed asset configuration lives in SQLite and cloud tables. Offer an explicit configuration export rather than overwriting that file on each UI save. Existing historical backtest datasets/runs and feedback survive asset edits or removal from live scanning.

Local changes commit configuration/history/outbox atomically. Online changes save to cloud desired configuration and are pulled by the local sync worker. Use the same duplicate-safe change IDs and expected-revision conflict handling as feedback; keep its configuration cursor separate. Show “saved locally/pending sync,” “saved online/pending scanner,” “applied,” or an application error with desired and applied revisions. An online save while the computer is off is not an active scanner change.

The local scanner validates the configuration against available providers/data and applies a consistent revision at the next scan-cycle boundary without restarting. Keep the in-progress cycle tied to its original revision. Replace the global timeframe loop with each asset's effective timeframe list. On newly enabled feed/timeframe combinations, establish the normal first-run baseline so adding an asset does not flood Telegram with historical findings. Existing combinations retain checkpoints; source/rules changes retain the existing rebaseline behavior. Disabled/archived assets stop future checks after the current cycle; preserve findings, candles, feedback and audit history. Surface the distinction between removing an asset from scanning and permanently deleting historical data.

Use paginated lists, bounded bulk requests, and one shared configuration/feedback pull worker to meet the existing memory budgets. Owners can change assets/timeframes, but this does not authorize editing strategy parameters or enabling trade execution.

Acceptance checks: bulk add with spaces, repeats, exchange-qualified tickers and malformed values; two assets with different interval sets; local changes while offline; online changes while the scanner is stopped; conflicting local/online edits; application at scan boundaries; no historical Telegram flood; preserved backtests/feedback after asset archival; accurate saved-versus-applied status.

## Open a finding's candle and external chart

Add a clickable candle/date action to both live and backtest finding rows in both UIs. The default target is the **second divergence pivot candle**, not the later confirmation timestamp. The detail view also offers first pivot and confirmation candles, with clearly labeled times. Persist/reference the confirmation candle's start separately from `confirmed_at` (which is its close); do not subtract a fixed duration for weekly/monthly/session candles. Links identify the immutable feed/timeframe and candle revision used by the finding, rather than an asset's newly edited live timeframe selection.

TradingView chart links are available only for verified exchange-qualified mappings. A BTC/USD Kraken finding must map to Kraken USD, not another exchange or USDT. For Yahoo-derived EUR/USD, cash indices or adjusted equity series, show any different TradingView feed/bucket/adjustment basis instead of claiming an identical candle. Build and encode external URLs on an allowlisted TradingView host; open in a separate tab with `noopener`. Keep mapping metadata alongside asset/feed configuration.

Research result: the official free [Advanced Chart widget](https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/) documents symbol and interval configuration. I did not verify a supported public tradingview.com URL parameter that selects an exact historical candle. Do not promise a date jump or rely on guessed URL parameters. Verify symbol/timeframe behavior in the integration before exposing the action; otherwise open the mapped symbol page and display the required timeframe and candle time for manual navigation. Historical availability can depend on the external feed/account. TradingView's [Advanced Charts time-scale API](https://tradingview.com/charting-library-docs/latest/ui_elements/Time-Scale/) supports programmatic visible ranges in an integrated library; that is distinct from a public chart URL or free iframe widget.

The reliable exact-candle implementation is an on-demand chart inside our local/online UI using [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) and our persisted OHLC data. Its [time-scale API](https://tradingview.github.io/lightweight-charts/docs/api/interfaces/ITimeScaleApi) supports setting the visible range. Load roughly 100–200 surrounding candles, highlight both pivots and the confirmation candle, and show the finding's original rule version and stored evidence. Where useful, plot RSI/Bollinger values computed with the original indicator state; do not recompute from a truncated display window and claim matching historical values.

Provide both “View signal candles” (exact stored data) and “Open TradingView” (external comparison), plus copyable UTC/Israel timestamps. If mapping is unavailable, preserve the exact internal view and explain why the external link is unavailable. If stored candles are missing, show that state rather than drawing synthetic bars. A date-centered internal URL can deep-link to a finding/candle, with authenticated ownership checks online.

Load the chart module only on click, keep one chart open at a time, cap interactive data at 500 bars and dispose of charts/listeners when closing. No TradingView iframe per table row. Include required Lightweight Charts attribution. This implements exact historical candle navigation with bounded memory without depending on unsupported external date links.

Acceptance checks: all four default timeframes, UTC/Israel DST conversion, second-pivot versus confirmation selection, feed mismatch/unmapped assets, changed live configuration, unavailable historical candles, equivalent local/online charts, external link opening, and released chart memory after repeated open/close cycles.

## Implementation status

Implemented locally: asset CRUD through versioned configuration (add/edit/disable), comma-separated bulk entry with deduplication and disabled drafts, independent monthly/weekly/daily/4h selections, scan-cycle application, persisted validated live candles, and on-demand finding candle charts. Existing feedback remains intact. The static hosted bundle uses matching authenticated cloud APIs; new-project Supabase SQL, a private ingestion function, and Cloudflare routing/setup files are in `cloud/`.

Cloud sync is optional and disabled without credentials. It has a SQLite-backed pending queue, bounded batches, duplicate receipts, configuration pulls, feedback-history pulls, conflict retention, and an explicit local action to accept the online configuration after a conflict. Configuration/feedback edits enter the pending queue in their local transaction. Historical CSVs stream into the queue instead of loading all files into memory. One isolated historical staging run queued 26,788 records in about one second with approximately 29 MiB peak process RSS; this is not a complete 24-hour memory qualification.

Verified: 27 Python regression tests, JavaScript syntax, mocked Edge Function authentication/acknowledgement checks, cloud SQL smoke tests in a scratch PostgreSQL database (owner reads, asset writes, feedback writes, candle reads, blocked direct evidence writes and owner isolation), and browser checks for bulk entry, per-asset frames and historical chart rendering. The schema was tested in a scratch database, not an actual Supabase project.

Remaining operational steps: create/configure the new Supabase and Cloudflare projects using `cloud/README.md`, supply private machine credentials locally, publish only the static bundle, and run real-service offline/conflict and sustained-memory acceptance tests. Source adapters remain CSV and Twelve Data; adding a ticker does not establish a live Colmex/Kraken feed. Supabase-specific runtime/deployment and full retention tooling are not claimed complete.
