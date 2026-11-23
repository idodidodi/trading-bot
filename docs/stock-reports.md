# Scheduled S&P 500 reports

Installation and diagnostics for the independent S&P 500 PDF report service.

## Behavior reference

Schedules, strategies, provisional versus completed candles, market-cap metadata,
PDF contents, routing, retries and delivery rules are maintained in the
[S&P 500 PDF reports section of the canonical alert reference](../TELEGRAM_ALERT_LOGIC.md#sp-500-pdf-reports--local-update-production-not-yet-verified-2026-11-23).

## Installation

1. Install `requirements-runtime.txt` in the worker environment. Standalone Windows/Linux builds include ReportLab and its resources.
2. For a new installation or this daily-report upgrade, with the worker stopped, apply `cloud/stock-reports.sql` as the database schema owner, after the existing Postgres runtime/worker-role migrations. It creates/upgrades private report tables (including the daily timeframe constraint) and enables the managed policy when no explicit report preference exists. It preserves permanent assets and is safe to rerun.
3. Deploy the updated worker and restart it. No provider calls or Telegram sends occur merely from applying the migration. An explicit existing `stock_reports.enabled=false` remains disabled.

Without the initial migration, report-enabled workers log `Stock reports unavailable`. Existing installations must also reapply the migration before deploying the daily-report upgrade; the older timeframe constraint rejects daily runs. The legacy rotating policy remains stored for rollback but is bypassed while reports are enabled. Turning reports off restores the old rotating behavior if its policy remains enabled. Old findings and delivery history are retained.

## Diagnostics and validation

`stock_reports.status(store)` returns the latest 12 run summaries: state, timeframe, period, evaluated/universe counts, missing/insufficient coverage count, and next retry. The local `GET /api/stock-reports` adapter is available in explicit disposable-database test mode. Production retains the existing authenticated-Supabase-only API restriction; this release does not publish a new hosted dashboard endpoint. Operators can inspect the private report tables through the administrator connection. PDF bytes remain private to the worker database and Telegram attachments.

Run `test_stock_reports.py` and `test_stock_report_schedule.py` for calendar, detector integration, PDF extraction, membership fallback, and Telegram transport tests. Database tests require the existing explicit disposable `trading_bot_test` setup; `scripts/setup-postgres-tests.py` now installs the report migration. Install `pypdf` for PDF extraction tests. These tests mock provider downloads and Telegram sends. Render representative generated PDFs and inspect first/last pages as part of layout verification.
