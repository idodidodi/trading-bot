# Colmex integration

Exporter source and package: https://github.com/idodidodi/colmex-mt4-exporter
Local source: `/Users/ido1/Dev - not synced/trading-bot/mt4` (separate Git repository).
Read its `docs/INTEGRATION.md` and `docs/DEPLOYMENT.md` for package installation,
reviewed UTC policy, history limitations and coordinated deployment.

This repository owns `colmex_feed.py` (scanner adapter), `colmex_runtime.py`
(request reconciliation), `cloud/colmex.sql` (authenticated member requests), and
`web/app.js` (provider selection, request controls and readiness display).
Signal behavior is documented only in `TELEGRAM_ALERT_LOGIC.md`.

Apply `cloud/colmex.sql` after existing Postgres dashboard and worker-role migrations.
It is additive and repeatable; it does not activate an asset or worker. Install the
reviewed exporter package into the existing scanner virtual environment and configure
`COLMEX_FILES_DIR` and `COLMEX_TIME_POLICY`. Publish the matching hosted UI bundle
through the existing Cloudflare deployment, after approval. No new edge function is
required: requests use the existing authenticated dashboard RPC.

Subscriber requests use exact catalogue pairs and default to shared 4H scanning.
The UI uses the authenticated account automatically; no code change, broker account
credentials, terminal chart or indicator reattachment is needed for a supported pair.
Collection starts only when the worker acknowledges the manifest; signal readiness
also requires sufficient valid candle history. No new Telegram chat command is added.

The v2.10 indicator supplies broker-derived forex classification; install and reattach
it once before enabling the request UI. An older catalogue cannot authorize new pairs.

No current production provider choices have been changed. Owner-managed assets can
select Colmex MT4, exchange `Colmex-MT4`, exact symbol (e.g. `SPX500#`) and 1h/4h/daily.
There is no verified identical TradingView mapping. Current history gaps block signal
readiness; do not treat collection success or a user request as a successful activation.
