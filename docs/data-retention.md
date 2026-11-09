# Retention operations

`scripts/retention.py` defaults to a local inventory without modifying files or connecting to a database:

```sh
.venv/bin/python scripts/retention.py > /tmp/retention-preview.json
```

Review `action: quarantine` entries. Only bytecode older than seven days is selected automatically. An inactive `.log` file becomes eligible after 90 days only when explicitly named with `--inactive-log data/dev/old.log`. Confirm its writer has stopped before using this option. Files with unknown dependencies, historical CSVs, research output, zip archives, releases, environments, configuration, credentials and recovery data remain untouched. Symlinks and changed candidates are excluded/rejected.

Apply the same selection:

```sh
.venv/bin/python scripts/retention.py --apply > /tmp/retention-result.json
```

Files are compressed losslessly into private `.retention/<run>/` archives. Each archive is read back and checked before removing the original; a cumulative manifest is atomically saved first, so interrupted runs remain recoverable. Archive metadata and payloads can contain private data. Source bytes and archive bytes are reported separately; compression does not guarantee savings. Keep archives at least seven days. Automatic archive purging is deliberately absent: confirm recovery and review the manifest before removing an archive.

Restore using `manifest.json` in the run directory:

```sh
.venv/bin/python scripts/retention.py --restore-local /absolute/path/to/.retention/RUN/manifest.json
```

Restoration verifies checksums and refuses to overwrite existing files. For a partially restored run, resolve existing targets before retrying; do not delete active replacements to restore old logs or bytecode.

## Optional database maintenance

Supply `TRADING_RETENTION_DATABASE_URL` explicitly using a dedicated maintenance role. The command never reads `.env` or falls back to worker credentials. Remote connections require verified TLS. The role needs schema usage, SELECT for inventory/expiry tables and DELETE only for `bot_runtime.tiingo_requests`, `tiingo_responses`, and `activity_log`. SELECT FOR UPDATE may additionally require UPDATE rights on those tables. Do not broaden worker privileges or grant access to unrelated private tables.

```sh
.venv/bin/python scripts/retention.py --database > /tmp/retention-db-preview.json
.venv/bin/python scripts/retention.py --database --apply > /tmp/retention-db-result.json
```

Only Tiingo entries strictly older than 24 hours and routine `Scanner heartbeat`, `Scan cycle started`, and `Scan cycle completed` logs strictly older than 30 days expire. Do not run log expiry while these routine events are needed for an open incident; other events (including market checks and failures) are preserved. Zero/null/future timestamps are retained. There is no deletion of completed jobs yet: terminal timestamps, references and holds need a separate migration.

Each invocation deletes at most 1,000 rows per eligible table inside a transaction protected by a maintenance advisory lock. Busy rows are skipped; locked cache rows cannot be removed during refresh. Statement and lock timeouts bound waits. Backups contain exact row values and are written, flushed and verified before deletion; failures roll back the transaction. An archive can therefore contain rows from a rolled-back run: compare with the database before restoring. Database reports inventory both runtime and public table sizes, including indexes, with estimated live counts; eligible counts are exact. They do not establish that public legacy storage is redundant.

Restore affected rows first in a disposable database using the JSON `rows` array and `json_populate_record(NULL::bot_runtime.TABLE, row::json)`, then compare all fields before restoring missing rows to the installation. Retain primary keys and do not overwrite newer cache values. Request budget rows have no primary key; restore only verified missing occurrences, since blind repeat restoration duplicates requests. `test_retention.py` exercises archive restoration against the disposable schema. No database restore or production maintenance occurs automatically.

After a large expiry, a maintenance administrator may run ordinary `VACUUM (ANALYZE)` on affected tables outside a transaction. Deleted space is normally reusable by Postgres; physical storage reduction is not promised. The original retention command does not perform table rewrites or lossy candle compaction.

## Supabase maintenance from the Mac

Reviewed 9 November 2026. `scripts/supabase-maintenance.py` provides a Mac-side management-API alternative when no dedicated maintenance DSN is configured. It uses the existing `SUPABASE_ACCESS_TOKEN` or Supabase CLI Keychain entry; credentials never enter command arguments or reports. It is pinned to Family Trading Bot project `bwejkkboophbfeiwizbu`. No worker modules are imported, scanner is started, or signal notifications/orders are sent.

