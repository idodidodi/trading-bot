# Family Trading Bot on Windows

Download [FamilyTradingBot.exe](https://github.com/idodidodi/trading-bot/releases/latest/download/FamilyTradingBot.exe) and double-click it. Python is included. Windows 10/11 x64 is supported. The app opens its local dashboard in your browser.

On first launch it installs its executable under `%LOCALAPPDATA%\FamilyTradingBot\bin`. Settings are in `%LOCALAPPDATA%\FamilyTradingBot\.env`; the database and historical candle files are under `%LOCALAPPDATA%\FamilyTradingBot\data`.

First launch uses **dry run**. Edit `.env` with your existing data-provider and Telegram settings, then set `DRY_RUN=false` and restart the app. Keep credentials out of GitHub and browser settings. Closing the browser leaves scanning running; closing the scanner console stops it. Keep the computer awake for scans and notifications.

To move an existing installation, stop its scanner, copy its `.env` and `data` folder into this Windows folder, and then start the executable. Remove the old `SSL_CERT_FILE` path. Run one scanner per installation to prevent duplicate alerts. Historical backtests require your historical candle files; private data and credentials are never included in the executable.

The app checks GitHub releases on startup and **every hour**. Overview's **Check for updates** button checks immediately. A newer stable release downloads, verifies its SHA-256 checksum and size, gracefully stops the worker, replaces the executable and restarts. If replacement fails, it restores the previous executable. Settings, SQLite state, candle history, scan deadlines and pending notifications are preserved. If internet/update checks fail, the installed version continues running. An administrator account is not needed.

The first release is unsigned by a Windows code-signing certificate. Windows may display its usual unknown-publisher prompt. No certificate or private provider credential is bundled.

For developers: run `scripts/build-windows.ps1` on Windows. GitHub's **Windows standalone release** workflow also builds, runs the Python tests, smoke-tests the frozen app and publishes the executable plus `windows-update.json`. Future releases change `app_version.VERSION` and push the matching `vX.Y.Z` tag. Build dependencies are in `windows-build-requirements.txt`.

For an existing Ubuntu scanner: pull the updated repository, then restart `trading-bot.service`. Its one-time migration preserves existing assets and creates a database backup before adding the requested portfolio.
