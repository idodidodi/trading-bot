# Postgres deployment

Postgres is the sole authoritative runtime database. The Linux scanner, request budgets, delivery reservations, paper journal and dashboard APIs share canonical state. Worker ownership and the administrator-controlled effects gate prevent overlapping installations and premature effects.

Use the [installation guide](https://github.com/idodidodi/trading-bot/blob/main/POSTGRES_MIGRATION.md) for operation and recovery. Production runs as a boot-enabled Linux user service. Browser-only Mac use starts no scanner and needs no worker database credential.

Before deploying changes, run the disposable Postgres suite and browser tests. Preserve the deployed source revision, private configuration and a current administrator backup. Retain finding IDs, feedback revisions, strategy baselines, pending setup versions, provider budgets and delivery identities.

SQLite import/recovery tools are offline operations. Keep retired installations and snapshots outside active paths. After Postgres writes or effects, repair Postgres or reconcile a reviewed recovery export instead of restarting stale SQLite.

The [previous SQLite/mirror deployment plan](https://github.com/idodidodi/trading-bot/blob/main/docs/archive/sqlite-runtime/DEPLOYMENT_PLAN.md) is archived historical reference.
