# Chasing and alert-only position flow

```mermaid
flowchart TD
    A[New eligible personal 1h or 4h RSI alert] --> B[Chase check every five minutes]
    A -. Chase + look back .-> LB[Replay from original alert to click
Report historical finding, stop, or incomplete coverage]
    LB -. No finding or incomplete; session still active .-> B
    LB -. First finding in active session .-> H
    LB -. Stop reached or session already terminal .-> LS[Keep chase stopped]
    B --> C{Favorable move reaches user's chasing threshold?}
    C -- Yes --> D[Send stop notice and stop chasing]
    C -- No --> E{Price adverse to original close?}
    E -- No --> B
    E -- Yes --> F[Inspect new same-direction findings\n5m → 15m → 30m → 1h → 4h\nCapped at original timeframe]
    F --> G{Any eligible finding?}
    G -- No --> B
    G -- Yes --> H[Send first finding and stop chase search\nKeep I'm in available]
    A -. User clicks I'm in .-> I[Report entry to Live positions]
    H -- User clicks I'm in --> I
    I --> J[Wait for first fresh post-click minute close\nDisplay monitoring reference and rules]
    J --> K[Check fresh closed-minute price every minute]
    K --> L{Exit condition reached?}
    L -- No --> K
    L -- Yes --> M[Send one EXIT ALERT\nNo broker order or assumed closure]
    M --> N[Continue minute observations without repeat exit alerts]
    N --> O{Owner clicks I've exited?}
    O -- No --> N
    O -- Yes --> P[Record reported exit and stop monitoring]
    K -. New same-direction divergence .-> Q[Use trailing extension rules]
    Q --> K
    K -. First opposite divergence on original timeframe .-> R[Send informational Live positions alert\nStop opposite search; continue price monitoring]
    R --> K
```

The user's favorable chasing threshold defaults to 0.5% and uses the original signal
close. It is separate from position exits, whose percentages use a displayed market
reference until a trading platform supplies actual fills.

Without another divergence: initial loss/profit alerts are ±0.5% for 1h originals and
±1% for 4h. With an additional same-direction divergence: activate a 0.5% trail at +1%;
for 4h, widen to 1% at +2%. The active trail replaces the original loss stop. A chase
finding already known when entry is reported qualifies; a third finding is unnecessary.

Done/Undo dismisses messages; I've exited reports that the position is no longer held.
Data failures suspend exit evaluation and generate a warning, followed by a recovery
notice when fresh data resumes. The check cadence is nominal and provider-dependent.
See [the full operational specification](telegram-position-monitor.md).

Earlier annual comparison results remain pinned to their recorded implementation and
minute chase-check cadence. This updated flow is not a claim of a deployed worker.

The [lookback specification](chase-lookback.md) defines retrospective results and terminal-state preservation. The return to live chasing applies only while the original session remains active.
