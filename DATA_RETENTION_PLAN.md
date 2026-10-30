# Data cleanup and compaction plan

Prepared 29 October 2026. Scope: this trading-bot repository, its local artifacts, and the application's Postgres data. This is a plan; no data has been deleted and no production database has been queried.

## 1. Define relevance by dependencies

Data is eligible only when it is outside the agreed retention window, not pinned, and no longer needed for active configuration, scanning, pending setups, delivery deduplication, paper-trade reconciliation, saved research, feedback, follow-ups, or migration recovery. Disabled assets and archived/deleted findings are not automatically disposable.

Age alone is insufficient. In particular:

- `platform_app.py` checks sent/suppressed signal payloads to prevent duplicate alerts. Keeping only `finding_keys` would not preserve this check.
- `finding_followup.py` needs the original pivot candle and subsequent candles; its `all` mode can require the complete interval.
- `cloud/postgres-dashboard.sql` builds findings from all saved backtest reports. Deleting an old report can remove findings with feedback even when a newer report exists.
- Local CSVs and `data/historical-2020/report.json` still have runtime fallback consumers in `backtest_jobs.py`, `candle_views.py`, `finding_feedback.py`, and `finding_followup.py`.
- Several relationships live in JSON/text rather than foreign keys. A foreign-key check alone cannot establish safety.

## 2. Measured local inventory

Approximate filesystem allocation from this checkout; categories below can overlap where indicated. Production sizes and recoverable database bytes remain unknown.

| Item | Size | Proposed treatment |
| --- | ---: | --- |
| `data.zip` | 231 MB | Inspect archive listing and compare checksums against retained files. Remove only if redundant or superseded by a verified recovery copy. |
| Four legacy release directories under `data/` | 68 MB total | Move required recovery artifacts outside active data paths; remove redundant versions after migration recovery requirements are satisfied. |
| `output/rsi-pb-sp500-2026-10-29/` | 59 MB | Recent research: keep now. Later compact losslessly or expire only when unpinned and reproducible. Candle JSON contributes about 53 MB. |
| `data/historical-2020/`, `data/historical-2026/` | 6.6 / 19 MB | Preserve inputs, manifests, configurations, and referenced reports. Compact derived copies only after reader changes or verified regeneration. |
| `tmp/pdfs/` | 11 MB | Remove completed temporary renders after confirming final documents exist and no job is using them. |
| `__pycache__/` | 3 MB | Rebuildable; clear when convenient. |
| `.venv.python3.14-backup/` | 19 MB | Remove only after confirming no launcher uses it and the current environment can be rebuilt. |
| `data/dev/` | 1.6 MB | Rotate development logs; preserve configuration and useful failure evidence. |

The zip, legacy releases, temporary PDFs, bytecode, and old environment total approximately **332 MB of conditional candidates**, not guaranteed savings. Moving an archive elsewhere on the same disk does not reclaim disk space. Recent research output is excluded from this estimate.

## 3. Proposed retention policy

These are implementation defaults to review, not existing guarantees. Apply cutoffs in UTC and report both oldest retained data and deletion counts.

| Data class | Proposed policy | Required protection |
| --- | --- | --- |
| Routine local logs and `activity_log` | Keep 30 days of routine detail; 90 days of errors/deployment evidence. Optional daily counts for 12 months. | Preserve incidents under investigation; classify by explicit events, not guessed text matches. |
| Provider request/response cache | Keep the existing Tiingo 24-hour expiry; optionally sweep when idle. | Never delete requests inside the active rate-limit window. Existing request-time pruning already handles this. |
| Completed scanner commands/backtest jobs | Keep 90 days and the latest terminal record per relevant job category. | Exclude pending/running, migration-held and unresolved failures; preserve referenced results. Add completion time where absent. |
| CSV research and generated reports | Keep pinned runs; retain unpinned completed runs for 90 days, then archive inputs, report, version and checksums together before expiry. | Keep latest displayed report and all runs referenced by feedback, follow-ups, or paper trades. Deduplicate exact content only. |
| Live and replay candles | No automatic age-based deletion initially. Build per-feed/timeframe dependency ranges first. | Retain strategy warm-up, pending setup history, chart ranges, saved replay ranges and finding follow-up intervals. `minimum_history(rules)` is only one lower bound, not the whole policy. |
| Momentum and stock-screen histories | Compact identical snapshots; expire unused historical snapshots after 90 days only once dependency analysis exists. | Preserve current universe, current tracking inputs, saved reviews and research source intervals. |
| Signals, delivery records, queue identities | Preserve initially. Compact old payloads only after introducing durable deduplication records and updating all readers. | Keep accepted message IDs, outcomes, identities and ambiguous submissions; never retry effects merely because history was removed. |
| Feedback, finding state/keys, configuration | Preserve user decisions, current configuration, applied revision and deletion tombstones. | Archive old configuration versions with restore support; never infer that an old rating/comment is irrelevant. |
| Paper trades, migration/recovery records, runtime ownership/state | No automatic purge in this iteration. | Preserve reconciliation, baselines, pending setups, migration receipts/audit/holds, final snapshots, retirement manifest and worker controls. |

