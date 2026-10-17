# Changelog

## Unreleased — 2026-10-17

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
