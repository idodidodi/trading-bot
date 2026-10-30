# Personal Telegram alerts

Each enrolled dashboard user has a separate mapping for all 13 categories: General and RSI, Pin Bars, RSI + PB across Forex, Crypto, Stocks and Other. The original installation routes remain in use for the existing group set. Personal delivery is additional and never falls back into another person's General group.

In the online dashboard, open **Settings → My Telegram alerts**. All categories default on. Save unchecked categories to stop future deliveries to your personal groups. Re-enable them to receive new alerts without replaying events already suppressed. Preferences are stored by installation and authenticated user; viewers can edit their own preferences without gaining installation editing rights. Clients cannot supply another user ID or rewrite the destination mapping.

Personal messages have **✓ Done**. Tapping it displays **Deleting… 3 seconds to undo** with **↩ Undo**. Undo restores the exact original text and entities. Without Undo, the listener deletes only that recipient's message. Only the enrolled Telegram user can operate its buttons. Group membership should remain exactly that person and the bot.

## Provisioning

1. Create the groups through the recipient's Telegram account with the bot as the only additional member. For Ido's initial set, names have `_` before the original group names.
2. With no conflicting poller/webhook, collect creation updates using `scripts/record-personal-telegram-groups.py --telegram-user-id USER_ID --output data/personal-groups.json`. The script verifies two members and both identities, then records chat IDs. It sends no messages. Coordinate with the active listener when onboarding additional users; do not run a second getUpdates consumer alongside it.
3. Apply `cloud/telegram-personal.sql` after the Postgres runtime/dashboard and worker-role migrations. The migration adds private registration/preferences tables, a self-scoped `/api/telegram-alerts` operation, the worker recipient reader, delivery-decision ledger and persistent message state.
4. The initial owner's verified mapping can be registered with `scripts/deploy-telegram-personal.py --group-map data/personal-groups.json`, using the existing Supabase CLI credential. This helper provisions the installation owner only; registration of another dashboard user requires an explicit administrator mapping to that user's Auth ID after verifying dashboard membership and their Telegram identity.
5. Deploy `platform_app.py`, `telegram_routing.py`, `telegram_personal.py`, `telegram_checklist.py`, and `telegram_dismissal.py` together. Restart the scanner. Publish the updated dashboard bundle.

No bot token is stored in registration or preference tables or browser assets. User-specific chat IDs are visible only in that user's settings; the worker receives all enrolled destinations through a restricted function. The initial verified map lives in ignored `data/telegram-personal-ido.json`.

## Delivery and recovery

Each personal destination uses the existing Telegram delivery reservation and deduplication. A preference decision is recorded for each recipient/event; a suppressed event remains suppressed after re-enabling. Findings predating enrollment are excluded when their timestamps are available. Events queued but never evaluated use the settings in force at first delivery evaluation. An already in-flight Telegram request may finish after a preference is saved.

The callback listener holds one database advisory lock per bot and checks the runtime ownership/effects gate. Pending deadlines and original message content live in Postgres. While a deletion is pending, polling uses short requests so a ten-second long poll cannot delay the three-second grace period. Network or database outages can delay deletion beyond three seconds. Telegram permissions and the 48-hour deletion limit still apply.

The API adapter accepts repeated identical edits and already-missing messages, allowing retries after an ambiguous network result. Callback acknowledgements occur after the message state commits. A crash between a Telegram effect and the database commit can replay that effect; message IDs and original content remain bound to the same recipient. Delivery timeouts can still produce duplicates under the pre-existing at-least-once delivery policy.

Signal evidence, dashboard feedback and the existing shared-group memberships are untouched. Keep each personal group's membership private; membership changes require review before using it for personal alerts.

## Validation

Run the explicit disposable-Postgres setup and the Telegram regression tests:

```sh
TRADING_TEST_DATABASE_URL='host=/private/tmp port=55439 dbname=trading_bot_test' .venv/bin/python -m unittest test_telegram_personal test_telegram_routing test_telegram_delivery test_telegram test_telegram_dismissal test_telegram_checklist -q
node --test web/telegram-alerts.test.mjs web/versions.test.mjs
```

Tests cover defaults, self-only preference updates, viewer access, stale revisions, unauthorized reads, private tables, no cross-person fallback, disabled-event suppression, independent deliveries, callback ownership, content restoration, deadline boundaries and persistence.

## Initial activation, 30 October 2026

Ido's 13 `_` groups were created and verified to contain only Ido and `@idodiTradingBot`. Their IDs were saved in the private mapping file and registered to the verified installation-owner dashboard account. The SQL migration and dashboard controls are deployed; the updated Linux service is active with all 13 preferences enabled. Read-only verification confirmed a processed listener cursor, four worker-role connections, and no recent database retries, listener API errors, ownership failures or tracebacks. No setup-test message was sent.

The listener reuses the existing database-wide `WorkerGuard` ownership protection. It does not allocate an extra lock connection: production allows one owner session plus a pool of three. Separate installations/databases must use separate bot tokens or one deliberately coordinated Telegram update consumer.

## Original groups and faster callback handling

`TELEGRAM_GROUP_DISMISSAL_ENABLED=true` enables the same three-second Done/Undo behavior on **new text alerts delivered through the original group routes**. Those message instances allow any member of that group to use Done/Undo; personal copies retain their enrolled-user restriction. The listener must be deployed with the personal-message schema before enabling the flag. This setting does not edit old messages, reset delivery reservations or replay alerts.

The listener now loads the update offset once and retains the next deletion deadline in memory. It refreshes the deadline after callbacks and expirations, and persists the update cursor once per received batch. It no longer scans pending messages and re-reads the offset before every poll. Ownership/effects checks remain before polls and message edits/deletions; pending deadlines are recovered from Postgres on restart. This reduces the initial response delay without shortening the three-second Undo window. Network/database latency still affects the visible response and deletion time.

## Explicit demo isolation

An explicitly queued test uses a unique `telegram-demo:` notification key and a stored route pointing to its requested demo chat. The delivery worker still enforces its normal ownership, effects, dry-run and per-destination reservation checks. These tests skip personal-group fan-out, so the same demo is not copied into General or other personal groups. A demo must be explicitly requested; never reset a delivery record or enqueue a duplicate to retry an uncertain send. Check the original queue entry's status instead.
