# Family Trading Bot

The Linux worker and authenticated dashboard share one authoritative Postgres database. Candles, strategies, configuration, findings, feedback, request budgets and delivery history persist in Postgres. There is no SQLite runtime or mirror synchronization.

## Installation and operation

Use the [installation guide](https://github.com/idodidodi/trading-bot/blob/main/POSTGRES_MIGRATION.md), split into Mac work with online-provider credentials and remote Linux work over SSH. It covers verified TLS, the restricted worker login, initial import, service setup and recovery.

Production runs from a separate application/configuration directory using the `trading-bot-postgres.service` user service. Enable user lingering so it survives logout. Keep Linux awake and online for scanning and alerts. The Mac can use the [online dashboard](https://family-trading-bot.idodidodi.workers.dev/) without running a worker.

Install [requirements-runtime.txt](https://github.com/idodidodi/trading-bot/blob/main/requirements-runtime.txt) and use the [Postgres environment template](https://github.com/idodidodi/trading-bot/blob/main/.env.postgres.example). `TRADING_DATABASE_URL` must use the shared SESSION pooler and verified TLS; transaction pooling is unsupported. Administrator credentials stay on the Mac. Provider and Telegram credentials stay in the private Linux worker environment.

`DATA_DIR` holds raw CSV inputs and diagnostic files, not a runtime database. `scanner.json` seeds a new installation; use Assets to edit its saved Postgres configuration afterward.

One worker owns the installation through a database advisory lock and durable ownership generation. `bot_runtime.runtime_control` separately controls routing and external effects. `DRY_RUN=true` simulates Telegram delivery; paper orders additionally require the paper-trading setting. Dry-run alerts are not automatically resent when real delivery is enabled.

Automatic legacy executable updates are disabled. Linux and Windows builds remain tested, but Postgres artifacts do not publish to the old update channel. Deploy reviewed source deliberately and restart the existing service; do not launch another scanner against production.

## Strategies and data

The worker supports RSI divergence, Pin-Bars, BB Pin-Bars, RSI + PB, daily momentum, historical replay and CSV research. Each asset has explicit providers, timeframes and strategy selections. Strategy versions, pending setups, baselines and delivery identities survive restarts.

See the [RSI strategy specification](https://github.com/idodidodi/trading-bot/blob/main/skills/rsi-divergence/SKILL.md). Providers include Twelve Data, Kraken, OANDA, Tiingo, Alpaca, FMP and Yahoo. A successful connection does not establish candle freshness or entitlement; inspect coverage, timestamps and provider notes. Feed changes retain separate identities and require verified symbol/provider choices.

The dashboard uses Supabase Auth and owner/editor-scoped APIs. Browser roles cannot directly access worker tables. Hosted replay and uploaded-CSV research run independently of Linux. Telegram reservations retain accepted message IDs, but ambiguous network failures can still repeat a message. Reconcile ambiguous broker submissions before retrying them.

## Local ad hoc scanner UI

Open `adhoc/rsi_pb_scanner.py` in PyCharm, use the project's `.venv` interpreter, and run the file. The local Tkinter window provides timeframe selection, Run/Stop/Resume controls, live stock/pattern/coverage tables, filters, CSV export and saved-result loading. See [the ad hoc tools guide](adhoc/README.md) for setup and controls. No server is required.

## On-demand S&P 500 RSI + PB screen

Run a read-only screen from the repository root with the existing local `.env` Alpaca credentials:

```sh
.venv/bin/python scripts/scan-rsi-pb-sp500.py
```

This scans all constituent securities on monthly, weekly, daily and 4-hour candles with the existing `rsi_pb.detect` engine. Matches default to the latest 10 completed candles per timeframe. Each run refreshes membership (with a visible fallback to the dated repository list) and writes a new timestamped directory under `data/ad-hoc/`. `qualified-stocks.csv` and `.txt` contain the deduplicated stock list; `patterns.csv`, `signals.json`, `coverage.csv` and `report.md` contain individual matches, evidence and coverage limitations. It does not start the worker, access the database, send alerts, place orders or change configuration.

For latest-candle signals only, use `--recent-bars 1`. To scan daily and weekly candles only:

```sh
.venv/bin/python scripts/scan-rsi-pb-sp500.py --timeframes daily weekly
```

The default history target is 250 candles; provider history can be shorter, especially monthly. Native Alpaca 4-hour bars include extended sessions and can require many paginated requests. For a shorter 4-hour download, use `--timeframes 4h --history-bars 100`; histories below 250 candles are explicitly flagged. This is a historical signal screen: inspect confirmation dates, actual latest candle dates and structural-breach flags in the report.

Completed candle batches are saved. Press Ctrl+C to save a partial report, then resume using the printed output directory:

```sh
.venv/bin/python scripts/scan-rsi-pb-sp500.py --resume data/ad-hoc/rsi-pb-sp500-TIMESTAMP
```

Resume preserves the original cutoff, universe, strategy rules and options, and retries failed batches. Run the command without `--resume` for a fresh market snapshot. `--output PATH` selects a new empty output directory; `--universe-file stock_universe.json` explicitly uses repository membership. Exit codes: 0 completed (including disclosed short histories), 2 provider/coverage failure, 130 interrupted. Run `--help` for all options.

## Development and tests

Use a separate Postgres project/database for development. Never point a development scanner at production. Source runs default to development with Telegram and paper trading disabled; an intentional production service sets `TRADING_BOT_ENV=prod` in its launching environment.

For the browser alone, run `python3 dashboard_server.py`. It starts no scanner and needs no worker database password. Database tests use [setup-postgres-tests.py](https://github.com/idodidodi/trading-bot/blob/main/scripts/setup-postgres-tests.py) and an explicitly disposable `TRADING_TEST_DATABASE_URL` whose database is `trading_bot_test`. Run `python3 -m unittest discover` and `node --test web/*.test.mjs`.

## Archives and recovery

SQLite remains only in the explicit offline [import/recovery command](https://github.com/idodidodi/trading-bot/blob/main/scripts/migrate-postgres.py) and migration test fixtures. Production modules do not import SQLite. Retired installations and databases belong in protected archives outside active application/data paths.

Preserve final migration snapshots, the hosted backup, import receipt and retirement manifest. After new Postgres writes or deliveries, do not restart stale SQLite. Follow the recovery section of the installation guide; prefer repairing Postgres when effects or identities require reconciliation.

The [archived SQLite documentation](https://github.com/idodidodi/trading-bot/tree/main/docs/archive/sqlite-runtime) is historical reference. Its installers, sync instructions and storage assumptions are superseded by the current guide.
