# Linux installation guide: Postgres edition

Install this version in a separate directory and keep the existing installation stopped as a fallback. Rehearse the migration in a disposable database before using production data.

The commands below run from a checkout of the [Postgres branch](https://github.com/idodidodi/trading-bot/tree/codex/postgres-only-runtime). Replace `USER`, `INSTALLATION_UUID` and example paths with your own values. Keep credentials in protected local files; never enter them in browser configuration or commit them to Git.

## 1. Prepare the database and credentials

Use a separate Supabase project/database for rehearsal. Each database supports one worker installation UUID. The destination needs the existing dashboard schema, Auth users, installation ownership and sharing permissions.

As a database administrator, apply these files **in this order**, after the existing dashboard migrations:

1. [postgres-runtime.sql](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/cloud/postgres-runtime.sql)
2. [postgres-dashboard.sql](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/cloud/postgres-dashboard.sql)
3. [postgres-worker-role.sql](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/cloud/postgres-worker-role.sql)

These files can be reapplied. Do not apply older dashboard migrations over them afterward. Leave runtime activation disabled during import.

The worker-role script creates `trading_worker` without login access. Enable its login and choose a private password interactively in psql:

```sql
ALTER ROLE trading_worker LOGIN;
\password trading_worker
```

Use this dedicated role for the worker, rather than a database owner or service-role credential. Obtain the custom-role username, IPv4 hostname and port for the **shared SESSION pooler** from Supabase Connect. Transaction pooling is unsupported. Use verified TLS: `sslmode=verify-full` and the appropriate trusted CA certificate path.

Copy the [worker environment template](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/.env.postgres.example) into a new configuration directory:

```bash
mkdir -p /home/USER/.config/FamilyTradingBotPostgres
chmod 700 /home/USER/.config/FamilyTradingBotPostgres
cp .env.postgres.example /home/USER/.config/FamilyTradingBotPostgres/worker.env
chmod 600 /home/USER/.config/FamilyTradingBotPostgres/worker.env
```

Edit that local file. Set `TRADING_DATABASE_URL`, the trusted CA path and required provider credentials. URL-encode special characters in the URL password. Initially keep:

```ini
DRY_RUN=true
PAPER_TRADING_ENABLED=false
```

Do not reuse the fallback installation's environment file or data directory.

## 2. Test the connection from Linux before installation

Use the [read-only connection test](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/scripts/test-postgres-connection.py):

```bash
python3 -m venv /tmp/trading-pg-preflight
/tmp/trading-pg-preflight/bin/pip install 'psycopg[binary]>=3.2,<4'
/tmp/trading-pg-preflight/bin/python scripts/test-postgres-connection.py \
  --env-file /home/USER/.config/FamilyTradingBotPostgres/worker.env
```

Require a successful verified-TLS connection. The command reports handshake and query latency without printing credentials or changing schema/data. If it fails, correct the pooler username, password, CA, DNS/IPv4 access or firewall before continuing.

## 3. Preserve the fallback and prepare a rehearsal

Save the existing Linux executable/source, environment, data directory, service unit and autostart settings. Preserve the current dashboard deployment ID and Edge Function sources/settings so you can restore them.

Deploy the [backtests Edge Function](https://github.com/idodidodi/trading-bot/tree/codex/postgres-only-runtime/cloud/functions/backtests) after applying the SQL files and **before activating** Postgres routing. Preserve its existing authentication settings. Hosted backtests can run independently of Linux.

For rehearsal, copy consistent legacy snapshots and all required historical CSV directories into a protected directory. Also restore an authorized, consistent export of the hosted records, routing, feedback, CSV reports and outcomes into the disposable project. Rehearsing with empty hosted data does not test reconciliation of your actual hosted edits.

Use the [migration tool](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/scripts/migrate-postgres.py) for snapshots. It uses SQLite's backup API; do not copy a running WAL database as an ordinary file.

```bash
python3 -m venv /tmp/trading-pg-migration
/tmp/trading-pg-migration/bin/pip install -r requirements-runtime.txt
```

The dependencies are listed in [requirements-runtime.txt](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/requirements-runtime.txt). Use `/tmp/trading-pg-migration/bin/python` for the remaining migration commands.

```bash
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/signals.sqlite3 \
  --output /secure/rehearsal/signals.snapshot.sqlite3
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/tiingo-cache.sqlite3 \
  --output /secure/rehearsal/tiingo.snapshot.sqlite3
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/telegram-sender.sqlite3 \
  --output /secure/rehearsal/sender.snapshot.sqlite3
```

Omit cache/sender arguments only if those files do not exist. Include every existing Tiingo budget snapshot; repeat `--tiingo` when multiple old workers used the same provider credential. For the final production snapshots, stop **all** writers before taking any of them.

## 4. Plan, resolve conflicts and verify the import

Set `TRADING_MIGRATION_URL` explicitly for the destination administrator connection. Keep it separate from the worker URL. A protected libpq service file and password file avoid putting administrator passwords in command arguments; for example, the URL setting can be `service=trading_migration sslmode=verify-full`.

Run the plan against the rehearsal destination:

```bash
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py plan \
  --source /secure/rehearsal/signals.snapshot.sqlite3 \
  --tiingo /secure/rehearsal/tiingo.snapshot.sqlite3 \
  --sender /secure/rehearsal/sender.snapshot.sqlite3 \
  --history-dir /secure/rehearsal/historical-2020 \
  --installation INSTALLATION_UUID --output /secure/rehearsal/plan.json
```

Repeat `--history-dir` for every historical CSV directory. The plan makes no destination writes and exits with code 2 when conflicts need review. Protect its output because it contains private evidence and comments.

Create a local `resolutions.json` mapping each reported conflict key to `"local"` or `"hosted"`, for example:

```json
{"managed_config:1":"hosted"}
```

Review each decision, especially hosted configuration and feedback edits. Both originals of conflicting edits remain in migration audit history. Resolve every reported conflict before importing.

```bash
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py import \
  --source /secure/rehearsal/signals.snapshot.sqlite3 \
  --tiingo /secure/rehearsal/tiingo.snapshot.sqlite3 \
  --sender /secure/rehearsal/sender.snapshot.sqlite3 \
  --history-dir /secure/rehearsal/historical-2020 \
  --resolutions /secure/rehearsal/resolutions.json \
  --installation INSTALLATION_UUID --writers-stopped \
  --output /secure/rehearsal/import.json
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py verify \
  --source /secure/rehearsal/signals.snapshot.sqlite3 \
  --tiingo /secure/rehearsal/tiingo.snapshot.sqlite3 \
  --sender /secure/rehearsal/sender.snapshot.sqlite3 \
  --history-dir /secure/rehearsal/historical-2020 \
  --resolutions /secure/rehearsal/resolutions.json \
  --installation INSTALLATION_UUID --output /secure/rehearsal/verify.json
```

Require **zero conflicts and zero mismatches**. Review counts, original finding IDs, feedback revisions, configuration, schedules/baselines, pending deliveries and paper client order IDs. Re-run the import with a new report filename against the same disabled destination; it must verify without duplicating records.

Keep snapshots unchanged. If an import loses its connection during COMMIT, do not assume it rolled back: rerun verify/import with the same inputs to inspect its durable receipt. Large imports retain migration material in memory, so allow sufficient RAM.

## 5. Prepare the separate application directory

Run the [installation preparation script](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/scripts/prepare-postgres-install.sh):

```bash
bash scripts/prepare-postgres-install.sh \
  /home/USER/apps/trading-bot-postgres \
  /home/USER/.config/FamilyTradingBotPostgres/worker.env
```

The destination must not exist. The script copies the application, creates separate dependencies and leaves services stopped.

Copy required raw CSV inputs into the new tree and review their configured paths. Do not copy SQLite databases, WAL/SHM files or the old environment file into the new installation. Automatic updates are disabled; keep the old installation's updater and startup mechanisms separate.

## 6. Perform the final production import

Arrange a maintenance window after the rehearsal passes:

1. Stop all old scanner processes on Linux and other machines. Disable their services, autostarts, timers and updater/installer processes. Record how to restore them; retain the executable and data unchanged.
2. Pause dashboard edits and hosted replay/CSV jobs. Let in-flight requests finish.
3. Take an administrator database backup, final stopped SQLite/cache/sender snapshots and historical-file copies.
4. Apply the SQL prerequisites to production if they were not staged earlier. Repeat plan, conflict resolution, import and verification using the final snapshots and production hosted edits. Keep `runtime_control.enabled=false` throughout import. Do not reuse the populated rehearsal database as production.

Before activation, inspect held work and paper states:

```sql
select queue,count(*) from bot_runtime.migration_holds group by queue;
select id,payload::jsonb->>'state' from bot_runtime.paper_trades;
```

Keep imported pending work held until you review each identity. Do not reset baselines, erase delivery history or bulk-release pending messages. Reconcile ambiguous paper submissions with Alpaca before allowing new orders.

## 7. Activate and start in dry-run mode

Confirm the new environment still has `DRY_RUN=true` and `PAPER_TRADING_ENABLED=false`. Activate dashboard routing while keeping external effects paused:

```sql
update bot_runtime.runtime_control
set enabled=true,effects_enabled=false where id=1;
```

Verify owner and shared-account access, both browser origins, paginated strategy histories, saved feedback/configuration, candles, follow-ups and a hosted replay while Linux remains stopped.

Adapt the [systemd service template](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/scripts/trading-bot-postgres.service.example) into a **new** user unit at `~/.config/systemd/user/trading-bot-postgres.service`. Replace every absolute path. Confirm the old service/autostart is disabled, then run:

```bash
systemctl --user daemon-reload
systemctl --user start trading-bot-postgres.service
journalctl --user -u trading-bot-postgres.service -f
```

Alternatively, start the [application entrypoint](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/platform_app.py) in the foreground:

```bash
TRADING_BOT_ENV=prod \
TRADING_BOT_ENV_FILE=/home/USER/.config/FamilyTradingBotPostgres/worker.env \
/home/USER/apps/trading-bot-postgres/.venv/bin/python \
/home/USER/apps/trading-bot-postgres/platform_app.py
```

Do not enable startup at boot until dry-run checks pass. Verify that only one worker installation is active.

For a browser-only installation, run the [dashboard server](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/dashboard_server.py) from the checkout:

```bash
python3 dashboard_server.py
```

It uses public Supabase configuration and authenticated browser APIs; it needs no database credentials and starts no scanner. Add the loopback origin to Supabase's authorized redirect URLs when using emailed sign-in links.

## 8. Enable alerts and paper execution deliberately

After reviewing dry-run findings and broker state, set the desired delivery/paper flags in the new environment, enable the external-effects gate as an administrator, and restart the new service:

```sql
update bot_runtime.runtime_control set effects_enabled=true where id=1;
```

Release only individually reviewed pending work. For example:

```sql
delete from bot_runtime.migration_holds
where queue='notification_queue' and key='EXACT_REVIEWED_ID';
```

Cancel unwanted legacy work by updating its status while retaining its history, then remove its hold. Dry-run alerts are recorded as `dry-run` and are not automatically resent when delivery is enabled.

Telegram delivery can repeat a message after an ambiguous timeout/crash; it is not exactly once. Keep ambiguous broker submissions under reconciliation rather than blindly retrying them. Manual Telegram connection tests send actual messages, so avoid them during a no-message rehearsal.

## Recovery and rollback

**Before new writes or external effects:** stop the new process, set both runtime flags false, restore the preserved dashboard/Edge Function release as needed, and manually restart the untouched fallback. Keep the new worker stopped.

**After new writes, orders or alerts:** pause workers and dashboard writes, disable both runtime flags, and take a fresh administrator backup. Prefer repairing the Postgres installation. A branch rollback alone does not reconcile data or broker state.

Before using the [recovery export tool](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/scripts/migrate-postgres.py), compare every paper client ID, submission, exit, cancellation, fill and position against Alpaca. Record accepted Telegram message IDs and ambiguous attempts. Then export into a new directory:

```bash
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py rollback-export \
  --source /secure/final/signals.snapshot.sqlite3 \
  --installation INSTALLATION_UUID --writers-stopped --broker-reconciled \
  --output /secure/recovery-after-postgres
```

Use the snapshot matching the preserved release's [supported recovery schema](https://github.com/idodidodi/trading-bot/blob/codex/postgres-only-runtime/migration/legacy-schema.sql), based on commit `1da8910`. Review the generated manifest and `unmapped-history.json` before restoring copies into a separate recovery data directory. The exporter leaves the original fallback untouched, disables paper execution in the recovery copy and cancels held work in that copy.

Keep the fallback updater/autostart disabled and its mirror credentials empty until intentionally reconciling the old dashboard store. Never restart it against stale original SQLite data after Postgres has accepted new writes.

Some hosted-only archived findings and newer hosted finding IDs/outcomes cannot be displayed or attached to feedback by the old release. They remain preserved in Postgres and the recovery archive. Older fallback schemas require a reviewed conversion. The `--broker-reconciled` flag is an operator attestation, not an automated live broker check; ambiguous Telegram acceptance also remains uncertain. If these limitations prevent a safe recovery, leave the fallback stopped and repair Postgres instead.
