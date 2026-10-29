# Hosted dashboard

The dashboard and Linux worker use the same Postgres database. Browser access uses Supabase Auth and owner/editor-scoped SQL APIs. There is no ongoing SQLite upload or feedback synchronization.

Follow the [Mac/Linux installation guide](https://github.com/idodidodi/trading-bot/blob/main/POSTGRES_MIGRATION.md) for provisioning, credentials, migration, deployment and recovery. Keep administrator credentials on the Mac; Linux uses a restricted worker login. Browser assets contain only the publishable key, Supabase URL and installation ID from [browser-config.json](https://github.com/idodidodi/trading-bot/blob/main/cloud/browser-config.json).

## Database and functions

After existing dashboard prerequisites, apply these administrator scripts in order:

1. [postgres-runtime.sql](https://github.com/idodidodi/trading-bot/blob/main/cloud/postgres-runtime.sql)
2. [postgres-dashboard.sql](https://github.com/idodidodi/trading-bot/blob/main/cloud/postgres-dashboard.sql)
3. [postgres-worker-role.sql](https://github.com/idodidodi/trading-bot/blob/main/cloud/postgres-worker-role.sql)

Do not reapply older dashboard migrations over the adapter. Keep routing and effects disabled until import verification succeeds. Browser roles cannot directly read worker tables; scoped security-definer APIs enforce installation membership.

Deploy the [backtests function](https://github.com/idodidodi/trading-bot/tree/main/cloud/functions/backtests) before activating routing. Preserve its deployed authentication settings; it validates the caller's user token itself. [CSV backtests](https://github.com/idodidodi/trading-bot/tree/main/cloud/functions/csv-backtests) also run without Linux.

The retired sync ingress rejects writes once Postgres routing is active. Legacy tables/functions remain as migration evidence and recovery compatibility, not the active source of worker state. Do not start an old mirror worker.

## Static deployment

Run `node cloud/build.mjs`, then deploy only `dist/` using [wrangler.jsonc](https://github.com/idodidodi/trading-bot/blob/main/wrangler.jsonc). The build uses an explicit browser-asset allowlist and never reads local credentials or a database. A production-main push may trigger the configured Cloudflare Git build. Never publish the repository, private backups or worker configuration as static assets.

Verify owner/editor access, paginated histories, feedback, asset configuration and a hosted replay before starting a new worker. The [old cloud setup notes](https://github.com/idodidodi/trading-bot/blob/main/docs/archive/sqlite-runtime/CLOUD_README.md) are archived historical material.
