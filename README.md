# Independent local trading-signal platform

**Market-data provider → our candle scanner → local dashboard + Telegram.** TradingView is not required. Python 3.10+; no third-party Python dependencies. The computer running the app must stay online for live polling and Telegram delivery.

The scanner reads numeric defaults from [the Markdown skill](skills/rsi-divergence/SKILL.md): RSI **3 on closes**, Wilder smoothing; Bollinger Bands **20 / 2**, population deviation; strict **two-left/one-right pivots**; consecutive pivot spacing **5–60**. It calculates indicators from OHLC data locally. Bullish divergence requires a lower price low, higher RSI, and a lower-band touch at the second low; bearish is reversed. Wicks count. Live alerts wait for **one following candle to close** and confirm the second pivot. The band touch and RSI divergence are evaluated at the pivot itself; the following candle must have a strictly lower high (bearish) or higher low (bullish). Backtests and newly generated Pine use the same rule; saved historical findings retain their original rules. Signals only, with no order execution or trade levels.

## Data-provider recommendation

**Twelve Data** supplies candle feeds through `/time_series`. The permanent forex set is EUR/USD, USD/JPY, USD/CNY, GBP/USD and USD/CHF, based on the [BIS 2025 turnover survey](https://www.bis.org/publications/202509-commentary-otc-derivatives). The permanent crypto set is BTC/USD, ETH/USD, SOL/USD, NEAR/USD and DOGE/USD (Binance feeds). The requested NEAR and DOGE are included within the five permanent crypto slots; this is a chosen core, not a claim that all five lead global volume. Existing NVDA and unavailable DAX/dollar-index placeholders are retained.

At **08:00 Asia/Jerusalem** each day, the worker selects five additional forex pairs and five additional crypto pairs. Ranking uses **Kraken venue-specific trailing 24-hour USD turnover**, not global forex volume. Base volume is multiplied by the 24-hour VWAP; non-USD forex quote turnover is converted at the current Kraken USD rate. Only online Kraken pairs with a matching Twelve Data catalogue feed qualify. Crypto must have an exact USD quote and Binance candle coverage; fiat and known stablecoin bases are excluded. USD is never substituted with USDT. Ranking venue and candle venue differ and are shown in Assets.

The selection is saved in SQLite and stays fixed until the next 08:00 boundary, including across restarts. Startup catches up if the morning refresh was missed. Permanent/configured assets are excluded from the rotating set, including disabled entries, so daily selection cannot bypass a manual disable. If ranking fails or either market has fewer than five eligible pairs, daily additions are unavailable and only permanent/configured assets scan; the next scan retries. Old selections are not reused for a new day. The computer and worker must remain running for scheduled refreshes. Daily selections appear in the local Assets page and activity logs; provider entitlement failures remain visible in scanner coverage.

Candle polling waits **four hours** after each scan and wakes earlier for the morning refresh. With 21 live assets and four timeframes, six regular cycles use 504 Twelve Data requests/day, plus a possible morning cycle and restarts. Requests remain paced at least eight seconds apart. This is sized for the installation's existing 800/day, 8/minute budget; other API consumers share the quota. Credentials stay in local `.env` as `TWELVE_DATA_API_KEY`. Catalogue presence does not guarantee account entitlement or sufficient candle history.

## Start locally

```sh
cp .env.example .env
# Edit .env: DRY_RUN=true for initial local testing.
python3 platform_app.py
```

Open `http://127.0.0.1:8080` on the computer running the app. No public port, webhook secret, HTTPS tunnel, or TradingView alert is needed in the default `MODE=scanner`. The local dashboard shows every configured asset/timeframe's coverage status on Overview and received signals on the Signals tab. Missing data is shown as unavailable, not as no signal. It shows the latest closed-candle timestamp; a successful scan alone does not guarantee a feed is current.

## Configure assets and timeframes

Use **Assets** to edit an existing installation. `scanner.json` seeds configuration only on the first start; subsequent runs use managed configuration in SQLite, so editing that file alone does not update an existing installation. The permanent and daily asset sets are described above; timeframes are `monthly`, `weekly`, `daily`, and `4h`. Restart after changing the skill.

The supplied configuration uses Twelve Data for the permanent live feeds and keeps CSV placeholders for the unavailable indices. A valid `TWELVE_DATA_API_KEY` in `.env` is required. An example live feed entry is:

```json
{
  "id": "NVDA",
  "provider": "twelvedata",
  "symbol": "NVDA",
  "exchange": "NASDAQ",
  "feed_confirmed": true
}
```

`feed_confirmed` is your explicit confirmation of the symbol/feed choice and entitlement, not a guarantee of connectivity. The adapter also rejects a returned symbol/interval/exchange that differs from the request. Do not guess symbols for DAX or the dollar index; resolve them with the provider first. Configure an optional `max_data_age_seconds` per asset to enforce an appropriate freshness limit, allowing for market closures. Use `continuous: true` only for a verified continuously traded feed with no scheduled session closures; it rejects missing buckets.

The adapter requests native timeframes and UTC timestamps, including up to 500 candles. It uses a **conservative calendar cutoff** for completeness: four hours, the next UTC calendar day, seven days, or the next calendar month. For session-based markets, this can delay detection beyond the actual closing bell. It is not an exchange-session calendar implementation. Verify this convention with your chosen provider before relying on timing-sensitive alerts. For repeated timestamps in an ordered Twelve Data response, the adapter keeps the last returned version. If the next native bar begins before the nominal end, it uses that next start as the close boundary. Both adjustments are disclosed in coverage Details; the strict CSV duplicate/overlap checks remain unchanged. No OHLC prices are interpolated. Provider boundary and revision conventions can affect indicator results.

## Candle-file mode

CSV files must use exactly these column names, with explicit timezone-aware start/end timestamps:

```csv
timestamp,closed_at,open,high,low,close
2026-09-01T00:00:00Z,2026-09-01T04:00:00Z,100,104,98,102
```

Use complete candles from a known feed, not synthetic charts. File paths in the default config are `data/candles/EURUSD-4h.csv`, etc. Rows are sorted, duplicates/overlaps and invalid OHLC are rejected, and unfinished candles are excluded. `closed_at` must reflect the source's actual bucket end. Other software can refresh these files between scans using an atomic rename.

```sh
python3 scanner.py --once
```

This evaluates the configured files/providers once and prints coverage, without running Telegram delivery. It uses the same SQLite state as the app. Use a separate `DATA_DIR` for experiments if you don't want to affect the normal scanner's baseline.

## Telegram bot and destination

1. Open verified **@BotFather**: https://t.me/BotFather. Send `/newbot`, choose a name and unique username ending in `bot`.
2. Put its bot token in local `.env` as `TELEGRAM_BOT_TOKEN`. Keep it private; revoke an exposed token through BotFather.
3. Open your new bot and press **Start**. For a group, create one, add the bot, and send `/start@YourBotUsername` inside it. Allow the bot to send messages. For a channel, make the bot an administrator allowed to post.
4. Run `python3 telegram_setup.py chats`. Put the desired chat ID in `.env` as `TELEGRAM_CHAT_ID`, including any leading minus sign. If no chat appears, send another command and retry. Use a dedicated new bot if another service already uses its Telegram webhooks.
5. Run `python3 telegram_setup.py test` to explicitly send a connection test.
6. Set `DRY_RUN=false` and restart the app for live delivery.

The `.env` file is ignored by Git. Its format is plain `KEY=value` without quotes; existing process environment variables take precedence. Official Telegram tutorial: https://core.telegram.org/bots/tutorial.

If Telegram reports a TLS certificate verification failure, check which interpreter your terminal uses with `python3 -c "import sys; print(sys.executable)"`. On macOS, a python.org installation can have a different CA certificate setup from Homebrew Python or the VS Code project environment. If the configured project virtual environment connects successfully, run `.venv/bin/python telegram_setup.py chats` or `.venv/bin/python telegram_setup.py test` instead. To keep using the other interpreter, repair its CA certificate configuration (python.org provides an `Install Certificates.command` in its Python application folder); managed networks may also require an administrator-provided trusted CA. Never disable TLS verification.

## Alert timing, persistence, and validation

The first successful scan of a feed/timeframe establishes a baseline and does **not** send historical signals. Subsequent scans emit signals confirmed after the last observed candle; overlapping polls and restarts do not repeat the same pivot pair. Changing the provider identity, skill, or live alert timing establishes a new baseline. Telegram reports the actual confirmation close in Israel time and the number of following candles required. Existing provisional findings retain their original labels. The worker wakes 15 seconds after the next known candle close (or sooner for its configured polling interval), with a 30-second minimum wait between scans. Delivery occurs on the next successful provider poll after that close, subject to provider availability, request pacing, and Telegram retries. Candle history should ideally have 250+ bars. The minimum with current rules is 64; smaller datasets are insufficient, and 64–249 bars are labeled limited history. This avoids requiring more than 20 years of monthly data for recently listed instruments.

SQLite state and the delivery queue live in `data/signals.sqlite3`. Offline catch-up is limited to the history available from your provider (up to 500 bars); a gap exceeding that window is reported as unavailable and requires backfill. Telegram failures retry up to once every five minutes and survive restarts. One app process per database. Delivery is at-least-once: a crash after Telegram accepts a message but before recording success may repeat a message. `DRY_RUN=true` records delivery as simulated and never sends those signals later.

```sh
python3 -m unittest -v
```

Tests exercise RSI seeding, population BB, real OHLC bullish/bearish divergence, strict pivot equality, delayed confirmation, band rejection, malformed/open candles, first-run baselines, subsequent signals, persistent deduplication, webhook compatibility, and simulated Telegram retries. These tests do not prove live data-provider coverage or Telegram connectivity. Live scanning requires a selected feed, its credentials/entitlement, and network access. Index CSV placeholders require a supported live feed or externally refreshed candle files.

Legacy `generate_pine.py`, `tradingview_template.pine`, and `MODE=webhook` remain available for compatibility. They are not used by the independent scanner.

## Logs and Backtest 2020

Restart the updated app to show the Overview, Logs, and Backtest 2020 navigation links. Logs record startup, scan cycles, market checks, and delivery attempts with Israel local timestamps (Asia/Jerusalem), automatically switching between UTC+3 during daylight saving and UTC+2 in winter. The page shows the latest 200 events. Logging begins after this update; earlier activity cannot be reconstructed.

Run `.venv/bin/python backtest.py` to replay historical CSV files configured in `scanner.json`. Include enough pre-2020 candles for indicator warm-up. This uses the same detection engine and only lists signals confirmed from January 1, 2020 through December 31, 2020 UTC. It does not enqueue live alerts or send Telegram messages. The latest report appears in the Backtest 2020 tab. Missing CSVs appear as unavailable; short history is labeled partial. Calendar range checks do not verify exchange sessions or missing trading days. Provider-mode feeds need historical CSV data; the live 500-bar adapter cannot supply a complete 2020 replay.

Tests use temporary databases. The update preserves existing settings and data; restarting adds activity logging, and running the replay adds a report table to the existing database.

### Preparing public historical CSVs

`prepare_history.py` writes CSVs, a separate replay configuration, source metadata, and a replay report into `data/historical-2020/`. Live candle files, `.env`, `scanner.json`, and the live database are preserved. The Backtest 2020 tab displays this saved report if no database replay exists.

Crypto uses Kraken's official OHLCVT archive, retrieving only selected CSV members via HTTP byte ranges. BTC/USD has native daily and 4h candles; weekly/monthly are aggregated from daily candles. Kraken NEAR/USD starts in June 2022 and cannot supply a 2020 replay.

No public historical download was located for Colmex forex or Colmex Pro stocks; the user-approved Yahoo fallback uses EURUSD=X, ^GDAXI (DAX cash), DX-Y.NYB, and NVDA. Daily, weekly, and monthly files are supplied. Non-crypto 4h remains unavailable. Yahoo EUR/USD contains 61 malformed source rows; they are recorded and excluded, and results are marked partial. Data gaps can change indicators and pivots. Provider series and calendar bucket conventions may differ from TradingView or broker charts.

`manifest.json` records sources, hashes, and warm-up counts; raw source files are cached for reproducibility. `report.json` was generated using a temporary database. Rebuild everything without writing to the live database:

```sh
.venv/bin/python prepare_history.py
```

To explicitly save the historical replay into the dashboard database:

```sh
SCANNER_CONFIG=data/historical-2020/backtest-config.json .venv/bin/python backtest.py
```

### Finding quality feedback

The last column of the backtest findings and live signals tables is labeled **Feedback**, with five clickable star boxes and a comment. Star ratings save immediately; comments save automatically after a short typing pause or when leaving the field. Comments can be saved without a rating. A small status shows saving, saved locally, or a save failure; temporary failures retry automatically. Restart the updated app to load the controls and create the additive feedback tables in `data/signals.sqlite3`. No strategy settings change when saving feedback.

Feedback is stored in SQLite, survives restarts, and is tied to the complete finding evidence and rule version. Identical backtest findings retain feedback across reruns and when the saved 2020 report is later imported into the database. Findings with changed evidence or rules get separate feedback. Each edit retains an immutable history entry for later strategy analysis, even when a finding no longer appears in the current view. Stale saves from another browser tab are rejected; reload to see its latest feedback before editing again.

The **Export feedback history** link (`/api/feedback/export`) downloads JSON containing ratings, comments, revisions, timestamps and original finding evidence/rules. This supports later strategy adjustments and import into online storage. The endpoint is available only on the local dashboard, never the legacy public webhook port.

**Online storage is not configured or deployed yet.** The UI explicitly reports that saves are local. The online deployment must support authenticated feedback writes and bidirectional sync, as specified in `DEPLOYMENT_PLAN.md`; a local save or export does not constitute an online backup.

## Manage live assets and view signal candles

Restart the local app, then open **Assets**. Bulk-add comma-separated tickers, select each asset's candle timeframes independently (monthly, weekly, daily and 4h by default), and configure the exact data-provider symbol and optional exchange-qualified TradingView symbol. New tickers start as disabled drafts. Enable them after arranging a CSV feed or confirming Twelve Data coverage. The data adapters remain CSV and Twelve Data; adding a ticker does not itself fetch a new provider's data. Save applies at the next scan-cycle boundary without a restart. Disabling removes an asset from future scans while preserving historical findings and feedback. `scanner.json` remains unchanged; managed configuration is stored additively in SQLite.

Both live and backtest rows have **View candles** links. The chart opens the second pivot by default and offers first-pivot and confirmation choices. Confirmation uses the candle's recorded close rather than guessing its start. Source candles are loaded in a bounded window and the chart module loads only when opened. A verified TradingView mapping adds an external chart link; exact-date navigation on the public TradingView site is not guaranteed. Historical backtest charts use the stored CSV data, and live charts use validated candles cached by scans after this update.

### Online UI setup

See [cloud/README.md](cloud/README.md) for new Supabase and Cloudflare Pages setup. Publish only the static `web/` bundle and routing file. The browser can edit owner-scoped assets and feedback, and the local worker uploads historical candles, findings, logs and reports. Set optional `CLOUD_SYNC_URL` and `CLOUD_SYNC_TOKEN` locally to enable sync; by default no upload occurs. Credentials, `.env`, local data and scanner code are not published.

The online integration ships as setup files; no real Supabase/Cloudflare project has been created or deployed by this task. Sync conflicts are retained and reported for resolution; ordinary pending data stays on disk during outages. SQL and local APIs are tested against isolated databases. Low-memory design uses one on-demand chart, bounded page responses and bounded upload batches; full 24-hour memory targets from the plan remain to be measured.

### Daily selection installation

`daily_universe.py` implements the schedule and volume ranking. `scanner.json` seeds it for new installations. For an existing installation, the explicit `daily_universe.install(store)` operation creates a managed configuration revision while preserving unrelated assets; do not run it on every startup. Daily snapshots are in the additive `daily_universe` table. Existing findings and feedback remain intact. Rotating assets do not emit catch-up signals confirmed before the current selection boundary.

## Install on a small Ubuntu PC for 24/7 operation

Use **`./scripts/install-ubuntu.sh`** from the repository checkout. Python 3.10+ and systemd are the only runtime requirements; the app uses the Python standard library and serves the shared browser app itself. There is no Node process, Docker container, browser or database server on the worker PC.

1. Copy or clone this repository onto the Ubuntu PC, for example into `~/trading-bot`. Install Python if needed: `sudo apt update && sudo apt install python3 ca-certificates git tzdata`.
2. Run `./scripts/install-ubuntu.sh`. If `.env` is missing, it creates a private example and stops so you can configure it. Set the provider and Telegram credentials, optional cloud-sync connection and initially `DRY_RUN=true`.
3. To preserve configuration, deduplication and feedback, stop the existing worker before copying `data/` and `.env` securely to the new PC. Include `data/historical-2020/` for local reruns. Alternatively, start with fresh state: the first scan establishes a baseline. Remove any Mac-specific `SSL_CERT_FILE` path from the copied environment; Ubuntu uses its system CA certificates. Run only one live worker for an installation to avoid duplicate Telegram alerts.
4. Run `./scripts/install-ubuntu.sh` again. It installs and enables `trading-bot.service`, starts it immediately, and restarts it after failure. Its memory limits are 192 MB soft / 384 MB hard, with low CPU scheduling weight and bounded request concurrency.
5. Visit `http://127.0.0.1:8080` on that PC. From another PC, use `ssh -L 8080:127.0.0.1:8080 USER@UBUNTU_PC`, then open the same URL on your own PC. The online UI remains available through Cloudflare.

Check operation with `systemctl status trading-bot` and `journalctl -u trading-bot -n 100 --no-pager`. After updating code, use `sudo systemctl restart trading-bot`. Stop it with `sudo systemctl stop trading-bot`. Configure Ubuntu power settings to keep the PC awake and restore power after outages. Back up `data/signals.sqlite3` using SQLite's backup facility rather than copying an actively written file. Python services read `.env` directly; no package installation or virtual environment is necessary.

## Dashboard, reruns and alert stages

Overview, Signals, Logs and Backtests now use the same browser app locally and online. Signals and Backtests open **Finding details** first and provide a separate **Coverage summary** mini tab. Backtests has **Configure & re-run backtests**, with historical asset names, independent timeframes, and a strategy selector that explains its logic. Local reruns execute on a single background thread and do not enqueue Telegram messages. The UI reports running, completed or failed status. Historical replay is currently restricted to the existing **2020** dataset, including pre-2020 warm-up; unsupported combinations are explicitly unavailable.

Cloud reruns use `cloud/functions/backtests` against historical candles already mirrored to Supabase. They execute independently of the Ubuntu live scanner. Apply the additive migration `cloud/backtests.sql` after the existing schema. It adds owner-scoped jobs and an API dispatcher, preserves existing findings/feedback, and commits the completed report atomically. A single job per installation is allowed; a job older than five minutes can be replaced on retry. Cloud completion never writes to the live delivery queue. Existing matching findings retain their feedback IDs. A completed cloud replay is the online current report; local CSV replays remain the local current report.

To apply the migration, set the maximum **604800-second (seven-day)** JWT lifetime, and deploy the function, set `SUPABASE_ACCESS_TOKEN` locally to an authorized management token and run **`./scripts/deploy-cloud.sh`**. It invokes `scripts/configure-cloud.py`, the official Supabase CLI and the static UI build. Do not put the token into browser config or commit it. Publish the resulting UI through the existing Cloudflare GitHub build after pushing the changes. Existing access JWTs retain their previous expiry until sign-in/refresh. The browser now stores the refresh token for its current tab and refreshes expired sessions automatically, so access JWT expiry does not itself force repeated sign-in. Actual cloud migration, JWT change and function deployment require authenticated Supabase access; preparing these files does not deploy them.

Live alerts now include **warm-up / potential divergence** at the potential second pivot candle's close and **confirmed divergence / structural pivot confirmation** after the required following candle closes. With the current one-right-candle rule, “after the next candle” and “with the confirmation candle” are the **same event**, so one confirmation alert describes both. No intrabar new-high/low alert is generated. Warm-up warnings can fail subsequent confirmation. The new stage-specific baseline establishes itself on the first successful scan after upgrade, and stage-specific deduplication survives restarts.

At **07:00** and **11:00 Asia/Jerusalem**, the local worker sends monitoring/follow-up reviews of active, previously emitted setups using cached closed candles. Reviews explicitly name warm-up or confirmation status and disclose the latest cached close. They do not claim a fresh provider scan or new confirmation. Setups with more than ten subsequent cached candles or a structural breach are excluded; candidates come from the latest 500 stored events received within 31 days. A scheduled slot has a ten-minute catch-up window and is persisted once per day; long missed slots are skipped. Delivery uses the existing durable Telegram queue, retries and dry-run behavior. Provider quota, polling and outages still determine how soon a closed-candle stage is discovered.

Logs supports errors/scan failures, newest/oldest/error-first sorting and text search. Error filtering loads the latest 200 matching events rather than filtering only the latest general events. Times show Israel local dates, relative minutes and their Unix timestamp; market-check JSON is translated into readable asset/timeframe/status/reason text.

Implementation status (6 October 2026): `cloud/backtests.sql` was applied successfully to Family Trading Bot and the `backtests` Edge Function was deployed through the authenticated Supabase dashboard. Unauthenticated invocation returns HTTP 401. The updated local app was restarted. The maximum JWT lifetime is prepared in the Supabase Sessions page but its Save action awaits the browser tool’s required confirmation; automatic refresh is implemented. Cloudflare successfully published the upgraded UI from commit d61afdf. The project uses ECC JWTs; the backtests function’s legacy-only gateway setting still needs its required browser approval to be turned off before signed-in cloud reruns can work. The handler verifies current user JWTs and checks owner access itself.
