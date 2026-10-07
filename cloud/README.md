# New Supabase and Cloudflare Pages projects

The local app and online UI use the same web assets. Cloud deployment requires your accounts; nothing here creates a paid service or publishes credentials automatically.

1. Create a Supabase project, create an Auth user, and run `schema.sql` in its SQL editor. Run the script once (named policies are intentionally not recreated). Insert an installation UUID with the Auth user's UUID into `dashboard_installations` using the SQL editor. Do not expose the dashboard until ownership policies are installed.
2. Copy `functions/sync` to your Supabase functions directory and deploy `sync` with platform JWT verification disabled (`--no-verify-jwt`). The function authenticates its own private machine token, distinct from browser access. Set `SYNC_TOKEN` to a strong random token and `INSTALLATION_ID` to the installation UUID as function secrets. Supabase supplies the service-role key to the function; never copy it into web assets or the local app.
3. Add `CLOUD_SYNC_URL=https://YOUR_PROJECT.supabase.co/functions/v1/sync` and `CLOUD_SYNC_TOKEN=YOUR_PRIVATE_MACHINE_TOKEN` to your local environment. Keep these values private. Restart the local app. The worker will import signals, historical reports/candles, logs and managed asset configuration, then keep mirroring local changes. Uploads are bounded and duplicate-safe.
4. Create a deployment directory containing a copy of `web/` and `_redirects`. In that copy's `web/config.js`, set `supabaseUrl`, the project's public publishable key, and `installationId`. These are browser configuration, not service-role credentials. Deploy this directory as a Cloudflare Pages static project, with no build command. Never upload the repository, `.env`, local SQLite or `data/` as static assets.
5. Open `/assets`, sign in, add/edit a test asset, then check the local Assets screen for the new desired revision. The scanner applies configuration at the next cycle. Test online/offline changes before using a large asset batch.

Online access is owner-scoped by default. To share an installation, run `sharing.sql` after `schema.sql` and `backtests.sql`. Invite the person under Authentication → Users; then add their Auth user ID to `dashboard_members` with role `editor`. Editors can view synced data and edit assets and feedback; the original owner keeps ownership. The installation ID is in `cloud/browser-config.json`. For the invited account ID, run `select id,email from auth.users where lower(email)=lower('person@example.com');` in SQL Editor. Grant access with `insert into public.dashboard_members(installation,user_id,role) values ('8734f8e3-8629-4d5f-b0ff-350fb4a6b911', 'USER_UUID_FROM_QUERY', 'editor') on conflict (installation,user_id) do update set role=excluded.role;`. Online configuration is read by the local worker; concurrent local/online edits cause a visible sync conflict rather than overwriting a pending local configuration. Resolve the configuration conflict explicitly by reloading/reapplying the desired edit locally.

Feedback is pulled into local SQLite with a durable cursor. Concurrent feedback is retained in local conflict records; deployment acceptance must include conflict resolution and offline testing. The current cloud setup has not been deployed or exercised against a real Supabase project in this workspace. Run the SQL/integration checks in an isolated project first.

TradingView's public chart URL does not reliably select a historic date. The internal chart shows the exact persisted OHLC window. Local external links use your verified exchange-qualified mapping. Hosted external links also use synced mapping; if a mapping cannot be verified, the internal chart remains available.

## Reproducible checks

`node test_function.mjs` verifies the private ingestion function using mocked network/platform APIs. For SQL smoke checks in a fresh disposable PostgreSQL database, run `test_auth_stub.sql`, `schema.sql`, then `test_api.sql` with `psql -v ON_ERROR_STOP=1`. Never run the auth stub or test data in a real Supabase project. PostgreSQL roles are cluster-wide; the test stub reuses existing test roles without changing them. SQL tests cover owner isolation and denial of direct evidence writes.

## Family Trading Bot setup status (6 October 2026)

Project: `bwejkkboophbfeiwizbu`, installation: `8734f8e3-8629-4d5f-b0ff-350fb4a6b911`. The existing owner Auth user, API/ingestion functions, sequence column and owner read policies were verified. Anonymous REST reads and dashboard API calls are rejected. The `sync` Edge Function is deployed and `INSTALLATION_ID` is configured.

Local `.env` retains all previous values and has additive cloud connection settings. The private machine token matches the saved Supabase `SYNC_TOKEN`. The legacy gateway JWT check was disabled with explicit approval; the function enforces private-token authentication. A correct token returns HTTP 200 and an incorrect token returns HTTP 401. The initial upload completed: 35,118 records were acknowledged and the queue drained. The local app was restarted with ongoing sync enabled; its sync status shows no error and no conflicts. A trusted certificate bundle is configured for this machine's Python installation.

