# Independent trading executor

Python 3.10+. Install this directory alone with `pip install .` or install `requirements.txt` and run its Python scripts. It requires no files or imports from the parent signal service. SQLite WAL stores inbox/cursor, execution journals and notice outbox separately from signal Postgres.

Set `SIGNAL_API_URL`, `SIGNAL_PROJECT`, `SIGNAL_PROJECT_TOKEN` privately. Start with `EXECUTION_ENABLED=false`:

```sh
python runner.py --database /absolute/private/execution.sqlite3 --once
python -m unittest discover -s tests -q
```

Omit `--once` to poll continuously (15-second idle interval). The disabled runner never initializes a broker. It commits each page before acknowledging it. Keep the same project/journal after restart. Backlog pages are fully consumed before entries so later invalidations are considered. Do not run two copies against separate journals with the same broker credentials.

`admin.py` supports `status`, `disable`, `configure-demo --policy PATH`, and `activate --reconciliation PATH`. Mutations require the runner stopped, acquiring its OS ownership lock. Activation is deliberately not part of normal startup: it requires reviewed reconciliation, database gates and `EXECUTION_ENABLED=true`. No service is installed or activated by packaging. Use the parent `SIGNAL_SERVICE_MIGRATION.md` for approved cutover; its gates remain requirements if this directory is copied elsewhere.

Alpaca paper execution retains deterministic client IDs, submission reconciliation and protective-exit behavior. Bybit demo retains durable intents, reduce-only close semantics and uncertainty blocking. No mainnet switch was added. Provider-neutral gateway and Velotrade adapter tests do not establish production integration. Telegram facts go to the signal API; this project has no Telegram bot credentials.

`migrate.py` imports a protected stopped-writer export into an empty journal, disabled. Original broker order/client IDs, source references, simulated tracking and reviews remain intact. Never import stale snapshots over a journal that has traded.

Trade research is offline: `momentum_policy.outcome` and `momentum_tracking.py` use recorded candles. `chase_simulation.py --replay SIGNAL_REPLAY_JSON --minutes CANDLES_JSON --cost-bps 10 --output RESULT_JSON` evaluates signal-service detection exports. Candle JSON is an ordered array of `{start,end,open,high,low,close}` in UTC milliseconds. `return_research.py` and `compare_chase_variants.py` retain return/volume comparison logic. These are simulations, not verified fills. Import or supply recorded market data explicitly; no signal database credentials are required or accepted.

See `.env.example` for consumer-owned credentials and flags. Environment files are not automatically loaded. Never copy the signal service's `.env` into this directory.
