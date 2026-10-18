# Changelog

## Unreleased — 2026-10-18

### Local development settings

- Settings & versions now saves development Telegram delivery and cloud sync overrides locally, independently of browser storage and cloud settings.
- Changes apply to subsequent worker requests without restarting. Saved preferences take precedence over legacy environment defaults and survive browser closure and app restarts.
- Development isolation, credential storage, production behavior and previously processed dry-run alerts are preserved.

### Forex weekend alerts

- Forex scans pause from Friday 17:00 until Sunday 17:00 New York time across all candle timeframes, including catch-up scans. The schedule follows New York daylight-saving changes.
- Provider candles wholly inside the weekend closure are excluded before caching and signal detection, including after reopening. Partial session candles remain eligible.

### Daily Linux executable updates

- The Linux app's background process checks for stable executable updates daily at 05:00 Asia/Jerusalem, including daylight-saving changes. No browser page needs to be open. If the running app resumes after missing the deadline, it checks once on resume.
- A newer verified package uses the existing graceful shutdown, detached installer, executable replacement and restart. External credentials, data locations and launch settings are preserved. Failed checks keep the installed app running.
- Manual **Check for updates** still wakes the process immediately. Windows retains startup and hourly checks. The dashboard now displays the platform's actual schedule.
- Linux temporarily enables verbose production activity output and retains stdout/stderr in `logs/runtime.log` under the application directory. Logs rotate at 5 MiB with three backups. Update download progress, checksum verification, installer actions and restarted-process startup are recorded there.
- The detached Linux installer redirects standard input away from the terminal and stdout/stderr to the persistent log before stopping the old process. Terminal-launched updates keep working if the terminal or its `tee` pipeline closes. Existing launch settings, including disabled paper trading, carry into the restarted process.

### Scanner database contention

- Cloud sync serializes records outside write transactions and queues them in batches of at most 100 records, keeping the SQLite writer available between batches. Candle/report reads use a separate read-only connection.
- Scanner workers retry temporary database locks without stopping their heartbeat. Persisted scan state and catch-up requests are reused.
- Activity logging falls back to the launch terminal when SQLite is busy, preventing an error-reporting failure from killing another worker. Cloud and paper workers also survive temporary locks while reporting failures.
- Disabled paper trading no longer runs a background database polling loop. Set `PAPER_TRADING_ENABLED=false` in the existing local credentials file and restart. Existing broker orders and positions are unchanged.

## Unreleased — 2026-10-17

### Automatic executable releases

- Relevant scanner and local UI pushes to `main` now allocate a newer numeric version and publish an update after both Linux and Windows builds pass.
- Both platforms publish together; failed builds leave the previous stable release available. Feature branches produce artifacts only.
- Existing installed updaters continue using numeric release versions; commit fingerprints remain a separate source comparison. Build version changes do not alter source fingerprints.

### S&P 500 scanner and historical data

- Added FMP cash-index provider for S&P 500 (`SPX`, exact symbol `^GSPC`).
- Added daily scanning and complete weekly/monthly candles aggregated from daily OHLC. Daily candles close conservatively at midnight UTC after the session date.
- Added historical pagination beyond FMP's 5,000-row response limit, CSV export, candle caching, and FMP selection in local historical backtests.
- Existing saved configurations need verified activation; changing bundled defaults alone does not replace saved assets. The activation script backs up SQLite before enabling SPX.
- DAX and intraday index candles remain unavailable under the tested subscription. Subscription access can differ between installations.

### Required local setup

1. Stop your local scanner before editing its settings. Keep your existing `.env`; do not replace it with `.env.example`.
2. Open the credentials file used by that installation:
   - Source checkout: `.env` beside `platform_app.py`.
   - Linux standalone: `${XDG_CONFIG_HOME:-$HOME/.config}/FamilyTradingBot/.env`.
   - Windows standalone: `%LOCALAPPDATA%\FamilyTradingBot\.env`.
   - A configured `TRADING_BOT_ENV_FILE` takes precedence where supported.
3. Add or update one entry locally, replacing the placeholder with your own FMP API key:
   ```dotenv
   FMP_API_KEY=YOUR_OWN_FMP_API_KEY
   ```
4. On macOS/Linux, restrict the actual credentials file to your user with `chmod 600 /absolute/path/to/.env`. On Windows, keep it inside your private user profile and restrict access through file Properties → Security if the folder is shared.
5. Restart the scanner to load the key. An existing `FMP_API_KEY` process environment variable takes precedence over the file; update that value too if you configured one.
6. For an existing source installation, run `.venv/bin/python scripts/activate-fmp.py --database /absolute/path/to/signals.sqlite3`. Select the database used by your installation; this verifies history, backs up SQLite, enables SPX daily/weekly/monthly, and exports history under `data/historical-fmp/`. In a standalone installation, select FMP cash indices for SPX in Assets, symbol `^GSPC`, exchange `INDEX`, market `index`, daily/weekly/monthly only, confirm the feed, and enable it. New feeds establish a baseline before sending new alerts.

### Credential handling

The key belongs only in the local credentials file or process environment. The example file contains an empty `FMP_API_KEY`; releases bundle that template, never the populated `.env`. Updates preserve existing credentials. Never paste a real key into Assets, the online dashboard, release notes, screenshots, chat, or Git. The browser has no key input and does not receive the FMP key. Provider errors use fixed messages without response bodies or authenticated URLs. If a key is exposed, replace it through your FMP account and update the local file.

The settings dialog includes a bundled **Changelog & FMP setup** page, available offline. This entry documents source changes; it does not mean an executable release has been published.
