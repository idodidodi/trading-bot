# Manual Postgres-only migration

This branch is a prepared migration, not a deployed release. Nothing here installs a service, changes production, sends messages or orders, or removes the old installation automatically. Start with a disposable rehearsal database. One `bot_runtime` schema supports one installation UUID per database; use a separate database/project for rehearsal/development. This is not a multi-tenant worker schema.

## What changes

Python uses Psycopg 3 and one shared bounded pool of **three** connections, plus **one** dedicated ownership connection: maximum four per process. Use Supabase's **shared SESSION pooler**, IPv4 hostname and port from the Connect panel (normally 5432). Transaction pooling is unsupported: ownership uses a session advisory lock. Prepared statements are disabled. TLS must verify the server certificate (`sslmode=verify-full`, or `verify-ca` with an explicitly trusted CA). Do not use an owner/service-role credential for the worker. Browsers receive only public Supabase configuration and sign in through Supabase Auth/RPC; owner/editor/viewer boundaries remain enforced. Local browsers can run `python dashboard_server.py` with public configuration and **no database credentials or Linux worker**.

All mutable runtime state lives in Postgres. No runtime imports SQLite or starts a sync thread. `cloud_sync.py` and `cloud/sync_once.py` reject the retired operation. `runtime_records` is a SQL projection of operational tables, not another stored mirror. Existing `public.dashboard_records` remains intact for pre-cutover compatibility and recovery; after activation, the dispatcher reads operational tables, and old ingestion and Telegram coordination are fenced out for that installation.

Hosted replay remains independent of Linux. The updated backtests Edge Function reads canonical candle rows through `dashboard_replay_history`, computes results in the Edge runtime, and atomically saves a report, finding identities, outcomes and completed job through `dashboard_finish_backtest`. Local replay jobs use the same history/jobs tables and the Python worker. Existing hosted CSV replay writes the shared CSV report table. Linux is not required for hosted replay. The existing hosted per-series replay limit remains 100,000 candles; the importer and stored history have no 500-finding or summary-payload cap.

Python transactions are scoped to database work; provider and broker calls happen outside them. Tiingo reserves its shared request budget before making a request, including failed requests. Reservations survive restarts. Delivery and catch-up claims use atomic row locking/`SKIP LOCKED`; result and notification intent commit together. Paper orders retain their deterministic client IDs and submitting/reconciliation journal. Ambiguous submissions are looked up, never blindly reposted. A lost COMMIT reply is reread through durable identities; database transactions are not automatically replayed by the pool.

Ownership reconnects with the same persisted generation if no replacement took over. A process from an older ownership generation is permanently fenced out. Temporary database errors pause processing and retry with bounded delay. A different active worker is rejected. These checks cannot stop an unmodified old executable: stop and disable every old launcher before cutover. Loss of a session during an in-flight external call cannot be undone; delivery leases and deterministic broker IDs remain necessary.

Telegram is **at least once on ambiguous sends**, not exactly once. A crash/timeout after Telegram accepts a message but before the acceptance is committed can repeat it after the 90-second lease. Accepted deliveries and per-destination sound slots are shared in Postgres. Imported pending work is held for explicit review; it is not silently discarded or replayed.

## State inventory and preserved material

| State / entrypoints | Destination or treatment |
|---|---|
| `platform_app.Store`, scanner, schedules/checkpoints | `signals`, `scan_state`, `scan_schedule`, `scanner_status`; preserve IDs, attempts and baselines |
| Pin-Bars, BB Pin-Bars | Full independent `*_signals` and `*_state`; no summary-size cap |
| Momentum, reviews and tracking | `momentum_runs/history/reports/feeds/reviews`, `paper_trades/status`; retain issued evidence and execution identities |
| Managed assets and changing universes | `managed_config`, `config_history`, `daily_universe`, `weekly_stock_screen`, `stock_screen_history` |
| Feedback / workspace / follow-ups | `finding_feedback`, immutable `feedback_history`, `finding_state`, `finding_followup`; hosted conflicts require an explicit decision |
| Candle and replay state | `candle_cache`, `backtest_candle_cache`, **all** `backtest_runs`, `backtest_jobs`, `csv_backtest_runs`; import raw historical CSV directories too |
| Commands and notifications | `scanner_commands`, `catchup_findings`, `notification_queue`, durable routes and deliveries; imported pending/running rows are held |
| Tiingo cache / quota SQLite | `tiingo_requests`, `tiingo_responses`; import its separate snapshot |
| Telegram sender SQLite | Snapshot retained; optional `TELEGRAM_SENDER_ID` preserves original UUID; otherwise shared per-device identity in Postgres |
| Hosted routing | Existing `public.telegram_routes` is authoritative, read directly through a restricted worker function |
| Retired `cloud_*` queues/conflicts | Archived in `migration_audit`; never processed by the runtime |
| Hosted-only legacy findings/artifacts | `legacy_findings` / `legacy_artifacts` are frozen migration archives, queried directly when appropriate; no runtime snapshot writer |
| Env/secrets, raw CSV source files, Markdown rules, logs, review exports | Files remain explicit configuration, inputs or exports, not a database fallback; copy required CSV inputs to the new directory and review configured paths |
| Activation/maintenance tools, launchers, packaging | Postgres SQL; no SQLite backup creation or file-existence gates. Take an admin `pg_dump` before maintenance. Separate packaged roots `FamilyTradingBotPostgres`; all updater entrypoints and legacy stable-release publishing disabled |

