# Local PostgreSQL working store — stage one

Prepared 15 November 2026; deployed at `ebe79c1`. Follow-up assessed 18 November. Opt-in via
`TRADING_WORKING_DATABASE_URL`; `TRADING_DATABASE_URL` continues to identify
Supabase. The working URL must explicitly use a loopback host and database.
Never substitute it for the shared URL. No automatic schema creation or silent
cloud fallback occurs. Do not expose the PostgreSQL port to the internet.

## What moves

Candle publication records a local PostgreSQL copy and uses local acknowledgements
to suppress unchanged hosted writes for 24 hours. Provider response cache entries
use local PostgreSQL. Historical CSV replay reads local PostgreSQL, without the
previous five-minute hosted-history refresh. Missing local CSV series may be
loaded from the configured CSV and published through the same candle path.

This first stage retains hosted candles for the existing charts and hosted replay
functions. It does NOT yet make Supabase contain only compact UI rows, relocate
hosted Edge Function backtests, or promise any quantified egress saving. Local
backtest calculation already runs on Linux; this changes its history source.
Reports, shared configuration, commands, findings, alert delivery, provider quota
reservations, ownership and deduplication stay in Supabase. Backtest results still
publish through the existing shared report contract.

UI commands use authenticated hosted RPCs and the shared scanner command queue.
Linux claims commands and acknowledges completion. Telegram callback updates are
polled directly by Linux from Telegram; shared callback offsets and delivery state
remain in Supabase. These paths retain their current internet dependency.

## Deployment and rollback

1. Back up the current installation, hosted database and working SQLite file.
2. Provision a dedicated local PostgreSQL database; apply
   `sql/working-postgres.sql` with an owner account. Give the worker role CONNECT,
   schema USAGE and SELECT/INSERT/UPDATE/DELETE on only `working` tables. Store
   its credentials in the existing private worker environment file. Provisioning
   and configuration changes require deployment approval.
3. Stop the existing scanner; verify no competing writer. Export its environment
   internally without printing credentials. Set both database URLs and run
   `scripts/seed-working-postgres.py --writers-stopped` with the installation venv.
   This streams a consistent, read-only hosted snapshot into an empty local
   namespace and commits only after count verification. This transfer itself
   consumes hosted egress. It does not populate publication acknowledgements:
   the first live publication will safely resend current rows.
4. Deploy code, enable the local URL, and restart only the existing service.
   Check service, shared heartbeat, local query health, UI charts, command status,
   and unchanged production signal/delivery settings. Send no test alerts/orders.
5. Measure representative replay/query latency and scan completion time alongside
   CPU, RAM, disk and actual billed egress. If performance or correctness regresses,
   stop the worker, remove ONLY the new working URL, and restart the same service.
   Supabase retains chart data and shared control throughout; the original SQLite
   path remains available. Preserve local PostgreSQL for investigation.

Use pg_dump for private local PostgreSQL backups and verify restoration into a
separate database before relying on them. Transfer backups off-machine. The SQLite
backup command refuses the PostgreSQL backend; it must not produce a misleading
SQLite backup. PostgreSQL health reports query availability and physical database
bytes, not a false full-integrity guarantee. Monitor filesystem free space on the
server separately. A disabled/local-unreachable store is not a healthy result.

## Next stage

Moving all chart history/backtests out of hosted storage requires an authenticated
job/request protocol and bounded result publication, plus a UI freshness/pending
state. Do not delete hosted candle tables until that contract is implemented and
verified. Budget cloud command/config polling separately from candle payloads;
local write suppression is not a measurement of billed egress savings.

## Follow-up assessment — 18 November 2026

Stage one is now deployed at source `ebe79c106b48e9a38db147b84bc23f9ed2a4acd3`.
Read-only production checks found local PostgreSQL healthy, about 264 MB in its
working database, and about 183 GB (170 GiB) filesystem space available. Capacity
is sufficient for the current sub-GB hosted dataset; this is not a throughput or
availability guarantee. The user selected authenticated HTTPS browser access
without a VPN for the next stage.

The daily monitor measured 351,524,531 database bytes (70.3% of 500,000,000), up
24,158,208 bytes from the 9 November post-cleanup baseline. The dashboard's
organization egress reading was 10.929/5 GB for 23 October–23 November, with grace
through 9 December. These are dated observations, not future/live metrics.

A subsequent read-only table inventory found these principal consumers (decimal
MB, including indexes/TOAST):

| Data | Size | Target |
| --- | ---: | --- |
| `candle_cache` | 103.68 MB | Local charts/history |
| `backtest_candle_cache` | 39.67 MB | Local replay/history |
| `activity_log` | 51.43 MB | Local diagnostics with bounded retention |
| `momentum_reports` + `momentum_history` | 48.12 MB | Local reports/replay |
| `migration_audit` + `execution_legacy.momentum_reports` | 47.71 MB | Verified local audit archive; preserve controls/holds |

This inventory establishes current occupancy, not which table caused all growth:
there is no comparable per-table baseline. Candle history was retained on purpose
for hosted charts and hosted Edge Function replay. Local copies alone do not
remove hosted storage or stop shared runtime reads.

