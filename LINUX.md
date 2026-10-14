# Family Trading Bot on Linux

Download [FamilyTradingBot-linux-x86_64](https://github.com/idodidodi/trading-bot/releases/latest/download/FamilyTradingBot-linux-x86_64). This standalone executable includes Python. It targets **Ubuntu 22.04 or newer, x86_64**, and compatible glibc distributions (glibc 2.35+). It is not an ARM/Raspberry Pi or Alpine/musl build.

After downloading, run these commands in the download folder:

```sh
chmod +x FamilyTradingBot-linux-x86_64
./FamilyTradingBot-linux-x86_64
```

The app installs its managed executable in `~/.local/share/FamilyTradingBot/bin/FamilyTradingBot` and opens http://127.0.0.1:8080 in the default browser when a desktop session is available. On a headless computer it starts the scanner without opening a browser. Keep it running in a terminal; close it with Ctrl+C. Running the downloaded executable again opens the existing managed installation.

Settings are in `~/.local/share/FamilyTradingBot/.env`; SQLite state and candle history are in its `data` directory. If `XDG_DATA_HOME` is an absolute path, the app uses `$XDG_DATA_HOME/FamilyTradingBot` instead. No administrator rights are needed.

First launch uses **dry run**. Add your existing provider and Telegram settings to `.env`, set `DRY_RUN=false`, stop the app and restart. For migration, stop the old scanner, copy its `.env` and `data` directory into the persistent folder, and remove old machine-specific `SSL_CERT_FILE` settings. Run one scanner per installation to avoid duplicate alerts.

The app checks stable GitHub releases at startup and **every hour**. The Overview **Check for updates** button checks immediately. Downloads must match the release manifest's size, SHA-256 checksum, Linux platform and x86_64 ELF header. After a verified download the app stops gracefully, atomically replaces its managed executable and restarts. It retains the previous executable as `FamilyTradingBot.previous`; settings and history stay outside the binary. Failed update checks leave the current version running. Replacement failures restore the previous executable; startup failures can be recovered manually using the retained backup.

The executable extracts bundled files into a temporary directory at launch. That directory must permit execution; if `/tmp` is mounted `noexec`, set `TMPDIR` to an executable directory you own before starting the app. For an existing systemd service, stop the Python service before launching this executable. Installing it does not modify your existing service.

Developers: run `bash scripts/build-linux.sh` on Linux x86_64. The **Linux standalone release** GitHub workflow builds on Ubuntu 22.04, tests the scanner and Linux updater, and checks the frozen app's startup and persistence on relevant branch pushes, version tags, or manual runs. Branch builds are downloadable Actions artifacts; version tags publish the executable plus `linux-x86_64-update.json`. See [executable build and release rules](EXECUTABLE_RELEASES.md). Release assets contain no local credentials, databases or private candle files.
