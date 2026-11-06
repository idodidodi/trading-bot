# Chase + look back

Implemented on `codex/chasing-5m-and-up`, not deployed. Eligible personal 1h/4h RSI
original alerts show adjacent **Chase** and **Chase + look back** buttons. They also
appear on the personal chase cards beside the existing entry controls. Existing
opt-in automatic chase enrollment remains; enrollment still occurs only after the
original alert has been delivered successfully.

**Chase** takes the active chase forward from the click, without reviewing the missed
interval. Repeated clicks do not keep postponing its schedule. It does not restart a
completed/stopped chase or interfere with queued history/position monitoring.

**Chase + look back** queues a historical review from the original candle-close alert
time through the click. Example: original alert at 19:00, click at 19:10; a qualifying
second divergence at 19:05 is reported with that historical timestamp, in Israel time.
The result is clearly marked **LOOKBACK / retrospective**, with the original anchor.
It is not presented as a real-time delivery at 19:05 or as an entry/fill.

## Replay rules

- Use the saved original symbol/venue, direction, RSI rules and favorable-stop setting.
- Replay the scheduled five-minute checks in chronological order, from the original
  alert. Inspect new 5m → 15m → 30m → 1h → 4h findings, capped at the original timeframe.
  The adverse-price gate remains anchored to the original close. No 1m divergence triggers.
- A favorable-stop event or configured deadline reached first blocks a later finding.
  An existing recorded first divergence can be returned without another provider fetch.
- Require price-history coverage up to the finding/stop, and sufficient indicator
  warm-up. Missing history is **incomplete**, never proof that no divergence occurred.
  The review is bounded to 12 hours; actual provider coverage may be shorter or gappy.
  Native historical indicators are recomputed from the currently available provider
  window; previously recorded findings remain authoritative evidence of prior detection.
- A verified historical finding completes a still-active, unentered chase, and keeps
  I'm in available. A complete review without a finding continues live chasing.
  Incomplete review leaves the live chase available and reports the limitation.
- A stopped/completed session is not reopened. Lookback never backdates an entry or
  changes a reported position. Reporting entry cancels a pending retrospective request;
  an in-flight result is discarded if the session revision changed.

The callback only validates/queues work; history is fetched by the existing worker,
using its shared Kraken caches and rate budget. Current historical-provider support is exact Kraken spot only. No extra update poller/scanner is
started. Requests/results are persisted in session JSON and the existing outbox;
callback redelivery is idempotent. Original-alert callbacks require the exact advertised
button in persistent dismissal state, active message, bot/group/message identity, and
owner Telegram identity. Done/Undo preserves the new buttons. Current preferences are
re-read after original delivery before enrolling a session.

No schema migration or broker action is introduced. Tests use mocked Telegram/provider
calls and the disposable PostgreSQL database. Production activation remains a separate
worker deployment; the last verified production SSH check timed out.
