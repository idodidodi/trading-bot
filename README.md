# Independent local trading-signal platform

**Market-data provider → our candle scanner → local dashboard + Telegram.** TradingView is not required. Python 3.10+; no third-party Python dependencies. The computer running the app must stay online for live polling and Telegram delivery.

The scanner reads numeric defaults from [the Markdown skill](skills/rsi-divergence/SKILL.md): RSI **3 on closes**, Wilder smoothing; Bollinger Bands **20 / 2**, population deviation; strict **two-left/two-right pivots**; consecutive pivot spacing **5–60**. It calculates indicators from OHLC data locally. Bullish divergence requires a lower price low, higher RSI, and a lower-band touch at the second low; bearish is reversed. Wicks count. Closed confirmation candles only. Signals only, with no order execution or trade levels.

## Data-provider recommendation

Evaluate **Twelve Data** first as a potential mixed-market provider: https://twelvedata.com/. A REST adapter is implemented for its `/time_series` endpoint. Before paying, verify your exact instruments, native **4h / daily / weekly / monthly** history, exchange feeds, stock adjustment basis, delay, and plan entitlements. DAX cash versus futures/CFD, the dollar index, and NEAR/USD need explicit confirmation. The adapter has not yet been tested with a live key in this environment.

For crypto, a direct exchange feed is another option once the venue and USD quote pair are chosen; no direct-exchange adapter is implemented yet. Never silently replace USD with USDT. Your TradingView account does not provide a reusable market-data API credential for this platform.

To get a Twelve Data key: create an account, open its dashboard, and locate the API-key section. Enter the key only in local `.env` as `TWELVE_DATA_API_KEY`. Do not send keys in chat. API usage costs and exact coverage depend on the provider's current plan. The six example assets × four timeframes generate 24 requests per polling cycle; set a polling interval that fits your entitlement. This initial adapter does not pace requests to a per-minute quota; use a paid quota suitable for the batch, or a smaller asset list while evaluating. Increasing the polling interval alone does not resolve a burst quota.

## Start locally

```sh
cp .env.example .env
# Edit .env: DRY_RUN=true for initial local testing.
python3 platform_app.py
```

Open `http://127.0.0.1:8080` on the computer running the app. No public port, webhook secret, HTTPS tunnel, or TradingView alert is needed in the default `MODE=scanner`. The local dashboard shows every configured asset/timeframe's coverage status and received signals. Missing data is shown as unavailable, not as no signal. It shows the latest closed-candle timestamp; a successful scan alone does not guarantee a feed is current.

## Configure assets and timeframes

Edit `scanner.json`. The example asset list is EURUSD, DAX, dollar index, BTCUSD, NEARUSD, and NVDA; timeframes are `monthly`, `weekly`, `daily`, and `4h`. Restart after changing configuration or the skill.

The supplied configuration uses **CSV files** until a provider is selected and verified. It does not download live prices by default. To use Twelve Data, replace an asset entry after confirming its exact feed, for example:

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

The adapter requests native timeframes and UTC timestamps, including up to 500 candles. It uses a **conservative calendar cutoff** for completeness: four hours, the next UTC calendar day, seven days, or the next calendar month. For session-based markets, this can delay detection beyond the actual closing bell. It is not an exchange-session calendar implementation. Verify this convention with your chosen provider before relying on timing-sensitive alerts. No candles are interpolated or fabricated.

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

The first successful scan of a feed/timeframe establishes a baseline and does **not** send historical signals. Subsequent scans emit signals confirmed after the last observed candle; overlapping polls and restarts do not repeat the same pivot pair. Changing the provider identity or skill establishes a new baseline. Candle history should ideally have 250+ bars. The minimum with current rules is 65; smaller datasets are insufficient, and 65–249 bars are labeled limited history. This avoids requiring more than 20 years of monthly data for recently listed instruments.

SQLite state and the delivery queue live in `data/signals.sqlite3`. Offline catch-up is limited to the history available from your provider (up to 500 bars); a gap exceeding that window is reported as unavailable and requires backfill. Telegram failures retry up to once every five minutes and survive restarts. One app process per database. Delivery is at-least-once: a crash after Telegram accepts a message but before recording success may repeat a message. `DRY_RUN=true` records delivery as simulated and never sends those signals later.

```sh
python3 -m unittest -v
```

Tests exercise RSI seeding, population BB, real OHLC bullish/bearish divergence, strict pivot equality, delayed confirmation, band rejection, malformed/open candles, first-run baselines, subsequent signals, persistent deduplication, webhook compatibility, and simulated Telegram retries. These tests do not prove live data-provider coverage or Telegram connectivity. Live scanning requires a selected feed, its credentials/entitlement, and network access. The default CSV configuration reports missing files until you supply candles.

Legacy `generate_pine.py`, `tradingview_template.pine`, and `MODE=webhook` remain available for compatibility. They are not used by the independent scanner.

## Logs and Backtest 2020

Restart the updated app to show the Overview, Logs, and Backtest 2020 navigation links. Logs record startup, scan cycles, market checks, and delivery attempts with UTC timestamps. The page shows the latest 200 events. Logging begins after this update; earlier activity cannot be reconstructed.

Run `.venv/bin/python backtest.py` to replay historical CSV files configured in `scanner.json`. Include enough pre-2020 candles for indicator warm-up. This uses the same detection engine and only lists signals confirmed from January 1, 2020 through December 31, 2020 UTC. It does not enqueue live alerts or send Telegram messages. The latest report appears in the Backtest 2020 tab. Missing CSVs appear as unavailable; short history is labeled partial. Calendar range checks do not verify exchange sessions or missing trading days. Provider-mode feeds need historical CSV data; the live 500-bar adapter cannot supply a complete 2020 replay.

Tests use temporary databases. The update preserves existing settings and data; restarting adds activity logging, and running the replay adds a report table to the existing database.
