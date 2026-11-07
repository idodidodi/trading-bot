# Signal API v1

Reviewed 2026-11-07. `signal_service.py` is a separate API process; starting it never starts another scanner. The existing scanner publishes atomically with finding persistence. Telegram does not consume this API and its delivery status is never used as a subscriber acknowledgment.

Run `python signal_service.py` with explicit `TRADING_DATABASE_URL`, `SIGNAL_ADMIN_TOKEN` (32+ random characters), and optional `SIGNAL_API_PORT` (8090). Bind is loopback only. For remote consumers use an authenticated network path and HTTPS reverse proxy; do not forward the scanner dashboard port. Environment files are not automatically loaded by this entrypoint. Apply `cloud/signal-service.sql` first.

All requests require `Authorization: Bearer TOKEN`; POST bodies are JSON. Tokens are returned once, stored only as SHA-256 digests, and must not appear in command history or logs. Admin and subscriber credentials are distinct. The existing worker database role can provision the API tables; subscriber projects receive no database login. One service database represents one installation.

| Endpoint | Credential | Body / behavior |
|---|---|---|
| `POST /v1/projects` | Admin | `{"project":"trading-executor","mode":"live","filters":{},"execution_notices":true}` returns token and starting cursor. Only explicitly trusted execution projects need notice permission. |
| `POST /v1/projects/credentials` | Admin | `{"project":"trading-executor","action":"rotate"}` returns a replacement token, preserving cursor/filters. `revoke` disables access. |
| `GET /v1/events?limit=100` | Subscriber | Read after durable acknowledgment. Optional `after` may rewind within this subscription's delivered range for recovery. Limit 1–500. |
| `POST /v1/ack` | Subscriber | `{"cursor":123}` advances monotonically; cannot acknowledge beyond delivered data. |
| `POST /v1/execution-events` | Authorized live execution subscriber | Durable, deduplicated `demo_exit` facts. The signal service alone selects opted-in Telegram recipients and renders/sends the existing message. |

Filters are nonempty lists keyed by `strategy`, `strategy_version`, `symbol`, `market`, `timeframe`, `direction`, or `kind`. Different keys are AND; values are OR. Filters/mode are immutable: register a new project to change them deliberately. The first trading subscription must include `rsi`, `rsi-pb`, `chase-4h-5m` and `momentum`, and all three event kinds, because opposing RSI and invalidations affect consumer decisions. Do not filter only entry strategies.

Events contain `contract_version=1`, stable `signal_id`, unique `event_id`, `cursor`, `strategy`, `strategy_version`, `instrument` (source, symbol, market), timeframe, normalized bullish/bearish direction, UTC-millisecond `occurred_at` and `published_at`, evidence, origin, and kind. `kind` is `detected`, `updated`, or `invalidated`; evidence may contain `observed_at` for a later lifecycle observation. Stable identity includes strategy/version/source/frame/direction/pivot/stage, not Telegram destination or account. Distinct P1 references may produce distinct evidence events for the same P2 identity. Evidence is allowlisted; accounts, credentials, routes, stops, targets and allocations are excluded. Consumers own sizing, trade windows, entry/exit policy and broker mappings.

Live subscriptions start after the current committed cursor and have a creation-time boundary. Historical backfill, replay subscriptions, and observations predating registration are marked `delivery_mode=replay`. Replay is never permission to execute. `scripts/backfill-signal-events.py` explicitly exports stored RSI/PB findings as replay-only; it does not reset scanner baselines or Telegram delivery history. Offline detection research also exports files for simulation. There is no implicit production backfill.

Pages scan a bounded number of stream rows, then filter. An empty page can advance `next_cursor`; save that cursor too. Persist the inbox and cursor in one consumer transaction, then acknowledge. Lost acknowledgments and restarts are safe; event IDs deduplicate re-delivery. A transactional stream counter prevents commit-order gaps. Retain the full stream in v1: there is no automatic event pruning or silent reset of expired cursors. Plan storage capacity separately before adding retention.

