# Reminder for the automated-entry milestone

Before designing or implementing automated entries/exits, revisit this rule with the
owner and backtest it separately. Recorded and clarified 31 October 2026. This reminder
is tied to that milestone, not a scheduled notification.

## Confirmed: no additional same-direction divergence

The owner confirmed symmetric exits measured from the position's entry price, applying
while no new same-direction divergence has appeared:

| Signal timeframe | Loss stop | Profit target |
|---|---:|---:|
| 1h | 0.5% adverse | 0.5% favorable |
| 4h | 1% adverse | 1% favorable |

Apply favorable/adverse direction relative to a long or short position, respectively.
These are position exits, distinct from the original alert candle's monitoring threshold.

## Additional same-direction divergence: trailing extension

The owner added: after another divergence, use a **1% milestone**, do not close
immediately, and use a **0.5% trailing stop**.

The working interpretation awaiting confirmation is: replace the immediate fixed-profit
exit, wait for **+1% favorable movement from entry**, then arm a **0.5% price trail**
from the best price reached. For a long this trails the highest price; for a short it
trails the lowest price. This is a proposed interpretation, not an approved state machine.

Outstanding details before implementation:

- Confirm that +1% from entry activates the trail for both 1h and 4h signals, rather
  than representing a different threshold or price anchor.
- Confirm whether the original loss stop remains active before trailing activation.
- Define the qualifying additional divergence: eligible timeframe and when it becomes
  available. Define treatment of repeated divergences and a threshold crossing in the
  same candle; never infer intraminute order from OHLC alone.
- Specify whether the original signal timeframe or the follow-up entry timeframe
  determines the initial stop/target when they differ.
- Define execution details, costs and gaps before live use. A trailing trigger does not
  guarantee a fill at the trigger price.

The current bot reports signals and user-declared entry. This note does not activate
broker orders, position closing, or new live risk controls. The existing configurable
favorable-move chasing stop remains a separate monitoring rule.

Once clarified, compare this exit policy with the preserved 1m and 5m-and-up variations
using the same data, realistic cost assumptions and explicit intraminute ordering limits.