```sh
python3 scripts/supabase-maintenance.py health
python3 scripts/supabase-maintenance.py retention
python3 scripts/supabase-maintenance.py retention --apply
```

Health reads project status, physical database bytes, connection usage, runtime activation, scanner phase and hourly heartbeat age. It does **not** supply billing egress: obtain fresh organization usage and billing-cycle dates from the signed-in Supabase usage dashboard. Do not substitute request counts or old screenshots for current usage.

The management retention helper imports the same `TABLES` and `ROUTINE` policy as `scripts/retention.py`. It previews by default. Apply requires the expected active installation, the existing maintenance advisory lock, verified local archives, and matching row contents, transaction version and physical row identity. Concurrently refreshed or locked rows are skipped. Each table is limited to 1,000 rows per run and a 2 MB serialized candidate batch; larger batches stop without deletion and require review. There is no automatic retry after an ambiguous write response. Compare the preserved archive to current rows before resolving such a failure. Archives retain the exact row values under each entry's `row` field for a reviewed restore. No automatic archive purge or broad vacuum/rewrite runs.

Reports, backups, alert state and samples live outside Supabase in private, ignored `.retention/supabase/` files. Keep recovery archives until an explicit recovery review approves disposal. An archive write failure prevents that batch's deletion; earlier successful table batches remain committed and have their own archives.

Two local Codex chat automations were configured on 9 November 2026: daily health at 09:00 and weekly retention on Sunday at 09:30, Asia/Jerusalem. They require the Mac/app, network, Keychain access and a signed-in Supabase browser for billing usage. This is not an always-on server service. The local schedule and latest run results in Codex are authoritative; creating a schedule does not prove future runs will succeed.

Daily checks evaluate 70%, 85% and 95% bands using the current plan limits, plus unhealthy/read-only state and missing or over-two-hour scanner heartbeat. Billing-cycle forecasts use same-cycle samples at least 24 hours apart, reset on billing-cycle changes, and do not infer recovery from missing data. Database growth is tracked independently because it does not reset each month. Alert history is in `.retention/supabase/monitor-state.json`. Health alerts appear in the maintenance chat; this does not alter Telegram signal logic. Weekly retention checks health first, preserves routine logs during an open incident, previews, applies one bounded batch per eligible table, and reports backlog or failure rather than increasing the scope automatically.

### Verified legacy cleanup on 9 November 2026

The user authorized clearing `public.dashboard_records` and `public.dashboard_receipts`, then explicitly approved removal of the duplicated `candle`, `log`, `summary`, `heartbeat`, `command` and `backtest_run` kinds from `bot_runtime.legacy_artifacts`. The existing 30 October backup at `data/backups/alert-display-fd9433e/before-deploy.dump` was restored into a disposable local database. All three legacy tables matched production by exact counts and full-row multiset fingerprints before deletion. Guards rechecked the fingerprints under table locks and verified the Postgres cutover/retired-ingest boundary. The tables themselves and their permissions remain.

The cleanup removed 399,210 mirror records, 393,416 mirror receipts and 396,026 duplicated archive rows. It preserved all 152 archived control records, migration audit/holds, legacy findings, active candles, configuration, feedback, signals and delivery state. Physical database size was then 327,366,323 bytes, versus approximately 1.01 GB before cleanup. Project status was `ACTIVE_HEALTHY`, read-only mode was off, and the running scanner's heartbeat was current. This is a point-in-time verification, not a guarantee about later usage.

The local cleanup manifest, backup digest, comparison, SQL and results are under `.retention/supabase/cleanup-2026-11-09/`. Recovery of removed historical mirror/archive rows now requires the verified external backup; they will not appear in a new live-database recovery export. Rehearse any restore in a disposable database and never restore the whole old backup over active production. The current grace deadline observed in Supabase was 9 December 2026; current-cycle egress was already 6.402/5 GB and resets on 23 November. Cleanup does not erase consumed egress.

## Validation

Run `test_retention.py` with the explicitly disposable Postgres fixture described in the README, the full Python suite, and `node --test web/*.test.mjs`. Tests cover age boundaries, protected data, archive/restore integrity, symlinks, changed files, bounded batches, rollback and concurrent cache locks. Retention tests use no broker or Telegram effects.
