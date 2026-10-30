# Telegram dismissal tool

Status: the owner approved this interaction in a private Telegram demo on 30 October 2026. It is now integrated for enrolled personal alert groups through `telegram_personal.py`; see [Personal Telegram alerts](telegram-personal-alerts.md). The reusable component itself starts no listeners or network operations.

The reusable component is `telegram_dismissal.DismissibleMessage`:

1. The message has a **✓ Done** button.
2. Done replaces the text with **Deleting… 3 seconds to undo** and **↩ Undo**.
3. Undo within three seconds restores the original text and entities in the same message.
4. Without Undo, the worker deletes the message after the grace period. No completed placeholder remains.

The Undo button is attached to the temporary message, rather than a floating Telegram notification. The grace period starts after the edit succeeds. Network and scheduling delays can make actual deletion later than three seconds. Telegram's normal deletion permissions and age limits apply. Group deletion affects everyone.

## Integration contract

The module has no side effects on import and starts no listeners or timers. It takes an injected `call(method, body)` Telegram API client, keeping bot credentials outside message state. It is separate from `telegram_checklist.py`, which implements the earlier persistent Completed/Undo trial.

Create one instance for an existing message with its numeric chat ID, message ID and original text/entities. Attach `item.keyboard()` to that message. Set `user_id` to restrict taps to a particular user; leaving it unset allows members who can tap the message's buttons to act on it. Route incoming callbacks through `item.handle(query, call)`. Unrelated chats, message IDs, users and callback keys are ignored.

The owning worker must serialize handling and expiry for each message. Call `item.expire(call)` when its `delete_at` deadline is due; do not block the callback listener with a three-second sleep or a long poll while a deletion is pending. Repeated Done taps do not extend the deadline. A late Undo does not restore the message.

Persist `item.snapshot()` after state changes, including if callback acknowledgement fails after a successful edit. `DismissibleMessage.restore(snapshot)` reconstructs pending deadlines and original content. Snapshots contain message content, not bot tokens; store them with the application's normal private data controls. Persistence and restart recovery are responsibilities of the future integration, not built into this component. The future integration must also reconcile ambiguous network outcomes and crashes between Telegram effects and saving state, including already-deleted messages.

The API adapter must sanitize errors and treat repeated identical edits as successful. API failures leave a pending delete eligible for retry. If deletion is unavailable (for example, due to age or permissions), the future integration must surface that failure rather than claiming the message disappeared.

No database migration, environment flag, production listener or alert routing is changed by this tool. Choose its destinations and retention/recovery policy only when the owner supplies the intended use.

## Validation

Run:

```sh
.venv/bin/python -m unittest test_telegram_dismissal test_telegram_checklist -q
```

Tests cover timed deletion, Undo and exact content restoration, deadline boundaries, duplicate clicks, callback ownership, state serialization and API failures. They send no Telegram messages.
