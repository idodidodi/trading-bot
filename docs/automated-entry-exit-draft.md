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

The owner confirmed the following after another same-direction divergence. Favorable
thresholds are measured from entry, mirrored for long and short positions. Replace the
immediate fixed-profit exit with this trailing approach:

| Signal timeframe | Favorable movement from entry | Trailing distance from best price |
|---|---:|---:|
| 1h | Reach +1% | Activate 0.5% trail |
| 4h | Reach +1% | Activate 0.5% trail |
| 4h | Reach +2% | Increase trailing distance to 1% |

Keep the original loss stop active until trailing activation: 0.5% for 1h and 1% for
4h. For a long the trail follows the highest price; for a short it follows the lowest
price. Reaching a milestone activates/changes the trail; it is not an immediate profit
exit. The 4h change from 0.5% to 1% is a widening of trailing distance.

Outstanding details before implementation:

- Define the 4h widening transition precisely, including treatment of an existing
  protective stop level and whether the 1% trailing stage remains latched after retracement.
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