Lossy aggregation of daily candles into weekly/monthly bars is excluded: it changes strategy/replay results. Lossless archive compression is suitable only when consumers can read it or the artifact is removed from active use. A `.gz` file is not a drop-in replacement for existing CSV/JSON readers.

## 4. Implementation sequence

1. **Inventory and dry run.** Build `scripts/retention.py` with report-only default. Report local path, allocation, modification time, content hash, dependencies, reason, pin and proposed action. For Postgres, report table/index/TOAST sizes, row counts, date ranges and eligible counts, including legacy public dashboard storage separately from `bot_runtime`. Confirm which installation owns each dataset. Do not load credentials into reports.
2. **Protect and verify recovery.** Create a checksummed backup/export for affected records and artifacts, test restoration in a disposable database/directory, and retain the manifest outside cleanup targets. Preserve the migration recovery set required by `README.md` and `POSTGRES_MIGRATION.md`. Identify archives containing secrets and keep their access restricted.
3. **Clean local disposable artifacts first.** Use an explicit manifest of selected paths; exclude symlinks and files changed since inventory. Quarantine selected files for seven days, then remove them after verification. Check archive contents before classifying `data.zip` or old binaries as redundant. Do not blanket-delete `data/`, `output/`, or historical folders.
4. **Add bounded database retention.** Start with routine logs, expired provider caches and eligible completed jobs. Add indexes only after inspecting query plans. Use small transactions (initially up to 1,000 rows), bounded timeouts and a maintenance lock. Recheck eligibility inside each transaction and skip active work. Use a dedicated maintenance role; do not give the worker general administrative rights. Record counts, cutoff and run ID without copying private payloads into logs.
5. **Implement dependency-aware compaction.** Add run pinning, content hashes, explicit completion timestamps where missing, and report-to-source dependencies. If old signals must shrink, first add durable deduplication identities and migrate/test every duplicate check. Preserve finding lookup and feedback access before removing report payloads. Treat this as a separate change from simple expiry.
6. **Reclaim and monitor.** After deletion, use ordinary Postgres vacuum/analyze through the appropriate maintenance connection; measure reusable space separately from reduced physical storage. Schedule table rewrites only if measured bloat justifies the locking, extra disk and maintenance window. Rotate local/service logs. Introduce a daily retention run only after a successful manual dry run and first application; no automation is created by this plan.

## 5. Acceptance and rollback

- Dry-run candidates exclude every pinned/referenced record, current configuration, active job, pending notification/setup and reconciliation hold.
- Boundary tests cover UTC cutoffs, timestamp units (epoch seconds versus candle milliseconds), null/missing timestamps, repeated execution and concurrent job creation. Unknown age/dependencies mean retain.
- Restore tests recover an archived run, its source candles, feedback and follow-ups. A cleanup failure stops that phase and leaves unprocessed records intact.
- Compare scanning and pending setup results on fixed candles before/after cleanup. Replaying an already delivered/suppressed signal must not create another alert; use mocked external effects.
- Dashboard checks cover latest and pinned backtests, historical finding lookup, ratings/comments, charts, follow-ups, paper trades and current configuration. Test application changes with the existing Python and browser suites using a disposable Postgres database.
- Publish before/after bytes, eligible/deleted/skipped counts, archive location and restore instructions. Do not promise database savings before measuring production.

First implementation scope: inventory/dry run, verified local artifact cleanup, log rotation and bounded low-risk database expiry. Candle pruning and signal/report compaction follow only after their dependency and deduplication protections exist.

## Implementation status — 30 October 2026

The initial conservative implementation is `scripts/retention.py`; operational instructions are in `docs/data-retention.md`. It provides a default local inventory, verified compressed quarantine/restore, explicit inactive-log selection, database table size inventory, and optional bounded expiry for Tiingo caches and three routine log events. Local archives have no automatic purge. Database credentials are supplied separately; production maintenance is not implicit.

Completed-job expiry, research pinning/dependency migrations, signal compaction, candle pruning, old release/environment removal, archive deduplication and scheduling remain deferred. Their relevance cannot yet be established safely by the tool. Unknown dependencies are retained. This implementation does not claim the full conditional 332 MB estimate as reclaimable.

Initial application result: 131 old bytecode files (1,486,100 bytes) were replaced by verified compressed recovery archives and a manifest (757,230 bytes), reducing logical file bytes by 728,870. A second inventory found no remaining eligible local files. Recovery manifest: `.retention/1793382274421953000/manifest.json`. Database maintenance was tested only in a disposable local cluster; no production data was changed. Validation: Python discovery ran 364 tests, with 363 passing and the native Linux installer test skipped on macOS; all 25 browser tests passed. All eight new retention tests passed, including real Postgres cases.