RSI provisional pivots receive later update/invalidation events when the scanner has the required following candles. These events do not retract or repeat Telegram alerts. Structural confirmation remains a separate stored finding/stage. Missing candle history is not proof of invalidation. Other strategies currently emit detections; their producers can emit explicit lifecycle events using the same transaction API.

Delivery is at least once; broker actions cannot be exactly-once across a network. The executor persists an intent before writing and reconciles ambiguous submissions by original broker client ID. The API does not authorize a trade, and subscriber replay mode cannot bypass execution activation/freshness checks.

## Independent consumer example

`examples/signal_subscriber.py` is a dependency-free inbox/cursor example, exercised against this API by integration tests. The separate [Trading Executor repository](https://github.com/idodidodi/trading-executor) implements the production consumer contract without importing this service. The Signal Service desktop window can register a live subscriber and display its token once.

## Read-only discovery

Discovery is a separate contract, `discovery_version=1`. It does not call `/v1/events`, acquire subscription row locks, update `floor`/`ack`/`delivered`, publish events, fetch provider history, start scanning or authorize orders. Database reads use one repeatable-read, read-only transaction. Discovery never grants `delivery_mode=live` to a finding.

Before deployment, apply `cloud/signal-discovery.sql` after `cloud/signal-service.sql`. Existing projects have **no discovery permission**. The migration grants the existing `signal_api` login read access to managed configuration, scanner coverage and calendars if that role exists; provision those grants separately if creating the role later. No production migration or grant is implicit in installing this source.

An administrator explicitly grants or revokes visibility without changing execution subscription filters/cursors:

```http
POST /v1/projects/discovery
Authorization: Bearer ADMIN_TOKEN
Content-Type: application/json

{"project":"trading-executor-dev","discovery":{"enabled":true,"filters":{},"max_lookback_minutes":1440}}
```

Discovery grants are independent of execution subscription filters and registration floors. An empty grant filter explicitly authorizes the installation's full scanner universe within the granted lookback, including eligible findings predating registration. Narrow `filters` when the project should see less. Use `enabled:false` to revoke only discovery; rotating/revoking the project credential also applies to discovery. Authorization is rechecked on every request.

| GET route | Result |
|---|---|
| `/v1/discovery/catalogue` | Visible instrument/provider/exchange/frame/strategy combinations, session and coverage states, supported category labels and observed eligible strategy versions |
| `/v1/discovery` | Eligible published findings, newest observation first, then stable signal ID |
| `/v1/discovery/{signal_id}` | Revalidate one visible finding; returns its latest lifecycle and explicit eligibility reason, including invalidation/closure/staleness; invisible/unconfigured identities return 404 |

All three use the project's bearer token. Search accepts repeated `marketCategory`, `strategy`, `strategy_version`, `timeframe`, `source`, `symbol`, and `direction` parameters. Different dimensions are AND; values are OR. Missing/empty values remove the request restriction but cannot widen the grant. Exact category labels are `Stocks & ETFs`, `Forex`, `Futures`, `Crypto`, `Other assets`. Unsupported category/strategy/frame values, unknown keys and repeated scalar parameters return 400. Daily `ma-cross` discovery includes the extra daily frame scheduled by the scanner even when the asset otherwise selects intraday frames. Supported strategy names do not imply enabled production scanning; the catalogue reports the combinations actually configured/observed.

`max_age_minutes` defaults to the smaller of 60 and the grant allowance, with bounds 1–10080. `limit` defaults to 100, with bounds 1–200. Both original signal age and latest lifecycle observation must fit the requested age; a recent publication/update never refreshes an old signal. Future or missing timestamps fail closed. The matching feed must have a successful strategy check within five minutes, a completed candle at least as recent as the signal observation, no reported limited-history condition, and no next-check deadline overdue by more than five minutes. These conservative checks can exclude a long-timeframe finding even though its signal age is acceptable. No on-demand scan is triggered to overcome that exclusion.

The universe consists of enabled static assets plus the latest scanner-selected rotating/requested assets. Disabled static assets and disabled strategy/frame combinations are excluded. Unapplied configuration revisions disable coverage eligibility until the scanner acknowledges them; old provider/symbol rows cannot substitute for a changed configuration. Matching uses exact `provider:exchange:symbol` source identity and frame; it never substitutes a different provider. Metadata comes from the actual coverage row when available, including an observed fallback provider, otherwise configuration. Before any filtering or paging, each signal is reduced to its newest event, including invalidations. Only live-origin, detected/updated findings can be candidates. Historical/replay findings remain ineligible. The API does not certify broker instrument mappings, execution policy or future tradability.

Response fields include `items`, `excluded` reason counts, `as_of`, `evaluated_at`, `snapshot_expires_at`, `next_page_token`, `page_complete`, `coverage`, `filters`, and `execution_authorized:false`. Items include lifecycle/event identities, source/provider/exchange/category, strategy/version, timestamps, allowlisted published evidence, session status and feed coverage. A result with no items still describes visible coverage and exclusion reasons. `coverage.complete` and `historical_index_complete` are explicitly false: this initial implementation searches published findings only, and does not silently index older stored findings. Pagination completion means only that this result set has been exhausted.

The opaque HMAC-signed page token binds the project/grant, requested filters/age/page size, stream watermark, configuration, coverage, calendar revisions and evaluation time. Send the same filters and scalar parameters with it. Snapshot lifetime is 60 seconds. Changed state, including a new lifecycle event or a session closing between pages, returns **409 `snapshot_changed`**; expiration returns **410 `snapshot_expired`**. Restart the search in either case. Revalidate a selected finding with its identity endpoint immediately before subsequent policy evaluation; an API response cannot prevent a later market change. No stored server-side snapshot or execution cursor is created.

The initial implementation evaluates at most 10,000 latest signal identities and 10,000 catalogue combinations. Exceeding either bound returns 503 with `discovery_capacity_exceeded` or `catalogue_capacity_exceeded`, rather than truncating the universe. This is an explicit initial capacity limit; a materialized/indexed projection is required to scale beyond it. Underlying SQL remains subject to the existing statement timeout.

### Reviewed session calendars

No calendar is guessed from the market category, symbol or provider name. Every source needs an operator-reviewed entry in `signal_discovery_calendars`. Unknown/malformed/expired calendars fail closed, including futures and broker CFDs. Selecting a closed market does not bypass this rule. Continuous calendars are accepted only for the `Crypto` category and still require explicit review and a validity range. Broker-specific crypto products with closures must use explicit sessions.

Use `scripts/import-discovery-calendars.py FILE` to validate a source-to-calendar JSON mapping without connecting to any database. `--apply` additionally requires explicit `DISCOVERY_ADMIN_DATABASE_URL` and atomically upserts the supplied entries. Calendar files must be compiled from reviewed exchange/product schedules and operational closure information; this tool does not download or certify calendar data.

Each value requires `id`, `version`, IANA `timezone`, `verified:true`, nonempty `evidence` describing provenance, UTC-millisecond `valid_from`/`valid_until`, and either `continuous:true` or sorted, non-overlapping `sessions:[[open_ms,close_ms],...]`. Opens are inclusive; closes and validity ends are exclusive. Sessions must be within the validity range. Holidays, weekends, early closes, DST and maintenance gaps are represented by the reviewed UTC intervals, never generic weekday guesses. The operator must renew dated schedules and update exceptional closures. No production calendars are shipped or installed automatically.

### Executor integration

The provisional adapter expecting `contract_version`, a single `events` list and `complete:true` is intentionally incompatible. Update it to consume `discovery_version=1`, `items`, structured coverage, continuation tokens and 409/410 refresh responses. Use returned lifecycle/session eligibility without treating discoveries as execution-delivered events. Local inbox search remains a separate limited feature. This service change does not edit or activate the independent executor.
