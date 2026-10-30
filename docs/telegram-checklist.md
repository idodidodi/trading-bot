Telegram checklist trial

Each new alert receives a **✓ Done** button. Done replaces its text with a compact **✓ Completed** message and an **↩ Undo** button. Undo restores the original text and formatting in place. Completion is shared by everyone in a group; any group member who can tap the button can complete or restore it. Signal history and dashboard feedback are unchanged.

The completed placeholder stays in the feed so Undo remains available. This trial does not delete messages or require Telegram's 48-hour deletion window. State and original text are saved in Postgres and survive restarts. Old alerts sent before activation have no buttons.

Activation:

1. Apply `cloud/telegram-checklist.sql` as the database schema owner (after runtime and worker role provisioning).
2. Set `TELEGRAM_CHECKLIST_ENABLED=true` in the sending runtime's local environment and restart the app. The default is false.
3. Use a bot with no existing webhook or other getUpdates consumer. Do not remove an existing webhook blindly. One listener holds a database advisory lock per bot; use separate bot tokens for development and production when using different databases.
4. For development, enable Telegram delivery in local Settings as usual. Polling pauses while delivery is disabled. The runtime must remain running to process taps.
5. On the next real alert, tap Done, then Undo to verify the interaction. No alert is sent by enabling this setting alone.

To stop the trial, set the variable to false and restart. Existing Undo buttons require the listener to remain enabled to work. Keep it enabled until any needed alerts have been restored.

An existing webhook or competing poller produces a sanitized Telegram checklist HTTP 409 error in the worker logs. Network failures retry through the runtime's worker supervisor; offsets advance only after an update is handled. Repeated edits are safe if Telegram accepted the edit before a database transaction failed.

Approved three-second deletion variant: see [Telegram dismissal tool](telegram-dismissal.md). That component is now active for enrolled personal groups; see [Personal Telegram alerts](telegram-personal-alerts.md). The activation instructions above apply only to the original Completed/Undo trial.
