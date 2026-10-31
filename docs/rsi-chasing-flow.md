# First-divergence chasing flow

```mermaid
flowchart TD
    A[New personal 4h RSI divergence alert] --> B[Start chase using original candle close and saved user settings]
    B --> C[On each fresh closed minute]
    B -. User reports entry while chasing .-> J
    C --> D{Favorable move reaches user's threshold?}
    D -- Yes --> E[Send favorable-stop alert and stop chasing]
    D -- No --> F{Price is adverse to original close?}
    F -- No --> C
    F -- Yes --> G[Inspect newly available same-direction divergences\n1m → 5m → 15m → 30m → 1h → 4h]
    G --> H{Any eligible divergence?}
    H -- No --> C
    H -- Yes --> I[Send first eligible finding and stop chasing\nKeep I'm in and Done available]
    I --> J{User clicks I'm in?}
    J -- Yes --> K[Check for opposite 4h divergence every 5 minutes]
    K --> L{Opposite divergence found?}
    L -- No --> K
    L -- Yes --> M[Send Live positions alert and stop opposite search]
    I --> N[Done removes the message after a 3-second Undo window]
```

The favorable-stop setting is a positive percentage, default **0.5%**: upward for
bullish signals and downward for bearish signals, anchored to the original close.
Settings changes apply to new sessions. The threshold stops monitoring; it does not
place or close an order.

The smallest eligible timeframe wins within a minute. The first finding stops the
search regardless of its timeframe; there is no required ladder of confirmations.
Signals already known at the previous check do not become new when price turns adverse.
A return to the original close after adverse movement can send a non-terminal notice,
at most once per rolling minute per user. Neither Done nor an alert reports a trade fill.

The backtest runs separately: every original is retained, and observational follow-ups
found after a strategy stop are labeled separately from strategy-eligible alerts.
