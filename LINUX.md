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

The app checks stable GitHub releases at startup and **every hour**. The Overview **Check for updates** button checks immediately. Downloads must match the release manifest's size, SHA-256 checksum, Linux platform and x86_64 ELF header. After a verified download the app stops gracefully, atomically replaces its managed executable and restarts. It retains the previous executable as `FamilyTradingBot.previous`; settings and history stay outside the binary. Failed update checks leave the current version running. Replacement failures restore the previous executable; startup failures can be recovered manually using the retained backup.

The executable extracts bundled files into a temporary directory at launch. That directory must permit execution; if `/tmp` is mounted `noexec`, set `TMPDIR` to an executable directory you own before starting the app. For an existing systemd service, stop the Python service before launching this executable. Installing it does not modify your existing service.

Developers: run `bash scripts/build-linux.sh` on Linux x86_64. The **Linux standalone release** GitHub workflow builds on Ubuntu 22.04, tests the scanner and Linux updater, and checks the frozen app's startup and persistence on relevant branch pushes, version tags, or manual runs. Branch builds are downloadable Actions artifacts; version tags publish the executable plus `linux-x86_64-update.json`. See [executable build and release rules](EXECUTABLE_RELEASES.md). Release assets contain no local credentials, databases or private candle files.

In version 1.0.6, open the gear wheel → **Connection tests** to use **Test Telegram** or **Sync demo signal to cloud**. The first sends one labeled test message to the chat configured in `.env`. The second sends one `DEMO-CLOUD-SYNC` finding directly to the configured cloud database and confirms the database acknowledged it. It does not enqueue a local signal or create a paper order. These manual tests work even when automatic Telegram delivery or cloud sync is disabled. Results appear inside the settings dialog; the hosted dashboard does not expose these local credential tests.

## FMP update setup

The S&P 500 provider needs your own `FMP_API_KEY` in the existing local credentials file described above. Updates preserve that file; do not overwrite it with `.env.example`. Restart after editing. See [changelog and secure setup instructions](CHANGELOG.md), or open **Settings → Changelog & FMP setup** in the local dashboard.