`finding_keys` indexes evidence hashes and original finding IDs; it stores no second copy of the evidence. Hosted and Python-created identities remain attached to their original feedback. The importer archives both sides of conflicting edits, including divergent same-revision feedback, rather than dropping the losing original. The chosen current revision stays in the operational table; the alternative remains in protected migration audit history.

## Before installation: Linux connection test

Use a new secrets file, e.g. `/home/USER/.config/FamilyTradingBotPostgres/worker.env`, copied from `.env.postgres.example`. Enter only the dedicated worker connection URL, verified CA path and provider settings you need. Protect the directory/file with modes 700/600. Obtain the **session** pooler hostname and username for the custom role from Supabase Connect. Do not guess the region/project suffix. Do not put database credentials in browser config.

Before installing the application, run from the reviewed checkout:

```bash
python3 -m venv /tmp/trading-pg-preflight
/tmp/trading-pg-preflight/bin/pip install 'psycopg[binary]>=3.2,<4'
/tmp/trading-pg-preflight/bin/python scripts/test-postgres-connection.py \
  --env-file /home/USER/.config/FamilyTradingBotPostgres/worker.env
```

This command reads the URL without executing the file as shell code, connects with verified TLS, confirms TLS server-side, runs five `SELECT 1` probes in a read-only transaction, and prints handshake/median/max milliseconds. It prints no URL, credentials, remote error body or traceback. It changes no schema/data and does not load the scanner. A failure needs DNS/IPv4, pooler username, password, CA, firewall or project-availability correction before installation. No remote connectivity benchmark was run during implementation.

A schema owner must provision the worker role before this preflight. Use `cloud/postgres-worker-role.sql` after the schema/API migration, then manually `ALTER ROLE trading_worker LOGIN` and `\password trading_worker` in psql. Alternatively make a separate login a member of only this role. Do not grant it owner, service-role, superuser, bypass-RLS or schema-creation privileges. The script intentionally creates the role with NOLOGIN and never embeds a password.

## Rehearsal and additive schema preparation

1. Preserve a copy of the current **dashboard static release** and Edge Function sources/settings. Keep the prior Cloudflare deployment ID available. Preserve the old Linux executable/source checkout, environment file, data directory, service unit and autostart configuration. Record the exact fallback version/commit. Do not run the old updater against the new directory or vice versa.
2. Use a separate disposable Postgres/Supabase project and installation UUID. Its Auth user/member rows and existing latest dashboard schema must exist before the new adapter. For a purely local test database, `scripts/setup-postgres-tests.py` provisions the current baseline with an Auth stub; **never use the stub in Supabase/production**. Historic one-time description rewrites are already incorporated in the current base and must not be blindly replayed.
3. A schema owner manually applies, in order: `cloud/postgres-runtime.sql`, `cloud/postgres-dashboard.sql`, `cloud/postgres-worker-role.sql`. Apply the adapter after the currently deployed dashboard migrations (including sharing, follow-ups, Pin-Bars, BB Pin-Bars, CSV backtests, sorting and Telegram routing/delivery). It clones current asset validation; it deliberately fails if prerequisites are missing. These three new files are repeatable. Do not subsequently reapply older dispatcher migrations over the new wrapper.
4. Deploy the updated `cloud/functions/backtests` **before activating** Postgres routing. It works with inactive legacy installations through the new history RPC as well. No CSV Edge Function code change is needed. Preserve existing JWT verification/auth behavior. This is a manual operator step; nothing in this task deployed it.
5. Copy complete consistent snapshots and required historical CSVs into a protected rehearsal directory. To rehearse hosted edit reconciliation, restore a consistent authorized export of the existing hosted records/routing/CSV/feedback/outcome tables into the disposable project too. An empty hosted rehearsal cannot establish reconciliation correctness for your real data.

