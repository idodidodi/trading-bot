# Automatic crypto RSI Chase

The automatic group is registered by its chat ID under `crypto_auto_chase`; its
display name (currently “RSI + 5Min RSI”) has no effect on strategy behavior.

Each owner/configured crypto asset has one active pre-entry watch, shared by
automatic and personal Chase destinations. Separate reported positions are not
pre-entry watches and retain the existing position-monitoring rules.

After a new provisional, closed-candle RSI original is delivered, automatic
enrollment watches 5m, 15m, 30m and 1h, strictly below the current stage. A 1h
original therefore watches only 5m, 15m and 30m. Each new same-direction lower
divergence sends an alert and becomes the current stage; scanning continues only
below that stage. Stages can be skipped. Equal/higher and opposite secondary
divergences cannot replace a stage. When a separately published lower original
has the opposite direction, it starts the new lower-priority original watch;
it is never described as a same-direction confirmation.

Reaching 5m completes the watch. Price moves, including the old favorable-move
threshold, do not cancel these crypto watches. Expiry is evaluated before data
fetching, so an outage cannot keep an expired watch alive. An expired/completed
watch cannot block a fresh original, while duplicate delivery of the same original
cannot reopen it. Higher new originals never reset an active lower watch.

## Deadlines

The user confirmed timeframe minus five minutes:
**4h = 3h55m, 1h = 55m, 30m = 25m, 15m = 10m**. Each descent sets its stage's deadline
from detection time. Daily/weekly originals follow the same subtraction; monthly
uses the next calendar month, rather than a fixed 30-day duration. Five minutes
completes immediately. Equal/higher alerts and manual joins never reset a deadline.

## Shared data and compatibility

The scanner and Chase worker share native Kraken OHLC snapshots and in-progress
requests per exact pair/timeframe, retaining the existing global Kraken pacer and
cooldown. Automatic and manual destinations consume the same watch transition.
Indicator/detection results are shared by candle evidence and RSI rules. Empty,
delayed or failed responses have a short retry cache; no missing-data conclusion
is reported as a successful scan.

Configured asset IDs unify originals from that asset's explicitly selected feeds.
The watch uses its exact Kraken spot feed; a higher original on a different venue
can use an explicitly configured Kraken 1h override. Secondary alerts disclose their
own feed. No symbol, quote currency or venue is guessed. Assets without a configured
supported lower feed remain unavailable rather than silently substituting data.

Current crypto originals may be 1h, 4h, daily, weekly or monthly. Existing reported
position entry remains supported only for 1h/4h originals. Automatic cards provide
message dismissal; personal cards retain owner-bound Chase, lookback and entry
controls. A reported entry forks position monitoring while an automatic watch
continues toward 5m/expiry. It does not submit an order.

On startup only active crypto pre-entry sessions adopt this policy. Existing
completed and entered sessions retain their state. Duplicate legacy pre-entry
sessions collapse to the lowest active stage, retaining aliases for old callbacks.
Lookback reports intermediate findings and can advance to a lower stage or complete
a verified historical 5m match. It preserves historical deadlines, labels retrospective
alerts, and cannot rewind or reopen the shared live watch.
All state changes use the existing updater activity gate. Active watches, pending
lookbacks/deliveries, and unclosed positions independently block source updates.

## Deployment

After `divergence-chasing.sql`, apply `cloud/kraken-intraday.sql` and then
`cloud/crypto-auto-chase.sql`. Both are additive/rerunnable and neither registers
destinations nor activates delivery. Their schema changes require the supervised
source cutover; do not bypass the updater's compatibility or active-work gates.

The installation owner's authenticated `/api/crypto-auto-chase-settings` endpoint
accepts `{action:"save", revision, enabled, chat}`. Register only the already verified
private group, using the returned revision; other routes and preferences are
preserved. The endpoint rejects shared/other-recipient destination reuse and is
owner-only. Activation excludes pre-registration originals and does not replay old
alerts. The existing worker and Telegram listener remain the sole processes.

Verify the exact deployed source fingerprint, sole worker ownership, fresh scanner
health, owner settings and private SQL access after publication. Use disposable
Postgres and mocked market data/Telegram for behavioral smoke tests. Do not send
test alerts/orders or start another production scanner. An already verified source
rollback must respect active watches and entered positions; disabling the new group
alone does not reconcile or erase their state.
