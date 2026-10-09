# Independent local trading-signal platform

**Market-data provider → our candle scanner → local dashboard + Telegram.** TradingView is not required. Python 3.10+; no third-party Python dependencies. The computer running the app must stay online for live polling and Telegram delivery.

The scanner reads numeric defaults from [the Markdown skill](skills/rsi-divergence/SKILL.md): RSI **3 on closes**, Wilder smoothing; Bollinger Bands **20 / 2**, population deviation; strict **two-left/one-right pivots**; consecutive pivot spacing **5–60**. It calculates indicators from OHLC data locally. Bullish divergence requires a lower closing price, higher RSI, and a lower-band touch at the second low; bearish is reversed. Pivots and divergence comparisons use closes; wicks still count for Bollinger Band touches. Live alerts wait for **one following candle to close** and confirm the second pivot. The band touch and RSI divergence are evaluated at the pivot itself; the following candle must have a strictly lower close (bearish) or higher close (bullish). Backtests and newly generated Pine use the same rule; saved historical findings retain their original rules. Signals only, with no order execution or trade levels.

## Data-provider recommendation

**Twelve Data** supplies candle feeds through `/time_series`. The permanent forex set is EUR/USD, USD/JPY, USD/CNY, GBP/USD and USD/CHF, based on the [BIS 2025 turnover survey](https://www.bis.org/publications/202509-commentary-otc-derivatives). The permanent crypto set is BTC/USD, ETH/USD, SOL/USD, NEAR/USD and DOGE/USD (Kraken spot for 4h/daily/weekly; Binance via Twelve Data for monthly). The requested NEAR and DOGE are included within the five permanent crypto slots; this is a chosen core, not a claim that all five lead global volume. Existing NVDA and the unavailable DAX placeholder are retained. The dollar index is excluded from the active asset list.

At **08:00 Asia/Jerusalem** each day, the worker selects five additional forex pairs and five additional crypto pairs. Ranking uses **Kraken venue-specific trailing 24-hour USD turnover**, not global forex volume. Base volume is multiplied by the 24-hour VWAP; non-USD forex quote turnover is converted at the current Kraken USD rate. Only online Kraken pairs with a matching Twelve Data catalogue feed qualify. Crypto must have an exact USD quote and Binance catalogue coverage for monthly candles; native 4h/daily/weekly candles use Kraken when `daily_universe.crypto_native_provider` is `kraken`; fiat and known stablecoin bases are excluded. USD is never substituted with USDT. Kraken native crypto candles share the ranking venue; forex and monthly crypto use Twelve Data. Per-timeframe provider and exchange appear in coverage.

The selection is saved in SQLite and stays fixed until the next 08:00 boundary, including across restarts. Startup catches up if the morning refresh was missed. Permanent/configured assets are excluded from the rotating set, including disabled entries, so daily selection cannot bypass a manual disable. If ranking fails or either market has fewer than five eligible pairs, daily additions are unavailable and only permanent/configured assets scan; the next scan retries. Old selections are not reused for a new day. The computer and worker must remain running for scheduled refreshes. Daily selections appear in the local Assets page and activity logs; provider entitlement failures remain visible in scanner coverage.

Candle polling waits **four hours** after each scan and wakes earlier for the morning refresh. With 21 live assets and four timeframes, routing ten crypto assets’ three native timeframes to Kraken reduces each cycle from 84 to 54 Twelve Data requests (about 36% fewer). Six regular cycles use 324 Twelve Data requests/day, plus a possible morning cycle and restarts. Twelve Data requests remain paced at least eight seconds apart; Kraken requests use a separate timer with a minimum one-second spacing. Kraken public limits and outages still apply. This is sized for the installation's existing 800/day, 8/minute budget; other API consumers share the quota. Credentials stay in local `.env` as `TWELVE_DATA_API_KEY`. Catalogue presence does not guarantee account entitlement or sufficient candle history.

## Kraken quota relief

Kraken is enabled for permanent and daily-selected crypto 4h, daily and weekly candles; optional 1h candles are supported too. Its public spot API needs no API key. The scanner verifies the canonical BASE/QUOTE response and excludes the final uncommitted bar. No USDT substitution or calendar-month aggregation is performed. Kraken returns at most about 720 recent candles; older catch-up requires backfill. Switching venue establishes a separate baseline, signal identity and candle cache, preventing mixed-venue indicators and historical alert floods.

In **Assets**, keep Twelve Data as the primary provider with its Binance exchange and choose **Kraken spot** for the three native timeframes. The saved asset field is `"timeframe_providers": {"4h": "kraken", "daily": "kraken", "weekly": "kraken"}`. Coverage reports the actual selected provider and exchange. A Kraken override clears the primary venue’s TradingView mapping; use a separately verified Kraken mapping for assets whose primary provider is Kraken. For a Kraken-only asset, choose Kraken as primary, exchange `Kraken`, an exact pair such as `BTC/USD`, and native timeframes only. Monthly remains on Twelve Data; DAX still needs a verified index feed.

Cloud upgrades require applying `cloud/providers.sql` after the existing schema/backtests migrations. It updates the asset validation and save logic while preserving the deployed backtest dispatcher; then publish the rebuilt browser assets. The local scanner and local Assets editor use the new routing immediately after restart. Existing selections stay fixed for the day, with provider routing taken from the current policy.

## OANDA forex and optional hourly candles

OANDA v20 supports all five live timeframes. Configure these values locally in `.env` (never put the token in browser settings):

```dotenv
OANDA_API_TOKEN=your_personal_access_token
OANDA_ACCOUNT_ID=your_v20_account_id
OANDA_ENVIRONMENT=practice
```

Use `live` only with a matching live-account token and account ID. Run `.venv/bin/python scripts/activate-oanda.py`, then restart the local app. Activation checks the account’s currency-instrument catalogue and each configured pair’s selected candle timeframes, verifies the minimum closed history, backs up SQLite, and moves only verified forex feeds. Unsupported pairs retain Twelve Data; USD/CNH is never substituted for USD/CNY. The daily forex policy ranks eligible account-supported pairs, with older frozen selections retaining Twelve Data for pairs not verified during activation. New OANDA feeds start separate signal baselines. OANDA uses unsmoothed midpoint prices, explicit 17:00 New York daily alignment and Friday weekly alignment; daily/weekly boundaries respect daylight saving, and only provider-complete candles are evaluated. Practice and live histories have separate feed identities.

In local **Assets**, choose **OANDA forex**, exchange `OANDA`, and a slash-form pair such as `EUR/USD`. **1h** is selectable independently per asset and is initially off. Kraken and Twelve Data also support native 1h candles. The worker wakes 15 seconds after a known hourly candle close, subject to pacing and provider availability. Historical 2020 replay options remain the four stored timeframes; hourly backfill has not been prepared.

If all ten permanent/daily forex pairs are verified on OANDA, a four-timeframe cycle uses 14 Twelve Data requests instead of 54; each forex pair retained on Twelve Data adds four requests. With hourly enabled across all 21 live assets and all ten forex pairs on OANDA, a full cycle uses 15 Twelve Data requests; 24 such cycles would use about 360/day, excluding restarts/other consumers. Enabling hourly before forex migration can exceed the existing Twelve Data daily budget. OANDA uses its own minimum one-second request spacing and sanitized access/rate-limit diagnostics. The updated `cloud/providers.sql` supports OANDA and live 1h configuration and can upgrade the earlier Kraken migration; it still requires cloud deployment.

## Start locally

```sh
cp .env.example .env
# Edit .env: DRY_RUN=true for initial local testing.
python3 platform_app.py
```

Open `http://127.0.0.1:8080` on the computer running the app. No public port, webhook secret, HTTPS tunnel, or TradingView alert is needed in the default `MODE=scanner`. The local dashboard shows every configured asset/timeframe's coverage status on Overview and received signals on the Signals tab. Missing data is shown as unavailable, not as no signal. It shows the latest closed-candle timestamp; a successful scan alone does not guarantee a feed is current.

## Configure assets and timeframes

Use **Assets** to edit an existing installation. `scanner.json` seeds configuration only on the first start; subsequent runs use managed configuration in SQLite, so editing that file alone does not update an existing installation. The permanent and daily asset sets are described above; live timeframes are `monthly`, `weekly`, `daily`, `4h`, and optional `1h`. The default selection stays on the original four timeframes. Restart after changing the skill.

The supplied configuration uses Twelve Data for forex, NVDA and monthly crypto, Kraken for native crypto 4h/daily/weekly (optional hourly too), and a CSV placeholder for DAX. Verified forex can move to OANDA after activation. A valid `TWELVE_DATA_API_KEY` in `.env` is required. An example live feed entry is:

```json
{
  "id": "NVDA",
  "provider": "twelvedata",
  "symbol": "NVDA",
  "exchange": "NASDAQ",
  "feed_confirmed": true
}
```

`feed_confirmed` is your explicit confirmation of the symbol/feed choice and entitlement, not a guarantee of connectivity. The adapter also rejects a returned symbol/interval/exchange that differs from the request. Do not guess symbols for DAX; resolve them with the provider first. Configure an optional `max_data_age_seconds` per asset to enforce an appropriate freshness limit, allowing for market closures. Use `continuous: true` only for a verified continuously traded feed with no scheduled session closures; it rejects missing buckets.

The adapter requests native timeframes and UTC timestamps, including up to 500 candles. It uses a **conservative calendar cutoff** for completeness: four hours, the next UTC calendar day, seven days, or the next calendar month. For session-based markets, this can delay detection beyond the actual closing bell. It is not an exchange-session calendar implementation. Verify this convention with your chosen provider before relying on timing-sensitive alerts. For repeated timestamps in an ordered Twelve Data response, the adapter keeps the last returned version. If the next native bar begins before the nominal end, it uses that next start as the close boundary. Both adjustments are disclosed in coverage Details; the strict CSV duplicate/overlap checks remain unchanged. No OHLC prices are interpolated. Provider boundary and revision conventions can affect indicator results.

If Twelve Data returns malformed historical OHLC prices, the scanner excludes the entire prefix through the last malformed candle and evaluates only the uninterrupted valid suffix. Coverage Details disclose the bad candle's UTC timestamp, excluded count and remaining closed history. Prices are never clamped and candles are never stitched across a bad bar. The suffix must still meet the strategy's minimum history; recent corruption remains unavailable, and the existing catch-up guard blocks recovery when it would skip unprocessed candles. A shorter history changes indicator initialization and is flagged as limited below 250 bars. CSV validation remains strict. Insufficient-history coverage now reports the available and required counts.

[OANDA v20](https://developer.oanda.com/rest-live-v20/instrument-df/) is implemented for hourly, 4h, daily, weekly and monthly forex midpoint candles; activation requires an eligible v20 account and local credentials. DAX cash-index coverage still requires a verified, entitled exact feed; substituting a CFD, ETF, futures contract or USDT pair would change the instrument. QNT monthly remains unavailable while its selected feed has fewer than the required closed candles.

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

Restart the local app, then open **Assets**. Bulk-add comma-separated tickers, select each asset's candle timeframes independently (monthly, weekly, daily and 4h by default), and configure the exact data-provider symbol and optional exchange-qualified TradingView symbol. New tickers start as disabled drafts. Enable them after arranging a CSV feed or confirming Twelve Data coverage. The data adapters are CSV, Twelve Data and Kraken; adding a ticker does not itself fetch a new provider's data. Save applies at the next scan-cycle boundary without a restart. Disabling removes an asset from future scans while preserving historical findings and feedback. `scanner.json` remains unchanged; managed configuration is stored additively in SQLite.

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

Alerts are driven by actual setup candle events, with no fixed 07:00 or 11:00 review messages. Pending clock reviews from earlier versions are cancelled without delivery; historical records remain available.

Logs supports errors/scan failures, newest/oldest/error-first sorting and text search. Error filtering loads the latest 200 matching events rather than filtering only the latest general events. Times show Israel local dates, relative minutes and their Unix timestamp; market-check JSON is translated into readable asset/timeframe/status/reason text.

Implementation status (6 October 2026): `cloud/backtests.sql` was applied successfully to Family Trading Bot and the `backtests` Edge Function was deployed through the authenticated Supabase dashboard. Unauthenticated invocation returns HTTP 401. The updated local app was restarted. The seven-day JWT lifetime (604800 seconds) was saved and verified on 7 October 2026; automatic refresh is implemented. Cloudflare successfully published the upgraded UI from commit d61afdf. The project uses ECC JWTs; the backtests function’s legacy-only gateway verification was disabled with approval on 7 October 2026. An unauthenticated invocation was then rejected by the function itself with HTTP 401 (Sign in required). The handler verifies current user JWTs and checks owner access itself.

### Alpaca quota fallback and historical replay

Confirmed US equities (US stock/ETF market metadata or NASDAQ/NYSE/AMEX exchange) automatically fall back to Alpaca only when Twelve Data returns HTTP or JSON code 429. Forex, indices and crypto are not routed to the equity endpoint. Set `alpaca_rate_limit_fallback: false` in scanner configuration to disable this behavior. Current NVDA qualifies; the existing Kraken crypto and OANDA forex routes are preserved.

Set actual `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in `.env`, then restart the local app. The adapter always calls the market-data host, independently of the trading base URL, and never submits orders. `ALPACA_DATA_FEED=sip` (default) requests consolidated stock bars with a 16-minute delay compatible with delayed historical access. `iex` explicitly selects the narrower IEX feed. Prices are split-adjusted. Feed and adjustment are included in cache/signal identity; a new source establishes a baseline, so a first fallback does not send historical alerts. Native 1h, 4h, daily, weekly and monthly bars use conservative closes, with New York calendar/DST boundaries for daily and longer candles.

In the local backtest dialog, choose **Alpaca US equities** as the data source. The existing 2020 replay downloads paginated history from 2016 for warmup, records source provenance, retains exact replay candles separately from live candles, and never queues Telegram alerts. Unsupported assets are reported as unavailable. Monthly 2020 warmup may be insufficient because Alpaca history starts in 2016; coverage is reported as partial. Downloads share a paced limiter below the Basic 200 requests/minute quota and stop on provider errors. Alpaca selection is local only; the hosted backtest service still replays its stored CSV history.

Official references: [historical stock bars](https://docs.alpaca.markets/us/reference/stockbars) and [market-data plans](https://docs.alpaca.markets/us/docs/about-market-data-api).

### Tiingo forex

Set `TIINGO_API_KEY` in `.env`. Tiingo is available as a primary forex provider and as an explicit timeframe override in Assets. Use slash symbols such as `EUR/USD` and primary exchange `Tiingo`. Only exact forex pairs are accepted; DAX is never sent to the forex endpoint, and USD/CNY is never silently replaced by USD/CNH.

The adapter requests native `1hour`, `4hour`, and `1day` OHLC. Weekly and monthly bars aggregate daily OHLC into UTC Monday weeks and calendar months, excluding potentially truncated leading periods and unfinished periods. This differs from OANDA's New York sessions. Closed candles only are evaluated, with separate `tiingo:Tiingo-UTC:PAIR` cache and signal identity. A first scan of a new source sets a baseline without sending historical alerts. Hourly scanning remains opt-in.

`TIINGO_REQUESTS_PER_HOUR=50` and `TIINGO_REQUESTS_PER_DAY=1000` default to conservative free-plan budgets. Request attempts are reserved atomically in `DATA_DIR/tiingo-cache.sqlite3`, across scanner, backtesting, activation and restarts. Identical successful responses are reused for five minutes; weekly/monthly/daily requests share daily responses. These rolling limits are conservative relative to provider reset windows. Raise them only to match your subscribed plan; bandwidth limits still apply. Tokens are sent in authentication headers and never written to request caches or browser assets.

After adding a valid key, activate verified permanent and currently selected forex feeds with:

```sh
python3 scripts/activate-tiingo.py
```

The script tests all enabled frames, requires sufficient closed history, backs up the database before changing routes, and keeps unsupported pairs on their existing feeds. Authentication or quota failures stop activation without saving partial routing. A frozen daily selection routes to Tiingo only for pairs verified during activation; new unverified daily pairs continue using Twelve Data. Restart the app after successful activation. The checked-in `scanner.json` remains the bootstrap configuration; activation updates the versioned managed configuration in SQLite.

Choose **Tiingo forex** in the local backtest dialog for the existing 2020 replay. Exact replay candles and provenance are stored separately from live candles; backtests do not enqueue Telegram alerts. Tiingo's published January 2020 history start means earlier warmup is unavailable and coverage can be partial, particularly for weekly/monthly replay. Hosted backtesting still uses stored CSV data; updated cloud asset validation requires applying `cloud/providers.sql` and rebuilding/publishing the UI separately.

Official [forex docs](https://www.tiingo.com/documentation/forex) and [coverage/quotas](https://www.tiingo.com/products/forex-api).

### DAX ETF proxy through Alpaca

The managed asset `DAX-ETF` uses the US-listed Global X DAX Germany ETF, exact symbol `DAX`, with Alpaca SIP split-adjusted candles. It is a proxy priced in USD during US trading hours, with separate feed identity and signal history from the cash index. Its verified live timeframes are monthly, weekly, daily, 4h and 1h. Alpaca can now be selected as a primary US equity/ETF provider or an explicit timeframe override; Twelve Data quota fallback remains available independently.

Local 2020 backtests now accept 1h for providers with hourly history, including Alpaca. CSVs without hourly files remain unavailable. US session gaps and limited pre-2020 monthly warmup can make replay partial. Backtests preserve provider candles and never enqueue live alerts. Online provider settings require the updated `cloud/providers.sql` migration; local changes do not deploy it automatically.

### German DAX provider research

[QuoteMedia explicitly lists German DAX index coverage](https://quotemedia.com/data-coverage/exchange-support/german-indexes-dax-index), with delayed and end-of-day data, REST and FTP delivery, and history from 2008. Real-time coverage is not offered on that listing. Pricing and quotas are sales-assisted, and hourly/4h OHLC availability is not confirmed by the public coverage page. Ask for the DAX performance cash index, the exact API symbol, delayed OHLC intervals (1h/4h/daily), historical depth, API limits, and a personal-use quote before subscribing. This is a confirmed DAX index vendor, not yet an activated scanner provider. DAX remains on the existing unavailable/CSV route until usable candle access is verified; the separate DAX-ETF proxy does not replace the cash-index feed.

### Signal follow-up outcomes

Every live and backtest finding has a **Follow up** button. It measures maximum adverse movement (drawdown), maximum favorable movement (gain), their price levels, and time from the second divergence pivot to each extreme. Bullish gain is upward; bearish gain is downward. Elapsed times are rounded to whole minutes. Since OHLC cannot identify the exact time of a wick, the displayed time uses the extreme candle's close relative to the pivot candle's start. The pivot candle itself is excluded.

The default window ends at the first recovery after an adverse wick: a later candle must close at or beyond the original pivot price. The entire recovery candle is included. **All available history** includes every stored closed candle and reports recovery after the worst drawdown. An unresolved excursion remains ongoing; absent pivot/history is explicitly unavailable. These are retrospective signal outcomes from P2, including candles before confirmation, rather than simulated trade returns.

Backtests calculate both windows during replay, with 2020 as the cutoff; older reports acquire outcomes when reviewed. Live follow-ups calculate only on a click and recalculate from the latest stored candles on subsequent clicks. The automatic rating appears beside your stars. It is 5 for gain/drawdown ≥ 3, 4 for ≥ 2, 3 for ≥ 1, 2 for ≥ 0.5, otherwise 1; positive gain without drawdown scores 5, while zero gain scores 1. Missing history receives no rating. The formula appears in the panel. Results stay separate from immutable finding evidence and feedback, preserving finding IDs, comments, and user ratings.

Local results persist in SQLite. Hosted support requires `cloud/followups.sql`, the updated backtest function (including `followup.mjs`), and the rebuilt browser assets; see `cloud/README.md`. Code/build preparation does not deploy the hosted update.

Close-based divergence update: new findings record closing prices at both pivots and a separate `band_touch_price` for the wick filter. Existing findings retain their saved evidence. Restart the local scanner to load the updated rules; the first scan establishes a new baseline. For existing hosted installations, apply `cloud/close-divergence.sql` (preserves newer API wrappers) and deploy the updated cloud backtest function. Follow-up movement measurements still use candle highs/lows, now relative to the saved pivot close for new findings.

Deployment verified on 8 October 2026: local scanner restarted and `/health` reports `price_source: close` with rules hash `4949dbd3b41340b8`. Supabase `backtests` version 5 is ACTIVE, including the band-touch pivot and outer-band-slope rules. Unauthenticated invocation returns HTTP 401. The close-divergence description migration was applied with all other API definitions preserved. The 2026 findings were recalculated under the updated rules.

2026 year-to-date replay (8 October 2026): replayed the same 92 strategy/source/asset/timeframe combinations through the recorded closed-candle cutoff under the close-pivot, band-touch-pivot and outer-band-slope rules. The report contains 253 confirmed and 460 warm-up findings (713 total), including the BTCUSD 4-hour Oct 2–4 bearish divergence. Source candles and report are retained in `data/historical-2026/`; earlier results are in the timestamped recovery backup. Local replay accepts a configured year, and hosted replay reads the current report year and cutoff. Apply `cloud/backtest-period.sql` to keep hosted options aligned.

Every completed local backtest run is mirrored to the hosted database by the background sync worker. Each run keeps its own result set online; the hosted Backtests page defaults to the newest run. Historical local runs are uploaded on the next sync after this behavior is deployed.

The local scanner also rotates ten upper-band and ten lower-band stocks from the S&P 400/500 universe on each U.S. market weekday. Candidates must have wick-touched the relevant band in one of the last five completed weekly candles; each ticker is selected at most once per week and can return the next week. Weekly RSI divergences are checked with the same close-pivot, band-touch, and slope rules. The current constituent snapshot is in `stock_universe.json` and should be refreshed when index membership changes.

Backtest findings support server-side sorting by asset, timeframe, direction/stage, confirmation time and user rating. The default order is oldest confirmation first. The hosted query migration is `cloud/backtest-sorting-pagination.sql`; page controls include numbered pages at the bottom of the findings table.

## Family upgrade — 9 October 2026

The scanner keeps per-feed/timeframe deadlines in SQLite. Weekly and monthly histories are read after another completed candle, including across restarts. Insufficient history waits for another candle too; failed reads retry. Unchanged delayed provider data retries hourly. US stock bars use scheduled NYSE sessions, holidays and early closes; forex respects the New York Friday/Sunday boundary and DST. Unscheduled exchange closures require a calendar update. Crypto scans continuously. USD/CNY is excluded globally, including the rotating selection.

At startup, a one-time portfolio migration adds MRVL, NASA, QCOM, META, AMD, CRCL and EROC with verified Alpaca equity/ETF identities and preserves existing feeds. It adds unavailable futures placeholders for CL, GC and ES until an exact provider and continuous-contract roll convention are configured. A consistent SQLite backup is retained in data/backups/before-family-2026-10-09.sqlite3. Later user edits are preserved across restarts. Hourly remains off by default.

Daily upper/lower stock picks exclude permanent portfolio tickers and reuse completed weekly OHLC across the week. Each new pick immediately reviews divergence confirmations on its latest completed weekly candle, then waits for another close. Hosted Assets now receives selection snapshots and coverage.

Overview includes Catch up and the strategy white paper. Catch-up requests survive outages; the local scanner saves recoverable missed findings without an individual Telegram message for each, then queues one durable summary. Summaries are silent from 23:30 inclusive to 06:30 exclusive in Asia/Jerusalem. Provider history windows still limit catch-up; gaps beyond them remain unavailable and require backfill. A worker restart after a prolonged gap also requests catch-up automatically.

Assets are grouped, compact and expandable, with red unavailable-data reasons. Signal/backtest row selection survives closing the chart. Divergence status and observed maximum gain/drawdown appear separately. Follow-up times use hours:minutes. Candle titles show direction and the touching band's recorded regression slope. The normalized angle is atan(slope_pct / 100) in degrees: one horizontal unit is one candle, one vertical unit is a 100% price change. This is independent of zoom and is not a screen-pixel angle.

Apply cloud/family-upgrade.sql after the existing sharing/followups/provider migrations, then deploy the updated sync Edge Function and publish the shared browser bundle. The migration preserves existing API wrappers and family read/edit roles.

See [Windows instructions](WINDOWS.md) for the standalone executable, hourly updates, and state migration.
