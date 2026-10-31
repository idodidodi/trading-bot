# Position-exit rules and automated-entry milestone reminder

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

The alert-only implementation is described in [Telegram position monitoring](telegram-position-monitor.md). The original loss stop is replaced by the active trail. On reaching +2% for 4h, the 1% trail becomes the only stop; the widening transition is applied literally and remains latched.

Before future broker execution, separately validate the alert policy's choices:

- Eligible additional divergences are currently fresh same-direction 5m-or-higher
  signals, capped at the original timeframe. Repeated signals do not reset the trail,
  and an existing exit wins when a new divergence arrives on the same observation.
- The original signal timeframe currently determines stop/target percentages, even
  when the follow-up finding uses another timeframe.
- Add actual fill prices, execution costs, gap behavior and broker order semantics.
  A sampled trailing alert does not guarantee a fill at its threshold.

The branch now implements these rules as sampled Telegram exit recommendations after user-declared entry. It is not deployed. This does not activate broker orders or close positions. The existing configurable
favorable-move chasing stop remains a separate monitoring rule.

Once clarified, compare this exit policy with the preserved 1m and 5m-and-up variations
using the same data, realistic cost assumptions and explicit intraminute ordering limits.
