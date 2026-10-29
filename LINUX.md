> This migration branch uses Postgres as the only runtime database. Follow the [manual migration runbook](POSTGRES_MIGRATION.md) before installation. Instructions below describe the preserved SQLite release where they conflict.

# Family Trading Bot on Linux

Download [FamilyTradingBot-linux-x86_64](https://github.com/idodidodi/trading-bot/releases/latest/download/FamilyTradingBot-linux-x86_64). This standalone executable includes Python. It targets **Ubuntu 22.04 or newer, x86_64**, and compatible glibc distributions (glibc 2.35+). It is not an ARM/Raspberry Pi or Alpine/musl build.

After downloading, run these commands in the download folder:

```sh
chmod +x FamilyTradingBot-linux-x86_64
./FamilyTradingBot-linux-x86_64
```

The app installs its managed executable in `~/.local/share/FamilyTradingBot/bin/FamilyTradingBot` and opens http://127.0.0.1:8080 in the default browser when a desktop session is available. On a headless computer it starts the scanner without opening a browser. Keep it running in a terminal; close it with Ctrl+C. Running the downloaded executable again opens the existing managed installation.

To manually upgrade an existing installation with a branch-build executable, stop the running app first, then replace the managed binary and launch it:

```sh
BOT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/FamilyTradingBot"
mkdir -p "$BOT_DIR/bin"
install -m 700 ./FamilyTradingBot-linux-x86_64 "$BOT_DIR/bin/FamilyTradingBot"
"$BOT_DIR/bin/FamilyTradingBot"
```

Secrets are read from `~/.config/FamilyTradingBot/.env`, outside the program directory. SQLite state and candle history are stored in `~/.local/share/FamilyTradingBot/data`. Absolute `XDG_CONFIG_HOME` and `XDG_DATA_HOME` values replace `~/.config` and `~/.local/share` respectively. No administrator rights are needed.

Before first launch, you can copy your existing credentials into the standard secrets location:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/FamilyTradingBot"
chmod 700 "${XDG_CONFIG_HOME:-$HOME/.config}/FamilyTradingBot"
cp /path/to/existing/.env "${XDG_CONFIG_HOME:-$HOME/.config}/FamilyTradingBot/.env"
chmod 600 "${XDG_CONFIG_HOME:-$HOME/.config}/FamilyTradingBot/.env"
```

To use other external locations, set an existing secrets file and a data directory when launching:

```sh
TRADING_BOT_ENV_FILE=/absolute/path/to/secrets/.env \
DATA_DIR=/absolute/path/to/data \
./FamilyTradingBot-linux-x86_64
```

`DATA_DIR=/absolute/path/to/data` can also be placed inside `.env`. Process environment values take precedence over values in the file. Custom paths must be absolute; `~` is expanded in these two overrides. An explicitly selected missing secrets file is an error. Data directories are created as needed. Restart after changing settings. Use the same launch overrides on subsequent manual launches; automatic updates inherit them.

With no existing credentials file, first launch creates one with **dry run** enabled and owner-only permissions. Add your provider and Telegram settings and set `DRY_RUN=false` when ready to send alerts. On upgrade, the previous `~/.local/share/FamilyTradingBot/.env` is copied into the standard secrets folder only if no new file exists; the old copy is retained for rollback and is no longer read. Existing data stays in place. For migration from a source installation, stop the old scanner, copy its `.env` into the secrets folder and its `data` directory into the data location, and remove old machine-specific `SSL_CERT_FILE` settings. Run one scanner per installation to avoid duplicate alerts. Updates preserve external secrets and data.

The app's background process checks stable GitHub releases **daily at 05:00 Israel time (Asia/Jerusalem)**, independently of the browser and the computer's timezone. Keep the app running; a closed browser does not stop updates. If the running app resumes after missing the deadline, it checks once on resume. The Overview **Check for updates** button wakes the same process immediately. Downloads must match the release manifest's size, SHA-256 checksum, Linux platform and x86_64 ELF header. After a verified download the app stops gracefully, atomically replaces its managed executable and restarts. It retains the previous executable as `FamilyTradingBot.previous`; settings and history stay outside the binary. Failed update checks leave the current version running until the next daily or manual check. Replacement failures restore the previous executable; startup failures can be recovered manually using the retained backup.

The executable extracts bundled files into a temporary directory at launch. That directory must permit execution; if `/tmp` is mounted `noexec`, set `TMPDIR` to an executable directory you own before starting the app. For an existing systemd service, stop the Python service before launching this executable. Installing it does not modify your existing service.

While this version is being stabilized, Linux logs production activity and tracebacks verbosely to the launch terminal and to `${XDG_DATA_HOME:-$HOME/.local/share}/FamilyTradingBot/logs/runtime.log`. The log rotates at 5 MiB with three backups. Automatic update installers and restarted processes write to the same persistent log without depending on the old terminal or `tee` pipeline. To follow output continuously across restarts:

```sh
tail -F "${XDG_DATA_HOME:-$HOME/.local/share}/FamilyTradingBot/logs/runtime.log"
```

Update logs include checks, download progress, verification, replacement and restart startup. They do not dump credentials or process environment values. Once verbose activity output is no longer needed, set `TRADING_BOT_VERBOSE_LOGS=false` in the existing `.env` and restart; updater diagnostics and errors remain captured.

Developers: run `bash scripts/build-linux.sh` on Linux x86_64. GitHub’s **Build and publish standalone update** workflow tests and builds both platforms. Relevant pushes to `main` allocate a new numeric version and publish it after both executables pass their smoke tests. Feature branches produce downloadable Actions artifacts only. See [executable build and release rules](EXECUTABLE_RELEASES.md). Release assets contain no local credentials, databases or private candle files.

In version 1.0.6, open the gear wheel → **Connection tests** to use **Test Telegram** or **Sync demo signal to cloud**. The first sends one labeled test message to the chat configured in `.env`. The second sends one `DEMO-CLOUD-SYNC` finding directly to the configured cloud database and confirms the database acknowledged it. It does not enqueue a local signal or create a paper order. These manual tests work even when automatic Telegram delivery or cloud sync is disabled. Results appear inside the settings dialog; the hosted dashboard does not expose these local credential tests.

## FMP update setup

The S&P 500 provider needs your own `FMP_API_KEY` in the existing local credentials file described above. Updates preserve that file; do not overwrite it with `.env.example`. Restart after editing. See [changelog and secure setup instructions](CHANGELOG.md), or open **Settings → Changelog & FMP setup** in the local dashboard.

## Investigating missing heartbeats or growing memory

Open http://127.0.0.1:8080/logs on the Linux computer, select **All events**, and click **Refresh logs**. Heartbeats are due every hour at HH:15 Israel time, including while the scanner waits. Save the last heartbeat and the following entries, and check the launch terminal for `Exception in thread` messages. A live dashboard alone does not establish that the scanner thread is still running.

In a terminal, `ps -eo pid,ppid,rss,etime,comm --sort=-rss | head -15` shows the largest processes; RSS is current resident memory in KiB. Record whether the large process is FamilyTradingBot or the browser before restarting. If launched under systemd, also inspect that service's journal for worker exceptions or out-of-memory termination.

Updated source retries heartbeat writes after SQLite errors, reports scanner failures in status/logs, and includes current process RSS and thread count in Linux heartbeats. Cloud sync streams saved backtest reports one at a time to avoid a memory peak proportional to all saved runs. Standalone installations need a rebuilt executable to receive these changes.

If a terminal traceback reports `sqlite3.OperationalError: database is locked`, use the executable update with bounded sync transactions and scanner retries. A browser dashboard can remain accessible after an older scanner worker exits. Do not delete the database, its WAL file, or scan state as a workaround.

To pause the paper executor, open **Settings & versions → Paper trading**, uncheck **Enable Alpaca paper trading**, and save. The choice is stored locally and applies without restarting; an in-progress cycle may finish. Disabling stops local automation without cancelling existing broker orders or closing positions. `PAPER_TRADING_ENABLED` in `.env` provides only the initial default until the setting is saved. The disabled worker checks the local setting so it can be enabled again without restarting; it does not poll Alpaca.
