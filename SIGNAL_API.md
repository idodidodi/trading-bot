# Signal API v1

Reviewed 2026-11-06. `signal_service.py` is a separate API process; starting it never starts another scanner. The existing scanner publishes atomically with finding persistence. Telegram does not consume this API and its delivery status is never used as a subscriber acknowledgment.

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
