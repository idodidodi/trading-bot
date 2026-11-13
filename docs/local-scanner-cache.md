# Linux working cache: stage one

Implemented 13 November 2026. Opt-in; production activation has not been verified.

The scanner keeps candle working copies and Tiingo response payloads in a private
SQLite WAL database on Linux. Postgres continues to own configuration, worker
ownership, baselines, provider quota reservations, signals, deliveries and orders.
This is not an offline scanner: shared-state/network failures still stop dependent
work, and the existing effects/ownership gates remain mandatory.

## Behavior

- `TRADING_LOCAL_CACHE_DIR` must name a dedicated absolute private directory (0700).
  Empty keeps hosted-only behavior. The database destination/credential hash scopes
  all entries, so a different destination cannot reuse publication acknowledgements.
  No DSN or provider credential is stored in the cache.
- `managed_assets.cache_candles` and provider backtests store acknowledged candle
  copies locally. Only new/changed rows are upserted to Supabase, including revised
  historical prices and asset mappings. Unchanged rows are republished after 24h
  to repair out-of-band changes. Online history is not pruned in this stage.
- Acknowledgements commit locally only after the hosted transaction commits. A
  failed or ambiguously acknowledged hosted write remains eligible for an
  idempotent retry. Concurrent local publishers serialize; local I/O failures
  raise a sanitized error instead of silently claiming a successful sync.
- Tiingo's five-minute response cache is local when enabled. A small Postgres clock
  query checks freshness; misses still reserve the shared hourly/daily request
  budget in Postgres before contacting the provider. Provider errors consume their
  reservation as before. Old hosted responses are neither downloaded nor deleted.
  Local expired response entries are removed on writes after 24 hours.
- Repeated CSV backtests read their hosted candle input once per five minutes.
  They may see hosted edits up to five minutes later. Empty results are not cached;
  existing CSV fallback and insert-on-conflict-do-nothing behavior is preserved.
- Hosted charts, hosted replays and historical follow-ups keep using the full
  existing online candle tables, including while Linux is off. No UI/schema or
  Cloudflare production deployment is required for this stage.

Counters report response hits/misses, candle rows published and candle writes
avoided. They do not measure billed egress. Compare fresh billing samples over at
least 24 hours under comparable workloads; do not promise a particular saving.
The scanner already calculates live signals from provider responses in memory;
this change removes redundant hosted transfers, not the need for fresh market data.

## Rollout and rollback

1. Run the local/fixture tests on a disposable Postgres database. Compare cold,
   warm and revised-candle results. Do not start a second production scanner.
2. After publication approval, install the verified source in the existing Linux
   service's release directory using the current source-update procedure. Preserve
   its credentials, DATA_DIR, ownership controls and existing configuration.
3. Create a new service-user-owned directory, for example
   `/home/ido/.local/share/trading-bot-working-cache`, with `mkdir -m 700`.
   Set `TRADING_LOCAL_CACHE_DIR` in the existing service's private environment and
   restart that existing service once. Do not reuse retired SQLite databases.
4. Check the deployed version, current scanner heartbeat, errors, hosted charts,
   cache counters and free space. No test alerts/orders. The first candle publish
   is a cold full upsert; later refreshes should show writes avoided.
5. Compare daily Supabase egress and size separately. There is no immediate hosted
   database size reduction: the dashboard copy remains online. Moving bulk history
   offline and introducing UI jobs is a separate migration requiring compatibility
   and backup/restore checks before online deletion.

Rollback: unset the new variable and restart the existing service using the
verified prior source. Shared state has never moved, so no signal/order replay or
runtime data restoration is needed. After an online restore, clear the working
cache only with the service stopped (or use a new cache directory) to force a
complete republish; otherwise reconciliation can take up to 24 hours.

## Health, capacity and backup

With the same private environment loaded (never print it):

```sh
python3 -m local_cache health
python3 -m local_cache backup --destination /private/backup/path/cache-YYYYMMDD.sqlite3
```

Health reports SQLite integrity, local rows/bytes, free disk and counters. Exit 2
means failure/warning; exit 1 means disabled. Alert when free disk falls below 10%
or 5 GiB, whichever is larger. Check this alongside the existing scanner heartbeat
and Supabase monitor after rollout. No new schedule is activated by these files.

Backup uses SQLite's consistent backup API, verifies integrity, creates a 0600
file and refuses overwriting. Do not copy a live SQLite file without its WAL.
Transfer verified backups off-machine using the operator's approved backup
location and monitor their age. In this stage the local cache is disposable and
can be rebuilt from hosted/provider data; a backup is not a substitute for the
existing hosted shared-state backup. Future local-only history must have tested
off-machine recovery before deleting its online copy.

Read-only host checks found 171 GiB free on 13 November and 5.6 GiB available
RAM on 11 November. The existing service is active at
`/home/ido/apps/trading-bot-postgres`; its source-update timer was inactive on
13 November. Do not assume automatic rollout. Recheck at rollout; these are
historical measurements, not capacity guarantees.
