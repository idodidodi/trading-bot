# Local PostgreSQL working store — stage one

Prepared 15 November 2026. Not deployed. Opt-in via
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