`pg_stat_statements` reset on 29 October at 12:21 UTC. Cumulative observations
included 4.51 million worker ownership reads, 588 thousand schedule reads, 316
thousand effect-gate reads, 81 thousand full managed-configuration reads, plus
large historical export queries. These counters are neither billed bytes nor an
October 29 incident timeline. Backups/imports also consume egress. Do not attribute
the entire 10.929 GB to any one query from these counters. Linux receives database
responses from Supabase even when the dashboard has no viewers; this counts as
outbound traffic. Current billing samples imply roughly 0.5 GB/day, still above a
5 GB monthly budget. Deleting data cannot undo traffic already billed this cycle.

### Prepared immediate reduction

`managed_assets.get` conditionally transfers the payload only when its SHA-256
changes; every request still fetches current revisions and checks migration
flags. This catches external same-revision edits and never serves stale data after
a DB error. Migration flags no longer fetch full configurations or take the
migration lock when already applied. `momentum.view` no longer fetches an unused
full replay-history table. These changes do not move data, expose an API, or prove
a quantified billing saving. Signal semantics remain solely in
`TELEGRAM_ALERT_LOGIC.md`. Worker ownership and effect checks stay unchanged.

### Target and staged cutover

1. **Authenticated Linux API.** Keep the existing static Cloudflare UI and
   Supabase Auth. Implement a separate production API adapter for the existing
   dashboard operations; do not simply remove the current local-only API guards.
   Browser requests carry the Supabase access token. Validate signature, trusted
   algorithm, issuer, audience, expiry, and subject, then enforce installation
   membership and per-operation role locally. Reject valid tokens from unrelated
   accounts/projects. Inspect current signing-key mode before implementing JWKS
   verification; do not assume legacy HS256 tokens have a public verification key.
   Define revocation/session expiry behavior explicitly and test key rotation.
2. **HTTPS connectivity.** Proposed transport is an outbound Cloudflare Tunnel
   to a loopback HTTP API, using an authenticated public hostname. Inventory the
   account's existing domains/tunnels first; no hostname or tunnel is provisioned
   by this plan. PostgreSQL stays private. The tunnel is transport, not user
   authorization. Restrict browser origins, request sizes and rates; return
   `Cache-Control: no-store` for private data and never log bearer tokens. Do not
   ship tunnel or service credentials to the browser. Preserve login/recovery and
   show a clear Linux-offline state when the API cannot be reached.
3. **Move read-heavy data first.** Route chart and backtest requests through that
   API to local history; enqueue bounded local backtest jobs rather than running
   long work inside HTTP requests. Move report histories and diagnostics locally.
   Verify chart ranges, history freshness, backtest parity, filters and pagination
   before stopping hosted publication. Remove hosted replay functions only after
   clients no longer call them. Test with disposable data and a read-only pilot.
4. **Move runtime/control as one authoritative system.** Inventory dependencies
   on public RPCs, Auth users, memberships, command queues, notification routes,
   personal preferences and external signal-service consumers. Provision explicit
   local application schema/roles; do not point the existing working-schema DSN
   at runtime code. Migrate queues, IDs, offsets, deduplication, leases, holds,
   quota reservations and ownership together. UI commands become authenticated
   Linux API -> durable local command queue -> existing scanner. Telegram remains
   Linux -> Telegram, with callback/delivery state local. Preserve the executor's
   signal/acknowledgment and execution-notice contracts. Use separate API/worker
   DB roles and explicit authorization, replacing hosted RLS/RPC guarantees.
5. **Single-writer cutover and hosted cleanup.** Back up and restore-verify first;
   use a consistent final snapshot with all relevant writers fenced and drained.
   Verify table counts plus content checksums, pending work and protected records.
   Start only the existing scanner against the new authoritative database. Keep
   hosted copies read-only during verification; never allow two active command or
   delivery queues. A rollback after local writes requires reconciliation, not a
   switch back to stale cloud state. Only after parity and recovery verification
   archive/remove obsolete hosted application data with explicit reviewed scope.
   Auth-owned tables remain hosted. Ordinary DELETE need not shrink physical DB
   size immediately; plan any table rewrite separately with a lock/outage budget.

### Acceptance and operation

- No unauthenticated, expired-token, cross-user or wrong-installation data access;
  all mutating routes check permissions and reject duplicate command execution.
- Charts/settings/backtests and existing scanner consumers work over HTTPS from a
  normal browser. Safe production smoke tests send no test Telegram alerts/orders.
- Measure representative full scan durations, chart API latency, CPU/RAM and disk
  before/after. Local DB latency should improve, but heavy backtests must have
  bounded concurrency so the scanner is not starved.
- Maintain private off-machine backups and actual restore tests. A single always-on
  Linux machine is still a single failure point: alerts cover API/tunnel, worker,
  heartbeat, database availability, disk space, backups and restore age.
- Keep daily Supabase usage sampling. Target less than 100 MB/day recurring egress
  (about 3.1 GB in 31 days, with headroom); compare seven-day same-cycle deltas and
  tag one-time import/export transfers separately. This is a target, not a promise.
  Check 70/85/95% usage and projections as before; update monitoring sources after
  cutover so hosted heartbeats are not incorrectly required.
- Each stage requires relevant tests and AGENTS.md publication approval identifying
  the branch and destinations. HTTPS/API/UI changes require Cloudflare and Linux
  verification; this immediate read-reduction patch requires only Linux deployment.

References: [Supabase egress](https://supabase.com/docs/guides/platform/manage-your-usage/egress),
[Auth signing keys](https://supabase.com/docs/guides/auth/signing-keys),
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/).