The static-only deployment bundle is ready at `data/cloudflare-pages/`, with this project's public browser configuration. Deploy that folder's contents to Cloudflare Pages. It contains only browser assets and routing; local `.env` and databases are excluded. Cloudflare publishing and browser sign-in remain to be verified. The owner dashboard API was verified against the uploaded assets, backtests and logs.

For a bounded manual catch-up without starting scanning or Telegram delivery, run `python3 cloud/sync_once.py`. The durable local queue retains unacknowledged records if the command is interrupted.

## GitHub-connected Cloudflare Worker

The `family-trading-bot` Worker builds automatically from `main` in `idodidodi/trading-bot`. Set its root directory to `/`, build command to `node cloud/build.mjs`, and deploy command to `npx wrangler@4.147.0 deploy`. The checked-in Wrangler configuration serves only `dist/`, with a single-page fallback for dashboard routes.

The build copies an explicit list of browser assets, retains `/web/` URLs, and generates public Supabase settings from `cloud/browser-config.json`. It excludes tests, scanner code, local databases and `.env`. Optional build variables are `DASHBOARD_SUPABASE_URL`, `DASHBOARD_PUBLISHABLE_KEY`, and `DASHBOARD_INSTALLATION_ID`; private keys and machine tokens must never be supplied to this browser build.

Run `node cloud/build.mjs` locally to prepare the same static output. No npm dependencies or Python process are required by the UI build. Changes become live after their GitHub push and a successful Cloudflare build.


## Cloud backtest upgrade

Apply `backtests.sql` to the existing project, then deploy `functions/backtests` with legacy gateway verification disabled (`--no-verify-jwt`). The handler itself verifies the current ECC user JWT through Supabase Auth and checks ownership. The function validates the signed-in owner through `/auth/v1/user` and the owner-scoped SQL API before creating a job. Background replay uses only the stored historical candles and rule metadata. It does not use the private machine token or send Telegram alerts. The `dashboard_finish_backtest` and failure functions are restricted to `service_role`; completion is atomic and existing matching evidence/feedback is preserved. Browser sign-in uses tab-scoped refresh tokens and automatic refresh.

From the repository root, `SUPABASE_ACCESS_TOKEN` configured locally plus `./scripts/deploy-cloud.sh` applies the migration, sets/reads back `jwt_exp=604800`, deploys the backtests function and builds the static UI. `schema.sql` must be installed before `backtests.sql`; on subsequent upgrades, apply changes to `dashboard_api_base` rather than replacing the wrapper with the old schema API.

The backtest migration and `backtests` function were deployed on 6 October 2026; unauthenticated calls return HTTP 401. The project uses ECC signing keys. The gateway's legacy-only JWT verification was disabled with approval on 7 October 2026 for ECC compatibility. An unauthenticated invocation was then rejected by the handler itself with HTTP 401 (Sign in required). The function's own JWT and owner checks remain mandatory. The seven-day access-token expiry (604800 seconds) was saved and verified on 7 October 2026. Existing tokens receive the new expiry on sign-in or refresh.

## Kraken provider routing

Apply `providers.sql` after the existing schema and backtest migrations before publishing the updated Assets editor. It adds OANDA, Kraken, optional live 1h candles and per-timeframe provider validation to the base API and preserves the backtest dispatcher. Test in a disposable PostgreSQL database with `test_auth_stub.sql`, `schema.sql`, `test_api.sql`, `backtests.sql`, `providers.sql`, then `test_providers.sql`. Reapplying the migration is safe. Live Kraken scanning runs locally and needs no provider credential. OANDA uses local v20 credentials; run `scripts/activate-oanda.py` after configuring them to verify forex coverage before migration. Hourly historical replay is not included.

Tiingo forex asset controls are included in `providers.sql` (apply after the schema/backtest migrations; safe to rerun). Primary exchange is `Tiingo`, with optional per-timeframe `tiingo` routing. Credentials and API requests remain on the local scanner. The local backtest dialog also supports Tiingo, but hosted backtesting continues to replay stored CSV history.

For the existing hosted installation, set `SUPABASE_ACCESS_TOKEN` locally and run `python3 scripts/deploy-providers.py`. It applies only `providers.sql`, verifies Alpaca hourly asset validation, and checks that the existing backtest dispatcher remains installed. It does not change Auth settings or redeploy Edge Functions. The updated editor and favicon are included in `node cloud/build.mjs`; publish through the existing Cloudflare GitHub build. Hosted replay still uses stored CSVs and its existing timeframes; local replay additionally supports Alpaca/Tiingo and hourly history.

On 7 October 2026, `providers.sql` was applied to production through the Supabase SQL editor in Opera. Production verification returned true for Alpaca support, provider validation, and preservation of the backtest dispatcher. The hosted Assets page showed Alpaca US equities and successfully saved the existing DAX-ETF configuration with 1h enabled (revision 12). The hosted UI and candlestick favicon were already published by the GitHub build.
