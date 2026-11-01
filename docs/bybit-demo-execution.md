# Bybit demo execution

The existing paper-execution worker also reads fresh crypto signals for Bybit's
fixed `api-demo.bybit.com` endpoint. It does not start another scanner or send
Telegram messages. Mainnet trading is unsupported.

Apply `cloud/bybit-demo.sql` and deploy the matching source before inserting the
singleton policy. The migration does not enable trading. Credentials retain the
names `family_trading_bot_bybit_demo_api_key` and
`family_trading_bot_bybit_demo_api_secret` in the private worker environment.

Policy version 1 uses independent entry strategies:

- `chase-4h-5m`: a live automatic crypto Chase event reaching 5m, in the direction
  of its 4h original. Intermediate descents use the existing Chase rules.
- `rsi-pb`: the existing crypto RSI + PinBar signal with a live notification,
  at the configured timeframe. Historical/catch-up findings are excluded.

Only signals confirmed after activation and no more than five minutes old can
enter. Each logical signal can enter once, across policy revisions/restarts.
Disabled assets cannot enter. Symbol bindings are explicit: USD signal feeds
map to separately verified Bybit USDT perpetuals. Closed contracts are rejected.

Example policy config (stored as JSON, not secrets):

```json
{"version":1,"allocation":"0.25","exit_distance":"0.01","max_signal_age_ms":300000,
 "strategies":["chase-4h-5m","rsi-pb"],
 "symbols":{"NEARUSD":"NEARUSDT","SOLUSD":"SOLUSDT","DOGEUSD":"DOGEUSDT"}}
```

Allocation is 25% of total demo equity, converted from USD to USDT using the
wallet's valuation, without a leverage multiplier. Quantity rounds down to the
instrument step with a 0.1% execution allowance. Existing exposure plus the
allocation cannot exceed equity; 1% of available collateral is reserved. No
leverage or position-mode settings are changed. One-way mode is required.
An occupied symbol and working non-protective account orders block new entries.
USDT and USDC linear positions/orders are paginated. Do not use this dedicated
demo account for concurrent manual or other-bot trading or other product types.

Market entry includes full-position SL and TP. After a terminal entry fill, the
worker sets both levels 1% from actual average fill (nearest valid price tick,
LastPrice triggers) and checks the returned position. If correction remains
unverified on the next cycle or allocation was exceeded, it attempts one
reduce-only close of its known position. A partial entry retains protection for
its actual size. Ambiguous entry/close outcomes block new entries and never
automatically resubmit. An empty immediate order lookup is inconclusive.

The cycle runs every 15 seconds inside the existing owner and effects gate.
Policy updates must increment `revision`; save `activated_at` as the activation
time in milliseconds to prevent historical entries. Set `enabled=false` to stop
new entries; existing positions continue reconciliation/protection. Immutable
policy snapshots and order IDs are in `bot_runtime.demo_execution_trades`.
Policy `status` records checks; unresolved rows block entries and source updates.
All tables are private to database administrators and the worker role.

Use `scripts/configure-bybit-demo.py --apply-schema` to provision, `--status`
to inspect, `--activate /private/path/policy.json` to save a new policy revision
and start from new signals, or `--disable` to stop entries. This administrator
CLI uses the existing Supabase login and never submits orders. Activate only
after verifying the deployed source, owner, account connectivity and credentials.

Validation uses mocked broker responses plus disposable Postgres. Do not create
test orders on a running installation. Production activation waits for a fresh
qualifying strategy signal; no qualifying signal means no order is expected.
