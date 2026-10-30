# Scheduled S&P 500 reports

The full constituent universe is screened independently of permanent portfolio monitoring. `stock_reports.enabled=true` replaces the old daily rotating weekly stock selection. Permanent assets keep their individual alerts, strategy selections, prices restrictions, and timeframes. Report scans apply all four strategy detectors without a price preselection or portfolio exclusion and never insert live signals or modify their baselines.

## Schedule and contents

- Weekly: Sunday from 08:00 Asia/Jerusalem, for the preceding US market week.
- Monthly: first calendar day from 08:00 Asia/Jerusalem, for the preceding calendar month.
- Sunday that is also the first day produces two independent report sets.
- Each matching strategy/direction gets its own PDF: RSI divergence (candle-close stage), Pin Bars, BB Pin Bars, and RSI + PB, bullish and bearish.
- One additional PDF combines strategy/direction pairs with no matches among successfully evaluated stocks. Combinations without any evaluable data appear only in coverage notes, never as no matches.
- Each matching PDF groups stocks by candle open/close timestamps, shown once per group in Israel time with the offset. Columns: ticker, stock name, candle open price, candle close price, sector. Prices are USD and split-adjusted. Open times are the first regular session's 09:30 New York opening; closes respect holidays and early sessions.
- Pin strategies show the qualifying pin-bar candle; RSI shows the divergence pivot candle. Identical ticker/candle/direction rows are deduplicated.

Membership and company/sector metadata refresh together. On refresh failure, the latest saved membership is used, then the dated repository universe if no previous run exists. Missing names/sectors are labeled Unavailable and disclosed. Fetches target 250 native weekly/monthly candles, with minimum history enforced separately per strategy. Missing period candles, insufficient history, and history shorter than the 250-candle target receive a separate coverage PDF. Provider history can be shorter for newly listed stocks and monthly candles.

## Persistence and delivery

Runs freeze their universe, feed, rules, and period cutoff. Each batch is checkpointed in Postgres. A timed ownership reservation prevents concurrent processing; every checkpoint and artifact transaction verifies ownership. PDFs are persisted as bytes before delivery and reused after restart. Shared Telegram reservations prevent ordinary duplicate sends and retain the existing at-least-once limitation after ambiguous network failures.

Matching reports use stock strategy destinations. BB Pin Bars uses the Pin Bars destination. Combined no-match and coverage PDFs use General. Definitively rejected destinations fall back to General. Existing Telegram presentation, personal routing preferences, and shared notification sound limits apply. PDFs remain downloadable documents; the text-message Done/Undo editor is not attached to them.

Dry-run and development Telegram switches are honored. A dry run stores PDFs without sending them; enabling delivery later that same scheduled day sends the saved documents. Delivery/provider retries occur only on the scheduled local day. Interrupted runs still pending on a later date become `missed`, with saved artifacts and status retained. No historical report sets are created for dates before the feature was activated or when the worker never ran. Failed provider batches retry hourly; after 23:00 remaining failed batches are disclosed as unavailable so partial reports can be delivered before midnight.

## Installation

1. Install `requirements-runtime.txt` in the worker environment. Standalone Windows/Linux builds include ReportLab and its resources.
2. With the worker stopped, apply `cloud/stock-reports.sql` as the database schema owner, after the existing Postgres runtime/worker-role migrations. It creates private report tables and enables the managed policy when no explicit report preference exists. It preserves permanent assets and is safe to rerun.
3. Deploy the updated worker and restart it. No provider calls or Telegram sends occur merely from applying the migration. An explicit existing `stock_reports.enabled=false` remains disabled.

Without the migration, report-enabled workers log `Stock reports unavailable` instead of attempting reports. The legacy rotating policy remains stored for rollback but is bypassed while reports are enabled. Turning reports off restores the old rotating behavior if its policy remains enabled. Old findings and delivery history are retained.

## Diagnostics and validation

`stock_reports.status(store)` returns the latest 12 run summaries: state, timeframe, period, evaluated/universe counts, missing/insufficient coverage count, and next retry. The local `GET /api/stock-reports` adapter is available in explicit disposable-database test mode. Production retains the existing authenticated-Supabase-only API restriction; this release does not publish a new hosted dashboard endpoint. Operators can inspect the private report tables through the administrator connection. PDF bytes remain private to the worker database and Telegram attachments.

Run `test_stock_reports.py` for calendar, detector integration, PDF extraction, membership fallback, and Telegram transport tests. Database tests require the existing explicit disposable `trading_bot_test` setup; `scripts/setup-postgres-tests.py` now installs the report migration. Install `pypdf` for PDF extraction tests. These tests mock provider downloads and Telegram sends. Render representative generated PDFs and inspect first/last pages as part of layout verification.