For an admin connection, prefer a protected libpq service file and `.pgpass`, e.g. `service=trading_migration sslmode=verify-full`, rather than placing passwords in command arguments. Set `TRADING_MIGRATION_URL` in the operator environment; it is intentionally separate from the least-privileged worker URL. CLI tools never read an implicit production destination.

Snapshot using SQLite's backup API (not `cp signals.sqlite3` while a WAL writer is active):

```bash
python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/signals.sqlite3 --output /secure/rehearsal/signals.snapshot.sqlite3
python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/tiingo-cache.sqlite3 --output /secure/rehearsal/tiingo.snapshot.sqlite3
python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/telegram-sender.sqlite3 --output /secure/rehearsal/sender.snapshot.sqlite3
```

Snapshots are internally consistent individually. For the final cutover all old writers must be stopped while taking **all** of them. If a separate cache/sender file does not exist, omit that argument. Do not omit an existing Tiingo budget file. Repeat `--tiingo` for every old worker using the same provider credential; their reservations are conservatively combined.

Plan, resolve and import (replace `INSTALLATION_UUID` with the rehearsal destination's UUID):

```bash
python scripts/migrate-postgres.py plan --source /secure/rehearsal/signals.snapshot.sqlite3 \
  --tiingo /secure/rehearsal/tiingo.snapshot.sqlite3 --sender /secure/rehearsal/sender.snapshot.sqlite3 \
  --history-dir /secure/rehearsal/historical-2020 \
  --installation INSTALLATION_UUID --output /secure/rehearsal/plan.json
```

The plan writes complete conflicts and counts, exits 2 when choices are needed, and makes no destination writes. Protect the report: it contains private evidence/comments. Create `resolutions.json`, mapping each reported conflict key to `"local"` or `"hosted"`, for example `{"managed_config:1":"hosted"}`. Review hosted edits first; never choose all local simply to suppress conflicts. Repeat `--history-dir` for every historical directory. A conflict at the same feedback revision retains both originals in audit; reconcile any `feedback-final:*` inconsistency before proceeding.

```bash
python scripts/migrate-postgres.py import --source /secure/rehearsal/signals.snapshot.sqlite3 \
  --tiingo /secure/rehearsal/tiingo.snapshot.sqlite3 --sender /secure/rehearsal/sender.snapshot.sqlite3 \
  --history-dir /secure/rehearsal/historical-2020 --resolutions /secure/rehearsal/resolutions.json \
  --installation INSTALLATION_UUID --writers-stopped --output /secure/rehearsal/import.json
python scripts/migrate-postgres.py verify --source /secure/rehearsal/signals.snapshot.sqlite3 \
  --tiingo /secure/rehearsal/tiingo.snapshot.sqlite3 --sender /secure/rehearsal/sender.snapshot.sqlite3 \
  --history-dir /secure/rehearsal/historical-2020 --resolutions /secure/rehearsal/resolutions.json \
  --installation INSTALLATION_UUID --output /secure/rehearsal/verify.json
```

Require zero conflicts and zero mismatches; review per-table source/destination counts, original IDs, every feedback revision/audit original, schedules/baselines, current config revision and paper client order IDs. Re-run import into the **same disabled destination**: it must verify without adding duplicates. Import is a single atomic admin transaction, with a snapshot hash and manifest receipt. An interrupted uncommitted import can restart; a lost commit reply is detected by its receipt. This is intentionally a stopped-writer operation, not an online incremental replication mechanism. It loads migration material into memory; large imports need more RAM than the worker. Do not reuse the populated rehearsal destination for final production import.

The tooling refuses unmapped SQLite tables and incompatible pre-existing destination values. Missing raw CSVs, legacy operational tables or hosted exports cannot be inferred from capped summaries. Resolve omissions before final import. Keep the original snapshots unchanged.

## Prepare the new installation, without starting it

```bash
bash scripts/prepare-postgres-install.sh \
  /home/USER/apps/trading-bot-postgres \
  /home/USER/.config/FamilyTradingBotPostgres/worker.env
```

The destination must not exist. The script copies reviewed source/public assets, creates a separate virtual environment, installs requirements, and does **not** install or start services. Keep required raw CSV source inputs in this new tree; never copy any `*.sqlite*`, WAL/SHM files or the old `.env` into it. Review CSV paths through the authenticated Assets editor to avoid depending on writable files in the fallback installation. The normal runtime has no SQLite fallback even when the URL/server is unavailable.

For a local browser only, use `python dashboard_server.py` from the reviewed source. It serves only public assets/config on loopback and sends browser requests through Supabase Auth. Add the loopback origin to Supabase's authorized redirect URLs if using emailed sign-in links. It starts no worker and needs no database URL. The worker's local HTTP server serves the same authenticated browser bundle; unauthenticated local `/api/*` mutations are disabled.

## Final write pause and manual cutover

1. Arrange a maintenance window. Stop **all** old scanner processes on Linux and other machines. Stop/disable their systemd services, desktop autostarts, timers and updater/installer processes. The old installation is a stopped fallback, not a concurrent worker. Discover the actual unit/process names (`systemctl --user list-units`, `systemctl list-units`, `pgrep -af FamilyTradingBot`); do not assume this repository owns a particular pre-existing unit. Record exactly how to restore them. Keep the executable/data untouched.
2. Pause dashboard edits and active hosted replay/CSV jobs. Let already in-flight requests finish. Take an admin database backup (including schema/functions/Auth membership and hosted tables), final stopped SQLite/cache/sender snapshots and historical-file copies. This final write pause is mandatory; a morning rehearsal snapshot is not a cutover snapshot.
3. Apply the three additive SQL files to production manually if not staged earlier. Run plan/import/verify against the final snapshots and the production hosted edits, using a new production destination schema with `runtime_control.enabled=false`. Resolve every conflict. Existing public records and the old dispatcher remain available while disabled. If abandoning a partial rehearsal schema in production, back it up and explicitly have an admin clear it; the importer does not silently overwrite it.
4. Confirm pending work remains held:

```sql
select queue,count(*) from bot_runtime.migration_holds group by queue;
select id,payload::jsonb->>'state' from bot_runtime.paper_trades;
```

Review held alerts/notifications/commands **by identity**. Retain the holds until you decide whether to cancel or deliver each item; removing a hold authorizes processing that original pending item. Expired paper candidates should be skipped by existing execution rules; ambiguous journal states must reconcile against Alpaca before any new order. Do not reset baselines or delete delivery history to “start clean”.
5. Set `DRY_RUN=true` and `PAPER_TRADING_ENABLED=false` in the **new** environment, keep `effects_enabled=false`, then activate routing manually:

```sql
update bot_runtime.runtime_control set enabled=true,effects_enabled=false where id=1;
```

This switches the same public API to direct operational records and fences old ingestion/coordinator calls. The original dashboard bundle remains API-compatible; preserve its deployment for reversal. Verify owner and shared accounts, both browser origins, complete paginated strategy histories, saved feedback/config, candles, follow-ups, and an independent hosted replay while Linux is stopped.
6. Copy/adapt `scripts/trading-bot-postgres.service.example` to a **new** user unit. Replace all absolute paths and confirm the old unit/autostart stays disabled. Only now, manually run `systemctl --user daemon-reload` and `systemctl --user start trading-bot-postgres.service`. Do not enable boot startup until dry-run verification passes. A manual foreground alternative is:

```bash
TRADING_BOT_ENV=prod \
TRADING_BOT_ENV_FILE=/home/USER/.config/FamilyTradingBotPostgres/worker.env \
/home/USER/apps/trading-bot-postgres/.venv/bin/python \
/home/USER/apps/trading-bot-postgres/platform_app.py
```

7. After reviewing dry-run/new findings and reconciling the broker, explicitly authorize effects: change delivery/paper flags in the new config as desired, enable `effects_enabled=true` with an admin, and restart the new service. Releasing a specific reviewed hold uses `delete from bot_runtime.migration_holds where queue='notification_queue' and key='EXACT_REVIEWED_ID';`. Cancel unwanted legacy work by updating its status while retaining its history, then removing that hold. Do not bulk-delete holds as an initialization step.

Dry-run processing marks newly processed alerts `dry-run`; it does not later send them automatically. Review this when choosing the dry-run window. The effects gate blocks automatic Telegram/broker writes even if an imported saved paper setting was enabled. Manual Telegram tests are explicit sends and should not be used during a no-message rehearsal.

## Stop, recover, or roll back

Before any new writes/effects, stop the new process, leave it disabled, set `enabled=false,effects_enabled=false`, restore the preserved dashboard deployment/Edge Function release as needed, then manually restart the untouched fallback. The new schema does not remove old public records. This branch also disables the legacy stable-release publish job, so a future main/tag build cannot accidentally feed these incompatible binaries to the stopped fallback updater. Do not start two installations or re-enable an old updater accidentally.

**After new writes, orders or alerts, a branch/binary rollback is insufficient. Prefer pausing and forward-fixing.** Stop workers and dashboard writes, set both runtime flags false, take a fresh admin backup, and compare every paper client ID/submission/exit/cancel state against the broker's current orders, fills, positions and account. Record accepted Telegram message IDs and ambiguous attempts; an ambiguous Telegram acceptance cannot be proven absent by a database export. Never blindly retry a `submission unknown` order or silently deliver all pending messages.

A supported offline recovery export creates NEW legacy files from current Postgres state, without modifying the stopped fallback:

```bash
python scripts/migrate-postgres.py rollback-export \
  --source /secure/final/signals.snapshot.sqlite3 \
  --installation INSTALLATION_UUID --writers-stopped --broker-reconciled \
  --output /secure/recovery-after-postgres
```

Use the baseline from the preserved current release (`1da8910` schema). The exporter initializes compatible missing legacy tables, transfers current operational/config/feedback/queue/strategy/paper state and Tiingo quota/cache, resets paper enablement in the recovery copy, cancels held work **in that copy only**, clears retired mirror queues, and runs SQLite integrity checking. Review its manifest and `unmapped-history.json` before restoring copies into a separate recovery data directory. Keep original fallback data unchanged. Configure the preserved release against the reviewed recovery directory, disable its auto-updater/autostart, and keep mirror credentials empty until intentionally reconciling the old dashboard store. Do not claim that restarting stale SQLite reconciles anything.

Post-write rollback limitations are explicit: hosted-only archived findings and newer hosted finding IDs/outcome artifacts may not be displayable by the old Python/dashboard release. They remain preserved in Postgres plus `unmapped-history.json`; older code cannot attach every such ID to its locally recomputed evidence. An older fallback schema than the preserved release is unsupported without a reviewed custom conversion. Ambiguous external effects still require operator/broker reconciliation; `--broker-reconciled` records an operator attestation, not a live automated broker verification. If these limitations affect required history or safety, keep the old worker stopped and forward-fix Postgres. Keep the authoritative backup until all displays and external state have been reconciled.

## Validation and memory

Use Python 3.10+ and `requirements-runtime.txt`. For local integration tests, create an EMPTY loopback database named **trading_bot_test** and set `TRADING_TEST_DATABASE_URL` explicitly. The fixture refuses other names/hosts. Then run:

```bash
python scripts/setup-postgres-tests.py
python -m unittest discover -q
node --test web/*.test.mjs
node cloud/test_backtest_function.mjs
```

Without the explicit test DSN, database regression fixtures skip rather than creating SQLite. The SQL Auth stub exists only in that disposable test setup. Tests use mocked provider, Telegram and broker calls. Linux CI provisions its own disposable Postgres service; Windows verifies pure behavior and browser-only packaged startup. Frozen builds and physical Linux/Supabase TLS connectivity still require the operator's acceptance run; they were not deployed or remotely exercised here.

Do not promise a RAM reduction. Removing the sync snapshot thread removes its serialization/queue work, but strategy candle arrays, indicator calculations, replay results, HTTP handling, provider responses and browser charts still use memory. Pool/driver buffers also cost memory. Measure worker RSS on Linux under comparable data/coverage and record browser memory separately. The connection preflight measures only connectivity, not scanner throughput or memory. Rehearsal/import verification intentionally retains complete migration material in memory and is not representative of runtime RSS.

A local macOS development smoke measured process peak RSS at 39.8 MiB after runtime imports and 40.5 MiB after opening Store and three simultaneous pooled connections. This excludes real scanner/provider/replay workloads and the browser, and is not a Linux before/after measurement. It establishes no production RAM saving.

Implementation validation on 24 October 2026: 308 Python tests passed with one platform-dependent skip against a disposable local Postgres database; 25 browser tests passed; the hosted replay Edge Function authentication/background-completion checks passed. The three new SQL files were reapplied successfully. Compile, shell syntax and diff-whitespace checks passed. Provider/broker/Telegram calls were mocked; physical Linux connectivity, production data reconciliation, frozen build execution and live cutover remain operator acceptance steps.
