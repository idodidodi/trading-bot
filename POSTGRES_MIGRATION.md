# Postgres installation guide

This guide has two parts: work you can do on your **Mac**, using your online-provider credentials, and work that must execute on the **remote Linux machine**. You can run the Linux commands over SSH from the Mac; physical access is not required.

Keep Supabase administrator/service credentials on the Mac. Linux needs only its dedicated worker database login and the provider credentials used by the scanner. Use a separate installation directory and retain the existing Linux installation as a stopped fallback.

Use [main](https://github.com/idodidodi/trading-bot/tree/main). Replace `LINUX_USER`, `LINUX_HOST`, `INSTALLATION_UUID` and example paths with your actual values. Paths beginning `/home/LINUX_USER/` refer to Linux, even when you prepare their configuration on the Mac.

**Order:** Mac M1–M3 → Linux L1–L3 → Mac M4 for rehearsal. After rehearsal passes, prepare the production destination and worker credentials in M2, repeat the Linux connection test L2, then repeat Linux L3 and Mac M4 with fresh **final** snapshots. Then Mac M5 → Linux L4–L5 → Mac M6. Recovery responsibilities are in Mac M7 and Linux L6.

## Part 1 — On your local Mac

### M1. Prepare the checkout and migration tools

Use your existing checkout of the branch, or clone into a new directory:

```bash
git clone --branch main \
  https://github.com/idodidodi/trading-bot.git trading-bot-postgres-tools
cd trading-bot-postgres-tools
python3 -m venv "$HOME/.local/share/trading-pg-migration-venv"
"$HOME/.local/share/trading-pg-migration-venv/bin/pip" install -r requirements-runtime.txt
MIGRATION_PY="$HOME/.local/share/trading-pg-migration-venv/bin/python"
```

The dependencies are in [requirements-runtime.txt](https://github.com/idodidodi/trading-bot/blob/main/requirements-runtime.txt). Run the Mac commands below from this checkout. Set `MIGRATION_PY` again when opening a new terminal.

Preserve the current dashboard deployment ID and Edge Function sources/settings through your online accounts. Take an administrator database backup before production changes. The Linux executable, data and service backup must be collected on Linux in L3.

### M2. Prepare Supabase and the Linux worker credentials

Use a separate Supabase project/database for rehearsal. Each destination supports one worker installation UUID and needs the existing dashboard schema, Auth users, installation ownership and sharing permissions.

Include the current [RSI + PB dashboard prerequisite](https://github.com/idodidodi/trading-bot/blob/main/cloud/rsi-pb.sql) when provisioning a fresh rehearsal database. Preserve the deployed production prerequisites and defaults. The runtime scripts install schema version 2, including `rsi_pb_state`, `rsi_pb_pending` and `rsi_pb_signals`; import their saved IDs, versioned waits and baselines without resetting them.

From the Mac, using the SQL editor or an administrator connection, apply these files **in order**, after the existing dashboard migrations:

1. [postgres-runtime.sql](https://github.com/idodidodi/trading-bot/blob/main/cloud/postgres-runtime.sql)
2. [postgres-dashboard.sql](https://github.com/idodidodi/trading-bot/blob/main/cloud/postgres-dashboard.sql)
3. [postgres-worker-role.sql](https://github.com/idodidodi/trading-bot/blob/main/cloud/postgres-worker-role.sql)

These files can be reapplied. Do not subsequently apply older dashboard migrations over them. Keep runtime activation disabled during import.

The worker-role script creates `trading_worker` without login access. In **psql on the Mac** (the `\password` command is not SQL-editor syntax), enable login and set its private password interactively:

```sql
ALTER ROLE trading_worker LOGIN;
\password trading_worker
```

Obtain the dedicated role's username, IPv4 hostname and port for the **shared SESSION pooler** from Supabase Connect. Transaction pooling is unsupported. Use `sslmode=verify-full` with the appropriate trusted CA certificate. Do not give Linux a database-owner or Supabase service-role credential.

Prepare a separate Linux environment file on the Mac using the [worker template](https://github.com/idodidodi/trading-bot/blob/main/.env.postgres.example):

```bash
mkdir -p "$HOME/.config/FamilyTradingBotPostgres-preparation"
chmod 700 "$HOME/.config/FamilyTradingBotPostgres-preparation"
cp .env.postgres.example "$HOME/.config/FamilyTradingBotPostgres-preparation/worker.env"
chmod 600 "$HOME/.config/FamilyTradingBotPostgres-preparation/worker.env"
```

Edit this file with the worker URL and required provider credentials. URL-encode special characters in the URL password. Use the **Linux** paths for `DATA_DIR` and the URL's `sslrootcert`, for example:

- Data: `/home/LINUX_USER/apps/trading-bot-postgres/data`
- CA: `/home/LINUX_USER/.config/FamilyTradingBotPostgres/supabase-ca.crt`

Download the server root certificate from Supabase Database Settings as described in the [Supabase SSL instructions](https://supabase.com/docs/guides/database/psql). The system or certifi CA bundle alone may not trust Supabase's pooler. Save the trusted certificate as `supabase-ca.crt` beside the prepared environment file. Keep `DRY_RUN=true` and `PAPER_TRADING_ENABLED=false`. Do not reuse the fallback environment file or commit any credentials.

A successful connection from the Mac does **not** establish connectivity from Linux; L2 is still required. When moving from rehearsal to production, provision the production role/schema here, update only the new worker environment/CA files, transfer them securely to Linux, and repeat L2 against production. Do not alter the fallback configuration or repeat the new-directory setup over existing files.

### M3. Transfer reviewed code and worker configuration

Make an archive of committed source. It excludes untracked environment files and local databases:

```bash
git archive --format=tar --output="$HOME/trading-postgres-source.tar" HEAD
chmod 600 "$HOME/trading-postgres-source.tar"
scp "$HOME/trading-postgres-source.tar" LINUX_USER@LINUX_HOST:trading-postgres-source.tar
scp "$HOME/.config/FamilyTradingBotPostgres-preparation/worker.env" \
  LINUX_USER@LINUX_HOST:trading-postgres-worker.env
scp "$HOME/.config/FamilyTradingBotPostgres-preparation/supabase-ca.crt" \
  LINUX_USER@LINUX_HOST:trading-postgres-ca.crt
```

Only transfer the dedicated worker credentials. Leave administrator credentials, management tokens and database backups on the Mac. Continue with Linux L1–L3 before importing anything.

### M4. Receive snapshots, rehearse and perform the final import

After Linux L3 has produced the snapshots, copy them to a protected Mac directory:

```bash
SNAPSHOT_SET=rehearsal
MIGRATION_DATA="$HOME/trading-postgres-migration/$SNAPSHOT_SET"
mkdir -p "$MIGRATION_DATA"
chmod 700 "$MIGRATION_DATA"
scp -r "LINUX_USER@LINUX_HOST:postgres-migration-snapshots/${SNAPSHOT_SET}/." "$MIGRATION_DATA/"
```

For rehearsal, restore an authorized consistent export of hosted records, routing, feedback, CSV reports and outcomes into the disposable project too. Empty hosted data does not exercise reconciliation of your actual hosted edits.

Set `TRADING_MIGRATION_URL` in the Mac terminal for the destination administrator connection. Keep it separate from the Linux worker URL. Use Mac certificate paths for this administrator connection, rather than the Linux paths in the worker file. A protected libpq service/password file avoids passwords in command arguments; for example, the setting can be `service=trading_migration sslmode=verify-full`.

Use the [migration tool](https://github.com/idodidodi/trading-bot/blob/main/scripts/migrate-postgres.py). For rehearsal, the destination is the disposable project. **Only for the final import**, select the production destination, set `SNAPSHOT_SET=final` and rerun the receiving block above to copy the fresh Linux `final` directory, then repeat the commands below. Do not reuse the populated rehearsal database as production. Confirm the stopped-writer requirements in L3 before passing `--writers-stopped`.

After receiving the Linux snapshots, run the plan against the rehearsal destination:

```bash
"$MIGRATION_PY" scripts/migrate-postgres.py plan \
  --source "${MIGRATION_DATA}/signals.snapshot.sqlite3" \
  --tiingo "${MIGRATION_DATA}/tiingo.snapshot.sqlite3" \
  --sender "${MIGRATION_DATA}/sender.snapshot.sqlite3" \
  --history-dir "${MIGRATION_DATA}/historical-2020" \
  --installation INSTALLATION_UUID --output "${MIGRATION_DATA}/plan.json"
```

Repeat `--history-dir` for every historical CSV directory. The plan makes no destination writes and exits with code 2 when conflicts need review. Protect its output because it contains private evidence and comments.

Create a local `resolutions.json` mapping each reported conflict key to `"local"` or `"hosted"`, for example:

```json
{"managed_config:1":"hosted"}
```

Review each decision, especially hosted configuration and feedback edits. Both originals of conflicting edits remain in migration audit history. Resolve every reported conflict before importing.

CSV candle conflict keys include `:csv:DIRECTORY/FILENAME`. For those keys, `local` selects that CSV row and `hosted` retains the candle already accumulated from the database or earlier files. Review each file's decision separately, especially a partial first week.

```bash
"$MIGRATION_PY" scripts/migrate-postgres.py import \
  --source "${MIGRATION_DATA}/signals.snapshot.sqlite3" \
  --tiingo "${MIGRATION_DATA}/tiingo.snapshot.sqlite3" \
  --sender "${MIGRATION_DATA}/sender.snapshot.sqlite3" \
  --history-dir "${MIGRATION_DATA}/historical-2020" \
  --resolutions "${MIGRATION_DATA}/resolutions.json" \
  --installation INSTALLATION_UUID --writers-stopped \
  --output "${MIGRATION_DATA}/import.json"
"$MIGRATION_PY" scripts/migrate-postgres.py verify \
  --source "${MIGRATION_DATA}/signals.snapshot.sqlite3" \
  --tiingo "${MIGRATION_DATA}/tiingo.snapshot.sqlite3" \
  --sender "${MIGRATION_DATA}/sender.snapshot.sqlite3" \
  --history-dir "${MIGRATION_DATA}/historical-2020" \
  --resolutions "${MIGRATION_DATA}/resolutions.json" \
  --installation INSTALLATION_UUID --output "${MIGRATION_DATA}/verify.json"
```

Require **zero conflicts and zero mismatches**. Review counts, original finding IDs, feedback revisions, configuration, schedules/baselines, pending deliveries and paper client order IDs. Re-run the import with a new report filename against the same disabled destination; it must verify without duplicating records.

Keep snapshots unchanged. If an import loses its connection during COMMIT, do not assume it rolled back: rerun verify/import with the same inputs to inspect its durable receipt. Large imports retain migration material in memory, so allow sufficient RAM.

### M5. Prepare the hosted dashboard and activate routing

After the final production import verifies successfully, deploy the [backtests Edge Function](https://github.com/idodidodi/trading-bot/tree/main/cloud/functions/backtests) from the Mac using your Supabase deployment credentials. Apply the SQL prerequisites first, deploy this function **before activation**, and preserve its existing authentication settings. Hosted backtests remain independent of Linux.

Inspect held work and broker journal states through your administrator connection:

```sql
select queue,count(*) from bot_runtime.migration_holds group by queue;
select id,payload::jsonb->>'state' from bot_runtime.paper_trades;
```

Keep pending work held until each identity has been reviewed. Do not reset baselines, erase delivery history or bulk-release messages. Reconcile ambiguous paper submissions through your broker account before allowing new orders.

Activate routing while leaving external effects paused:

```sql
update bot_runtime.runtime_control
set enabled=true,effects_enabled=false where id=1;
```

From the Mac browser, verify owner/shared-account access, paginated strategy histories, saved feedback/configuration, candles, follow-ups and a hosted replay while Linux remains stopped. Then continue with Linux L4–L5.

For an optional local browser server, run [dashboard_server.py](https://github.com/idodidodi/trading-bot/blob/main/dashboard_server.py) from the Mac checkout:

```bash
python3 dashboard_server.py
```

It uses public Supabase configuration and authenticated APIs; it needs no worker database credentials and starts no scanner. Add the loopback origin to Supabase's authorized redirect URLs when using emailed sign-in links.

### M6. Authorize external effects after the Linux dry run

After Linux L5 passes and you have reviewed findings and broker state, arrange the desired delivery/paper flags on Linux. Enable the database effects gate from the Mac:

```sql
update bot_runtime.runtime_control set effects_enabled=true where id=1;
```

Then restart the Linux service as described in L5. Release only individually reviewed work, for example:

```sql
delete from bot_runtime.migration_holds
where queue='notification_queue' and key='EXACT_REVIEWED_ID';
```

Cancel unwanted legacy work by updating its status while retaining history, then remove its hold. Dry-run alerts are recorded as `dry-run` and are not automatically resent after delivery is enabled.

Telegram delivery can repeat a message after an ambiguous timeout/crash; it is not exactly once. Keep ambiguous broker submissions under reconciliation rather than blindly retrying them. Manual Telegram tests send actual messages, so avoid them during a no-message rehearsal.

### M7. Handle online recovery and produce recovery exports

Coordinate with Linux L6 to stop workers. From the Mac, pause dashboard writes, disable both runtime flags and take an administrator backup:

```sql
update bot_runtime.runtime_control set enabled=false,effects_enabled=false where id=1;
```

Before new writes/effects, you can restore the preserved dashboard/Edge Function release and coordinate restarting the untouched fallback in L6. **After new writes, orders or alerts, a branch rollback is insufficient; prefer repairing Postgres.**

For post-write recovery, compare every paper client ID, submission, exit, cancellation, fill and position against Alpaca through your online credentials. Record accepted Telegram message IDs and ambiguous attempts. Only after reconciliation, use the [recovery export command](https://github.com/idodidodi/trading-bot/blob/main/scripts/migrate-postgres.py) on the Mac:

```bash
"$MIGRATION_PY" scripts/migrate-postgres.py rollback-export \
  --source "$HOME/trading-postgres-migration/final/signals.snapshot.sqlite3" \
  --installation INSTALLATION_UUID --writers-stopped --broker-reconciled \
  --output "$HOME/trading-postgres-recovery"
```

Use a baseline matching the preserved release's [supported recovery schema](https://github.com/idodidodi/trading-bot/blob/main/migration/legacy-schema.sql) and verify it matches the captured current production release, including RSI + PB tables. Review the generated manifest and `unmapped-history.json` before securely transferring the recovery directory to Linux. It leaves the original fallback untouched, disables paper execution in the recovery copy and cancels held work in that copy.

Some hosted-only archived findings and newer hosted IDs/outcomes cannot be displayed or attached to feedback by the old release. They remain preserved in Postgres and the recovery archive. Older fallback schemas need a reviewed conversion. `--broker-reconciled` is your attestation, not an automated live broker check; ambiguous Telegram acceptance remains uncertain. If these limitations prevent safe recovery, leave the fallback stopped and repair Postgres.

## Part 2 — On the remote Linux machine

Run these commands in an SSH session to Linux. Testing Linux networking, reading its live database files, stopping its processes and installing/managing its service must happen there.

### L1. Receive the files without touching the existing installation

On Ubuntu/Linux Mint, install Python venv support if needed: `sudo apt install python3-venv`.

After Mac M3, create new source/configuration directories and unpack the source archive:

```bash
mkdir -m 700 "$HOME/postgres-migration-source"
mkdir -p "$HOME/.config/FamilyTradingBotPostgres"
chmod 700 "$HOME/.config/FamilyTradingBotPostgres"
test ! -e "$HOME/.config/FamilyTradingBotPostgres/worker.env"
tar -xf "$HOME/trading-postgres-source.tar" -C "$HOME/postgres-migration-source"
mv "$HOME/trading-postgres-worker.env" "$HOME/.config/FamilyTradingBotPostgres/worker.env"
mv "$HOME/trading-postgres-ca.crt" "$HOME/.config/FamilyTradingBotPostgres/supabase-ca.crt"
chmod 600 "$HOME/.config/FamilyTradingBotPostgres/worker.env"
cd "$HOME/postgres-migration-source"
```

The source directory must be new. Verify the prepared environment uses this machine's actual home/data/CA paths. Keep the existing installation and its credentials separate.

### L2. Test connectivity from Linux before installing the application

Use the [read-only connection test](https://github.com/idodidodi/trading-bot/blob/main/scripts/test-postgres-connection.py):

```bash
python3 -m venv /tmp/trading-pg-preflight
/tmp/trading-pg-preflight/bin/pip install 'psycopg[binary]>=3.2,<4'
/tmp/trading-pg-preflight/bin/python scripts/test-postgres-connection.py \
  --env-file "$HOME/.config/FamilyTradingBotPostgres/worker.env"
```

Require a successful verified-TLS connection. The script reports handshake/query latency without printing credentials or changing schema/data. Correct Linux DNS/IPv4 access, firewall, pooler username/password or CA configuration before proceeding. Do not treat a successful Mac test as a substitute.

### L3. Preserve the fallback, stop writers and create snapshots

Record the actual old service/process names and autostart/update mechanisms. Useful discovery commands are:

```bash
systemctl --user list-units
systemctl list-units
pgrep -af FamilyTradingBot
```

Save the executable/source, environment, service/autostart definitions and data. Stop and disable the actual old workers, timers, autostarts and updater/installer processes. Also stop workers on any other machine. Retain the old installation intact and record how to restart it.

For the final production snapshots, coordinate with the Mac to pause dashboard edits and hosted replay/CSV jobs and let in-flight requests finish. All source writers must remain stopped through final import and cutover. A rehearsal snapshot is not a final snapshot.

Prepare the [migration tool](https://github.com/idodidodi/trading-bot/blob/main/scripts/migrate-postgres.py) locally; snapshot creation needs no administrator database credentials:

```bash
python3 -m venv /tmp/trading-pg-migration
/tmp/trading-pg-migration/bin/pip install -r requirements-runtime.txt
SNAPSHOT_SET=rehearsal
SNAPSHOT_DIR="$HOME/postgres-migration-snapshots/$SNAPSHOT_SET"
mkdir -p "$SNAPSHOT_DIR"
chmod 700 "$SNAPSHOT_DIR"
```

For the final pass, set `SNAPSHOT_SET=final` and rerun the directory assignment/creation commands so the new snapshots go into a separate `final` directory. Replace the old-data paths below with the actual paths discovered on this machine:

```bash
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/signals.sqlite3 \
  --output "$SNAPSHOT_DIR/signals.snapshot.sqlite3"
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/tiingo-cache.sqlite3 \
  --output "$SNAPSHOT_DIR/tiingo.snapshot.sqlite3"
/tmp/trading-pg-migration/bin/python scripts/migrate-postgres.py snapshot \
  --source /path/to/old/data/telegram-sender.sqlite3 \
  --output "$SNAPSHOT_DIR/sender.snapshot.sqlite3"
```

Use this backup API rather than copying a running WAL database as an ordinary file. Omit cache/sender arguments only if the files do not exist. Include every existing Tiingo budget file, including other old workers sharing the same provider credential; use distinct snapshot filenames and repeated `--tiingo` arguments on the Mac.

Copy every required historical CSV directory into `SNAPSHOT_DIR`, preserving its name, such as `historical-2020`. Include any required source manifests. The Mac can now receive these files and perform M4; it does not need SSH access to live SQLite files while importing.

After a completed rehearsal, you may deliberately resume the old installation while preparing the final maintenance window. Stop it again and take fresh final snapshots before production import.

### L4. Prepare the new application directory

After the final import, run the [installation preparation script](https://github.com/idodidodi/trading-bot/blob/main/scripts/prepare-postgres-install.sh):

```bash
mkdir -p "$HOME/apps"
bash scripts/prepare-postgres-install.sh \
  "$HOME/apps/trading-bot-postgres" \
  "$HOME/.config/FamilyTradingBotPostgres/worker.env"
```

The application destination must not exist. The script copies the application, creates separate dependencies and leaves services stopped.

Copy required raw CSV inputs into the new tree and coordinate reviewing their configured paths from the Mac. Do not copy SQLite databases, WAL/SHM files or the old environment file into the new installation. Automatic updates are disabled; keep old startup/update mechanisms separate.

### L5. Start the new worker in dry-run mode

Wait until Mac M5 has activated routing with `effects_enabled=false` and verified the dashboard. Confirm the Linux environment still has `DRY_RUN=true` and `PAPER_TRADING_ENABLED=false`.

Adapt the [systemd service template](https://github.com/idodidodi/trading-bot/blob/main/scripts/trading-bot-postgres.service.example) into a **new** unit at `~/.config/systemd/user/trading-bot-postgres.service`. Replace every absolute path. Confirm the old worker/autostart remains disabled, then run:

```bash
systemctl --user daemon-reload
systemctl --user start trading-bot-postgres.service
journalctl --user -u trading-bot-postgres.service -f
```

Alternatively, run the [application entrypoint](https://github.com/idodidodi/trading-bot/blob/main/platform_app.py) in the foreground:

```bash
TRADING_BOT_ENV=prod \
TRADING_BOT_ENV_FILE="$HOME/.config/FamilyTradingBotPostgres/worker.env" \
"$HOME/apps/trading-bot-postgres/.venv/bin/python" \
"$HOME/apps/trading-bot-postgres/platform_app.py"
```

Review Linux logs and coordinate checking findings from the Mac. Verify that only one worker installation is active. Do not enable boot startup until dry-run checks pass.

When ready for Mac M6, set the desired delivery/paper flags in the **new** environment file. After the Mac enables the effects gate, restart the new service:

```bash
systemctl --user restart trading-bot-postgres.service
```

Dry-run alerts are not automatically resent. Keep the old worker stopped throughout.

### L6. Stop or restore the worker during recovery

Stop the new service and prevent any automatic restart before Mac M7 disables routing and reconciles data:

```bash
systemctl --user stop trading-bot-postgres.service
systemctl --user disable trading-bot-postgres.service
```

Also stop any foreground or separately launched worker. Leave the old worker stopped until the Mac has confirmed whether a simple pre-write rollback is safe or has supplied a reviewed post-write recovery export.

For post-write recovery, restore copies from the reviewed export into a **separate recovery data directory**, configure the preserved release to use it, and keep the original fallback data unchanged. Keep the fallback updater/autostart disabled and its mirror credentials empty until intentionally reconciling the old dashboard store. Never restart stale original SQLite after Postgres has accepted new writes.

If the Mac's recovery review cannot account for required history or ambiguous external effects, keep both workers stopped and repair the Postgres installation instead.
