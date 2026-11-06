# Local separation verification — 2026-11-06

Worktree: `codex/signal-service-separation`. Production publication/cutover is not authorized by these results.

- Full signal-service Python regression suite: **504 run, 503 passed, 1 skipped**. Disposable loopback Postgres `trading_bot_test`; no production connection. The skip is the native Linux installer test on macOS.
- Independent trading project: **66 passed**, including broker uncertainty/reconciliation, replay/activation rejection, migration identity verification, single-owner locking, cursor persistence before acknowledgment and subscription-token/project mismatch rejection. Broker requests mocked.
- Browser JavaScript tests: **38 passed**.
- Final signal boundary/cutover tests: authenticated HTTP subprocess consumption from outside the repository, restart deduplication, ordered concurrent commits, filters/ack recovery, token rotation/revocation, lifecycle invalidation without Telegram sends, existing demo-exit message/keyboard semantics, and private legacy-table archival preserving broker IDs.
- Standalone isolation check: copied the executor directory to `/private/tmp/trading-executor-isolation-check`; the then-current 65-test suite passed with no parent source imports. A subsequent project-identity test brings the in-tree suite to 66.
- `git diff --check` and Python compilation passed. No real broker orders, Telegram messages, production scanner, publication, or deployment was started.

Commands use the temporary Python environment `/private/tmp/signal-separation-venv` and a separate PostgreSQL cluster on loopback port 55437. Test schemas were provisioned only with the repository's guarded disposable-database setup script. The temporary server is stopped after verification.

Limits: no Linux/Windows frozen executable builds, live broker certification, production schema change, external deployment check, or production health/version verification is claimed. Those belong to an explicitly approved publication/cutover. The new execution-notice HTTP boundary can delay notices during an API outage; Telegram rendering, route preferences and delivery journal remain in the signal service. See `SIGNAL_SERVICE_MIGRATION.md` and the canonical `TELEGRAM_ALERT_LOGIC.md`.
