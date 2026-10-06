# New Supabase and Cloudflare Pages projects

The local app and online UI use the same web assets. Cloud deployment requires your accounts; nothing here creates a paid service or publishes credentials automatically.

1. Create a Supabase project, create an Auth user, and run `schema.sql` in its SQL editor. Run the script once (named policies are intentionally not recreated). Insert an installation UUID with the Auth user's UUID into `dashboard_installations` using the SQL editor. Do not expose the dashboard until ownership policies are installed.
2. Copy `functions/sync` to your Supabase functions directory and deploy `sync` with platform JWT verification disabled (`--no-verify-jwt`). The function authenticates its own private machine token, distinct from browser access. Set `SYNC_TOKEN` to a strong random token and `INSTALLATION_ID` to the installation UUID as function secrets. Supabase supplies the service-role key to the function; never copy it into web assets or the local app.
3. Add `CLOUD_SYNC_URL=https://YOUR_PROJECT.supabase.co/functions/v1/sync` and `CLOUD_SYNC_TOKEN=YOUR_PRIVATE_MACHINE_TOKEN` to your local environment. Keep these values private. Restart the local app. The worker will import signals, historical reports/candles, logs and managed asset configuration, then keep mirroring local changes. Uploads are bounded and duplicate-safe.
4. Create a deployment directory containing a copy of `web/` and `_redirects`. In that copy's `web/config.js`, set `supabaseUrl`, the project's public publishable key, and `installationId`. These are browser configuration, not service-role credentials. Deploy this directory as a Cloudflare Pages static project, with no build command. Never upload the repository, `.env`, local SQLite or `data/` as static assets.
5. Open `/assets`, sign in, add/edit a test asset, then check the local Assets screen for the new desired revision. The scanner applies configuration at the next cycle. Test online/offline changes before using a large asset batch.

Online access is owner-scoped. The UI can edit asset selections and feedback, not candles/evidence/strategy rules or delivery. Online configuration is read by the local worker; concurrent local/online edits cause a visible sync conflict rather than overwriting a pending local configuration. Resolve the configuration conflict explicitly by reloading/reapplying the desired edit locally.

Feedback is pulled into local SQLite with a durable cursor. Concurrent feedback is retained in local conflict records; deployment acceptance must include conflict resolution and offline testing. The current cloud setup has not been deployed or exercised against a real Supabase project in this workspace. Run the SQL/integration checks in an isolated project first.

TradingView's public chart URL does not reliably select a historic date. The internal chart shows the exact persisted OHLC window. Local external links use your verified exchange-qualified mapping. Hosted external links also use synced mapping; if a mapping cannot be verified, the internal chart remains available.

## Reproducible checks

`node test_function.mjs` verifies the private ingestion function using mocked network/platform APIs. For SQL smoke checks in a fresh disposable PostgreSQL database, run `test_auth_stub.sql`, `schema.sql`, then `test_api.sql` with `psql -v ON_ERROR_STOP=1`. Never run the auth stub or test data in a real Supabase project. PostgreSQL roles are cluster-wide; the test stub reuses existing test roles without changing them. SQL tests cover owner isolation and denial of direct evidence writes.

## Family Trading Bot setup status (6 October 2026)

Project: `bwejkkboophbfeiwizbu`, installation: `8734f8e3-8629-4d5f-b0ff-350fb4a6b911`. The existing owner Auth user, API/ingestion functions, sequence column and owner read policies were verified. Anonymous REST reads and dashboard API calls are rejected. The `sync` Edge Function is deployed and `INSTALLATION_ID` is configured.

Local `.env` retains all previous values and has additive cloud connection settings. The private machine token matches the saved Supabase `SYNC_TOKEN`. The legacy gateway JWT check was disabled with explicit approval; the function enforces private-token authentication. A correct token returns HTTP 200 and an incorrect token returns HTTP 401. The initial upload completed: 35,118 records were acknowledged and the queue drained. The local app was restarted with ongoing sync enabled; its sync status shows no error and no conflicts. A trusted certificate bundle is configured for this machine's Python installation.

The static-only deployment bundle is ready at `data/cloudflare-pages/`, with this project's public browser configuration. Deploy that folder's contents to Cloudflare Pages. It contains only browser assets and routing; local `.env` and databases are excluded. Cloudflare publishing and browser sign-in remain to be verified. The owner dashboard API was verified against the uploaded assets, backtests and logs.

For a bounded manual catch-up without starting scanning or Telegram delivery, run `python3 cloud/sync_once.py`. The durable local queue retains unacknowledged records if the command is interrupted.
