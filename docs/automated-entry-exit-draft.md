# Reminder for the automated-entry milestone

Before designing or implementing automated entries/exits, bring this proposed rule back
to the owner for clarification and a separate backtest. Recorded 31 October 2026.
This is a planning reminder tied to that milestone, not a scheduled notification.

The owner proposed closing a position if price goes against it without first rebounding
and without another divergence. The stated thresholds were **0.5% for 1h** and
**1% for 4h**. The owner also mentioned taking profit on a 0.5% downward move.

Do not implement a symmetric stop-loss/take-profit interpretation until confirmed.
Questions to resolve at the automated-entry milestone:

- Does 1h mean a 0.5% loss stop and a 0.5% profit target, and 4h mean 1% for both?
- Are thresholds anchored to the actual entry/fill price, rather than the original alert
  candle close, and mirrored for bullish and bearish positions? A downward move is
  adverse for a long and favorable for a short.
- Does the rule apply only while no new same-direction divergence has appeared?
- Does any rebound change the rule, and what happens after another divergence?
- Which timeframe determines the threshold: the original signal or the follow-up entry?

These position exits are separate from the existing favorable-move **chasing stop**,
which stops alert monitoring. The current bot reports signals and user-declared entry;
this note does not enable broker orders, position closing, or new live risk controls.

Once clarified, compare this exit policy with the preserved 1m and 5m-and-up variations
using the same data, real cost assumptions, and explicit intraminute ordering limits.
