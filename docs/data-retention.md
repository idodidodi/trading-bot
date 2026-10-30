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

After a large expiry, a maintenance administrator may run ordinary `VACUUM (ANALYZE)` on affected tables outside a transaction. Deleted space is normally reusable by Postgres; physical storage reduction is not promised. No table rewrite, scheduled job, or lossy candle compaction is included.

## Validation

Run `test_retention.py` with the explicitly disposable Postgres fixture described in the README, the full Python suite, and `node --test web/*.test.mjs`. Tests cover age boundaries, protected data, archive/restore integrity, symlinks, changed files, bounded batches, rollback and concurrent cache locks. Retention tests use no broker or Telegram effects.
