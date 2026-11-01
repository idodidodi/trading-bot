# Linux source updates

The existing Postgres worker uses source deployments. Legacy executable publication
and binary replacement remain disabled. Windows CI is no longer scheduled; Linux
unit tests, its frozen executable smoke test, and Cloudflare must pass for the exact
main commit before a source update is eligible.

The systemd user timer runs at **02:39 Asia/Jerusalem**, including DST changes. A
five-minute retry trigger handles active sessions and temporary failures. After one
successful check/install, further retries are quiet until the next day's window.
Tailscale is needed for administration, not for unattended HTTPS downloads.

Chasing, pending/running lookback, unclosed live positions (including exit alerts
awaiting “I've exited”), and pending chase deliveries postpone installation. A
separate Postgres advisory lock coordinates all chase transactions with the final
idle check and graceful stop. Unknown states or unavailable schema fail closed.
No active work is cancelled to force an update.

Downloads are commit-pinned, bounded, traversal/link checked, and matched against
GitHub blob hashes. Runtime dependency, SQL migration, or update-policy changes
require a manual cutover. Only runtime source files are replaced. Database records,
credentials, user settings, routes and the existing venv stay in place. Backups are
private siblings of the installation under `trading-source-backups`.

## Bootstrap and operations

Use the existing worker's venv and dedicated database credentials. Place the two
`trading-bot-source-update` example units from `scripts/` under the existing user's
systemd directory, replacing all absolute placeholders. Verify them with
`systemd-analyze --user verify`, then reload systemd. Before enabling the timer,
run the reviewed staged updater with `--root EXISTING_INSTALL --bootstrap`. Bootstrap
also stops and rechecks activity, supporting an older worker without the new lock.
It preserves all environment flags. Never launch a scanner to test the updater.

The updater's `--check` option reads eligibility and blockers without installing
anything. Bootstrap does not bypass CI or activity checks. Normal operation requires
an existing `.source-deployment.json` record; it never guesses a missing baseline.

Enable `trading-bot-source-update.timer` only after bootstrap health/version checks
pass. Inspect `systemctl --user list-timers` and `journalctl --user -u
trading-bot-source-update.service`. Sanitized status is stored under
`~/.local/state/trading-bot-updater/status.json`. Database/API exception details are
not logged because they can contain credentials. Failed startup restores the backed
up source and verifies the old version; a failed rollback requires operator action.

Automatic updates require the same dependency, SQL and policy Git blob hashes as
the installed baseline. For a deliberate compatible migration, perform the reviewed
manual cutover and refresh that baseline using a supervised bootstrap. The updater
never applies SQL or installs Python packages on its own.
