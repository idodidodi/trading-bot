# Telegram alert logic

Last reviewed: 2026-11-02.

This is the reference for changes to Telegram signal-alert behavior. The following
rules were checked against local source; production deployment is not verified by
this document. Existing detailed design documents may describe older versions.

## Chase cancellation

- Original alert Chase controls and personal chase cards include **Stop chasing**.
  Active automatic crypto watch cards also include this control, including originals
  outside the 1h/4h position-monitoring range.
- While chasing is in progress, personal 1h/4h chase cards show only **Done**,
  **I'm in**, and **Stop chasing**. They omit **Chase** and **Chase + look back**.
  Original signal cards retain their launch/lookback controls; automatic watch cards
  retain their existing entry restrictions and show Done plus Stop chasing.
- Only the bound Telegram recipient, bot, chat and message may operate the control.
  The message must still be active. Done remains message dismissal with its existing
  Undo behavior; it does not cancel a watch.
- Stop applies to the selected owner's pre-entry watch. It marks active or
  completed-but-unentered watches `stopped_manual`, clears the polling deadline,
  cancels pending lookback and increments the revision. An in-flight market/history
  result cannot overwrite that revision. Repeat clicks are idempotent.
- Pending outbox events for that watch are suppressed. Stopped watches are also
  excluded from later delivery. A Telegram send already in progress may still arrive;
  cancellation cannot retract a message already sent.
- A shared crypto watch stops in both Chasing and Crypto Auto Chase destinations.
  Same-direction superseded cards follow the existing redirect to the active watch;
  opposite-direction replacements require the current card.
- Start, Lookback and I'm in on an old stopped card do not restart that watch or
  report entry. Newly eligible independent signals may create new watches; this is
  not a permanent asset mute or a global preference change.
- Stop does not close trades, submit orders, or stop an already reported position.
  A separate reported-position session from a shared auto watch remains monitored.
- Confirmation is returned through the Telegram button response. Existing messages
  retain their prior controls; newly sent cards carry the additional button after
  the worker update. No schema or Cloudflare frontend change is required.

Sources: `chasing_runtime.py` (`chase_buttons`, `deliver_pending`, `callback`,
`tick`, `_enroll_crypto`), `divergence_chasing.py` (`step`), `chase_lookback.py`
(`request`), and `telegram_dismissal.py` (`DismissibleMessage`).
