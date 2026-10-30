# Telegram asset names and internal delivery records

`alert-symbol-map.xml` is the viewable, public-safe name map used by
`alert_display.py`. Add aliases as `<symbol>` elements inside the appropriate
`<asset name="...">`. XML requires `&amp;` for an ampersand. Restart the worker
after editing; the map is cached for the process lifetime and bundled in both
standalone builds.

Telegram headers show the asset name and timeframe. Qualified feed IDs use the
final ticker for name lookup; unknown tickers remain readable. Provider names
appear at the bottom as `Provider: Yahoo`, for example. Cash/derived instruments
and futures have separate names. Formatting happens at the send boundary, so
queued alerts use the same rules without changing signal IDs or delivery keys.
All `Source:` lines are removed from public messages.

Successful routed deliveries write a `telegram-delivered` record to the internal
Postgres `bot_runtime.activity_log` in the same transaction that saves the
Telegram delivery result. Its JSON `details` holds the Telegram message ID,
chat ID (Telegram message IDs are scoped to a chat), sender UUID, device,
environment, delivery ID, event ID, and original message text with feed details.
It follows the existing activity-log retention policy; bot tokens are excluded.

Example lookup using the actual chat and message IDs:

```sql
SELECT occurred, details::jsonb
FROM bot_runtime.activity_log
WHERE event = 'telegram-delivered'
  AND details::jsonb->>'chat_id' = '<chat-id>'
  AND details::jsonb->>'message_id' = '<message-id>'
ORDER BY occurred DESC;
```

Deployment requires updating and restarting the production Linux worker, or
building/installing a standalone release. Editing the Mac checkout alone does
not update the running production worker.
