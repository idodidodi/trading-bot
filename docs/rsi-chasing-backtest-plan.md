# RSI divergence chasing and backtest plan

Status: discussion draft; no runtime implementation or activation authorized by this document.
Prepared 31 October 2026 on `codex/rsi-chasing-backtest-plan`, from source commit
`908f3147da812cbe6949c2909246e3e0ad14d614`.
Updated with the user's favorable-move stop rule: configurable in personal Settings,
defaulting to 0.5% beyond the original signal close.
Extended with the voice-session requirements for volume, 4h-first research and descending
timeframes. See the [detailed research design and literature check](rsi-chasing-research-design.md).

## 1. Agreed behavior and scope

After an original RSI divergence alert, follow further divergences in the **same direction**:
bullish follows bullish; bearish follows bearish. Evaluate this chase every minute.
Send chase notifications to that recipient's personal **Chasing** Telegram chat, with
**Done** and **I'm in** buttons.

Only that recipient clicking **I'm in** confirms their reported entry and switches their
signal to **opposite-direction divergence monitoring**, evaluated every five minutes.
One user's click must not change the other user's state. On finding an eligible opposite
divergence, record it, send one logical alert to that user's personal **Live positions**
chat, and stop that signal's periodic opposite-divergence search. Delivery retries may
continue without searching for another opposite divergence.

The contemplated Live positions buttons are **Done** and **Close the deal**. No trading
account is connected for this feature. Order execution and actual closing are future
work. Do not make either button invoke an existing paper-trading or broker path.

Two existing dashboard users eventually need two additional private chats each: **four
chats total**. Users must be able to opt out through dashboard checkboxes analogous to
their existing Telegram preferences. Chat creation and registration will be manual.

Also consider a Chasing notification when price moves favorably from the original
signal candle close immediately, or returns to that close after drawdown, without
another divergence. Such notifications must be limited to **at most once per minute**
and, below the favorable-stop threshold defined next, must **not stop chasing**. Only
**I'm in** changes monitoring direction. Close-return trigger tolerances, expiry and
overlap behavior remain decisions below.

**Added requirement:** while the user is still chasing, stop that chase when price
reaches **at least the user's configured favorable-move percentage in the original
divergence direction beyond the original signal candle's close**, and notify the user
in their Chasing chat. The setting defaults to **0.5%** and must be editable through
the dashboard without editing code. With setting `p` in percent units, bullish stops at
`price >= original_close * (1 + p/100)`; bearish stops at
`price <= original_close * (1 - p/100)`. At the default these multipliers are 1.005 and
0.995. No further divergence is needed. This covers an immediate favorable move and a
recovery after drawdown. Measure from the original close, not the drawdown low/high or
a later chase candle.

This stops chasing without implying entry, profit on a trade, or a switch to opposite
monitoring. The return-to-close notification above remains non-terminal; reaching the
configured favorable threshold is the new terminal exception. Once I'm in has already been
accepted, the session follows its opposite-monitoring lifecycle instead of this chase rule.

**Backtest and chase additions from the voice session:** prioritize original **4h** RSI
signals and user-supplied TradingView CSVs for **4h, 1h, 30m, 15m, 5m and 1m** of the same
asset. Study signal-candle volume, defined success rates, net profitability under
explicit hypothetical trade policies, and whether waiting for further divergences helps.
The bot itself continues to send signals; it does not choose entries for the user.

The proposed new chase approach descends to lower timeframes **only while price moves
against the original signal**. After a same-direction 1h divergence, look for another
on smaller timeframes. A **1m divergence completes the chase search** because no smaller
supplied timeframe remains; send the final chase signal without inferring entry. A 5m
divergence alone does not stop the search. The exact ladder, adverse-price definition,
child search windows, missing-stage fallback and entry access after 1m completion remain
written decisions. The original-close configurable favorable stop remains in effect
while chasing, with 0.5% as its default.

This task produces a plan only. No chats, provider downloads, production queries,
scanners, alerts, account connections, orders, deployment or GitHub push were performed.
There are no backtest results or claimed strategy improvements.

## 2. Repository findings that shape the design

| Area | Current evidence | Consequence |
| --- | --- | --- |
| Signal rules | [`skills/rsi-divergence/SKILL.md`](../skills/rsi-divergence/SKILL.md), `platform_app.load_rules`, `scanner.detect`: Wilder RSI(3), tolerance 1 inclusive, BB(20,2), closing-price pivots, spacing 2–60, only P2 needs a band touch | Reuse the detector; choose whether chase reference pivots roll or remain anchored. Do not quietly define a new RSI strategy. |
| Actual alert timing | [`scanner.py`](../scanner.py) evaluates provisional and structurally confirmed signals. [`platform_app.py`](../platform_app.py), `Store.insert`, cancels default delivery for structural confirmations. Provisional signals carry `alert_timing='pivot_close'`; their `confirmed_at` field means P2 close | The existing original Telegram alert is a **candle-close provisional** divergence, not a pattern known only one bar later. Later bars must never decide whether that earlier alert existed. |
| Identity and deduplication | `signal_alert_identity` groups Telegram alerts by instrument/feed, timeframe, direction, P2 and provisional/confirmed stage; dashboard evidence retains individual P1/P2 pairs | One P2 can have several qualifying P1 pairs. Preserve all evidence but avoid generating several chase messages or sessions for one original alert. |
| Cadence | [`scan_schedule.py`](../scan_schedule.py) waits for the next source candle close; `scanner._scanner_worker` can sleep until other scheduled work. Seed `scanner.json` has `poll_seconds=14400`; saved Postgres config is authoritative after setup | Changing one poll value will not deliver a separate one-minute/five-minute monitoring service. Large full-universe cycles must not block due monitor work. |
| Supported bars | `scanner.TIMEFRAMES` and current provider adapters expose 1h, 4h, daily, weekly, monthly (adapter subsets vary); no current 30m/15m/5m/1m scanner path | Polling cadence is different from RSI candle timeframe. The supplied multi-frame CSV workflow needs a separate research importer before any live adapter work. |
| Volume | `scanner.Candle`, candle caches and both Python/JavaScript CSV parsers retain OHLC without volume | Preserve volume in a new offline research record; existing stored outcomes cannot establish the requested volume relationship. |
| Personal delivery | [`telegram_personal.py`](../telegram_personal.py), [`telegram_delivery.py`](../telegram_delivery.py), [`cloud/telegram-personal.sql`](../cloud/telegram-personal.sql) already provide per-user routes, suppression decisions, reservations and persisted message state | Extend those controls, with user/session-specific events and no shared-chat fallback. Telegram delivery remains at-least-once under ambiguous timeouts. |
| Existing Done | [`docs/telegram-personal-alerts.md`](telegram-personal-alerts.md), [`telegram_dismissal.py`](../telegram_dismissal.py): personal Done starts message deletion with a three-second Undo window | Message dismissal is separate from signal state. Its behavior for the new chats must be explicitly chosen. |
| Listener ownership | [`telegram_checklist.py`](../telegram_checklist.py) owns polling, persisted offsets and callback dispatch; personal callbacks bind bot/chat/message/user | Add callbacks to this listener, not another `getUpdates` consumer. Preserve ownership/effects checks and deletion deadlines. |
| Dashboard | [`web/app.js`](../web/app.js), `telegramAlertControls`: self-scoped preference checkboxes; `telegramRoutingControls`: installation-level original group routes | Current settings do **not** offer owner-managed per-user chat registration. Add an explicit private registration operation/UI, not the shared routing fields. |
| Existing research | [`backtest.py`](../backtest.py), [`csv_backtests.py`](../csv_backtests.py), [`finding_followup.py`](../finding_followup.py) and [`cloud/functions/backtests`](../cloud/functions/backtests) replay signals and price excursions | Useful detection, candles and report foundations; no existing per-user chase/click simulation. Python and hosted JavaScript need parity fixtures. |
| Existing excursion baseline | `finding_followup.calculate` starts after P2, anchored to `price2`; CSV outcomes start after `confirmed_at` but can still use `price2` as denominator | These are signal-relative research outcomes, not executable alert-entry drawdown. Keep them labeled separately from the new comparison. |

### Identity/configuration limits

Repository documentation records Ido's 13 personal categories as activated on 30 October
2026. The schema supports an installation owner and dashboard members, and additional
Telegram recipients. This checkout has no `data/` mapping/history files available to
inspect. It does not establish the second user's identity, current enrollment or live
preferences. The requirement for **two users** comes from the conversation, not a new
database audit. Do not infer the second person's name or copy the owner's destinations.

No credentials or personal chat/Auth IDs belong in this plan. At implementation setup,
an authorized administrator should inspect only the two membership/recipient records
needed, verify their Telegram identities privately, and record enrollment readiness.
The existing provisioning helper supports the owner and exactly 13 routes; the group
discovery helper expects the original 13 underscore-prefixed names. Neither supports
these four new chats as-is. Preserve existing routes and recipient `created_at` when
adding new keys; use per-route enrollment times for new-message backlog exclusion.

## 3. Decisions to settle in writing

These are proposals and alternatives, not additional accepted requirements.

| Decision | Suggested first version | Alternatives / question |
| --- | --- | --- |
| Monitoring candle timeframe | Primary research starts from 4h and compares proposed descent 4h→1h→30m→15m→5m→1m with strict, skip-capable and direct-lower-frame controls; chase polling remains every minute | User added the 15m timeframe. Which rungs are mandatory, may stages be skipped, and which candle timeframe should be used for opposite detection after entry? Five-minute polling does not settle that last choice. |
| Adverse-price gate | Restrict lower-frame chase advancement to adverse movement; test original-close signed distance as a stable baseline | “Against us” could instead mean relative to latest chase close or a fresh adverse move over a recent interval. Set buffer and pause/resume policy explicitly. Always evaluate the favorable stop even when the adverse gate is off. |
| Final 1m divergence | Notify and stop further chase searches; keep entry unconfirmed | Decide whether the final card remains eligible for I'm in and for how long. Suggested: allow a deliberate click without reopening chasing; do not silently infer entry or switch direction. |
| Child-event timing/window | Require a newly available child after the parent, with a proposed one-parent-duration search window | Decide whether P2 itself must close after parent availability, whether historical confluence counts separately, and timeout/fallback rules. Aligned windows offer two 15m bars after 30m, three 5m bars after 15m, or five 1m bars after 5m. |
| Alert stage | Match today's P2-close provisional alerts; label them clearly | Require structural confirmation instead, accepting one monitoring-bar delay. Decide independently for chase and opposite, and test each policy separately. |
| What counts as the next divergence? | Any new qualifying same-direction P2 after the original P2, using the existing rolling set of eligible P1 pivots | Require each new P2 to diverge against original P2, original P1, or the last chase P2; perhaps require a more adverse close than the last chase. These produce different events and must be separate backtest variants. |
| Entry before another divergence | Offer a start card in Chasing immediately after enrollment in the original alert, with Done / I'm in | Otherwise the user cannot click I'm in until a chase/price notification appears. Decide whether the original personal RSI alert should also carry I'm in, or whether the start card is wanted. |
| Done in Chasing | Reuse three-second dismissal/Undo **for that message only**, with monitoring unchanged | Keep a Completed/Undo placeholder instead. Stopping an entire chase needs a separately named **Stop chasing** control and explicit confirmation of that feature; Done must not silently acquire that meaning. |
| Done in Live positions | Same message-only behavior; preserve reported entry and opposite evidence | An acknowledgement flag is possible, but must not claim the position is closed or restart monitoring. |
| Close the deal now | Hide it until supported, or show a clearly labeled unavailable action explaining there is no connected execution account | A separately approved **Mark closed manually** action could record user-reported closure without an order. Do not silently interpret Close the deal as either a broker close or manual bookkeeping. |
| Eligible opposite divergence | Require its event-availability time to be after the accepted entry click; evaluate all new events since the last cursor at each five-minute run | Include an opposite event already known before the click? Require P2 itself to form after entry? With confirmed detection, P2 may precede the click but confirmation may follow. Choose the intended rule explicitly. |
| New overlapping originals | Preserve separate root sessions, showing original timeframe/date and session ID on every card | One active root per user/feed/timeframe/direction, or explicit replacement. Never automatically overwrite an entered session. Define whether one divergence can notify several roots or is consolidated. |
| Other expiry/cancellation | Add an explicit finite chase horizon and a separate manual Stop control after choosing their values | Wall time vs trading time vs original bars; opposite-monitor timeout; structural breach; asset removal. These additional automatic stop rules remain undecided; the configurable favorable stop is agreed. |
| Favorable-move stop setting | Personal dashboard numeric setting, default 0.5%, using the immutable original signal close | User-editable without code changes. Proposed edit scope is new chases only, with existing thresholds shown explicitly; applying to running chases needs a separate deliberate action. |
| Favorable-move stop observation | Stop and notify at the saved per-session percentage | Still choose fresh trade/quote versus closed-minute price, whether a wick counts, and gap/restart handling. A lack of another divergence must not prevent evaluating this rule. |
| Preferences during monitoring | Two independent message checkboxes: Chasing and Live positions. Suppress messages without implying entry/closure; show monitoring state separately | Should opting out also pause computation? Decide resumption/backlog policy. Warn before disabling Live positions notifications for an entered session and make the consequence visible. |
| New-category defaults | Register disabled until each user's destinations and choices are verified | Existing categories default on; do not use that implicit default to send new categories to unverified chats. Decide whether existing RSI category opt-out also prevents new chase enrollment. |

Priority for the next discussion: adverse-gate reference, required/skippable rungs,
child timing/windows and final-card entry eligibility, plus the outstanding Done semantics
and opposite timeframe. Research success/exit definitions must be fixed before reporting
profitability. The companion design proposes bounded experiments rather than assumes answers.

## 4. Proposed lifecycle and durable records

Keep immutable original findings and later divergence evidence separate from each
user's monitoring and message state. Suggested lifecycle:

| State | Event | Result / next due |
| --- | --- | --- |
| `chasing` | Eligible intermediate same-direction divergence while adverse gate qualifies | Create recipient-specific chase event, advance the chosen lower-frame rung; stay `chasing`, next due +60s |
| `chasing` | Eligible same-direction 1m divergence | Freeze chain evidence, enqueue final Chasing alert, move to `chase_search_complete_1m`; clear chase/price deadlines, entry still unconfirmed |
| `chasing` | Eligible original-close price notification | Create rate-limited outbox event; stay `chasing`, next due +60s |
| `chasing` | Price reaches saved favorable-move percentage beyond original close | Atomically move to `chase_stopped_favorable`, freeze trigger evidence, enqueue one logical stop alert to this user's Chasing chat, clear chase/price deadlines |
| `chasing` | Owner's valid I'm in callback | Atomically record reported entry, disable further chase/price events, move to `monitoring_opposite`, next due +300s |
| `monitoring_opposite` | No eligible opposite event | Advance evidence cursor, stay in state, next due +300s |
| `monitoring_opposite` | First eligible opposite event | Atomically freeze selected evidence and enqueue Live positions event; move to `opposite_found`; clear periodic search deadline |
| `opposite_found` | Delivery failure/retry | Retry the same logical alert, never restart periodic search or select a different opposite event |
| `chase_stopped_favorable` | Delivery failure/retry | Retry the same stop alert; do not resume chasing or start opposite monitoring |
| `chase_search_complete_1m` | Delivery failure/retry | Retry the same final alert; no resumed chase or automatic opposite monitoring. Later I'm in eligibility is a separate decision |
| Any | Done / Undo | Apply only the agreed message action; no monitoring transition |

Potential `expired`, `stopped_by_user` and `closed_reported` states require the decisions
above. `chase_stopped_favorable` and `chase_search_complete_1m` end chase search; neither
confirms entry. `opposite_found` is not proof of a closed trade. No automatic direction switching
on prices, message delivery, another user's action or a hypothetical backtest fill.

Suggested new private Postgres records (names provisional):

- **Sessions:** installation, dashboard user, root alert identity, immutable origin
  finding IDs, exact feed/venue/symbol, source and monitor timeframes, direction,
  rule/policy versions, P2 close/time, alert-created and accepted-send times if known,
  status/revision, next due, last processed evidence cursor and enrollment time.
  Retain current rung, parent/child event chain, adverse-gate reference, pause state,
  stage/root deadline and a separate entry-eligibility flag for terminal search cards.
- **Entry:** accepted callback receipt time and Telegram update ID, source message and
  chase evidence, `entry_source='user_click'`, optional user-entered fill price, observed
  market price/time/source/staleness. A click confirms reported entry, **not broker fill
  price, size or execution**. Never substitute the chase candle's close as a real fill.
- **Events/outbox:** root/session, user, kind, evidence IDs, logical sequence, preference
  decision, session revision, destination snapshot and delivery status/message IDs.
  Unique divergence event key includes session + kind + P2 + stage (with P1 evidence
  grouped). Price events use durable crossing sequence plus a rate reservation.
- **Price trigger state:** last fresh observation/time, adverse excursion/rearm state,
  immutable original-close anchor, last notification reservation time and pending event.
  For the favorable stop, persist the session's saved percentage and settings revision
  (`0.5` percent corresponds to fractional threshold `0.005`), trigger observation/source/time,
  actual signed move, stopped-at time and the unique terminal notification event.
- **Callback audit:** user, message binding, action/update ID, transition revision and
  result. Keep rejected/duplicate outcomes sufficient for diagnosis without secrets.

Use self-scoped authenticated dashboard APIs for personal state/preferences, with
installation membership checks. Only an authorized owner/admin can register verified
destinations for another user; reject arbitrary caller-supplied user IDs elsewhere.
Callbacks must validate bot, chat, message, enrolled Telegram user, session and revision.

Serialize I'm in, 1m completion, the favorable stop and due monitor jobs with row locks/compare-and-set
revisions. Only one transition can win while the session is `chasing`; if entry wins,
do not apply the chase stop to the entered session. If the stop wins, do not let a stale
button silently restart monitoring. Decide separately how a user can report entry after
the chase has stopped. For 1m completion, decide whether a final card stays entry-eligible
without an active search. Duplicate callbacks must return the already accepted result,
not reset the entry time or deadline. Callbacks on old cards can enter only an explicitly
entry-eligible root session. Disable stale
I'm in buttons on sibling cards where possible; server-side checks remain authoritative.
Decide the UX if Done deletion wins a race with I'm in; preserve a dashboard entry control
so deleting a card cannot make a still-active chase impossible to enter.

Commit state transitions and outbox insertion together. Before sending, recheck session
revision and opt-out policy so queued chasing cards are suppressed after entry; an
already in-flight Telegram request can still complete. Suppress queued non-terminal
chase/return cards after the favorable stop too; its own terminal alert remains eligible
for delivery despite the session being stopped. Apply the same revision policy on 1m
completion while preserving its final alert. Check preferences again for
retries and retain suppression decisions so re-enabling does not replay old notifications.
Bind message IDs to sessions after sends and reconcile ambiguous sends using the
existing reservation model. Promise one **logical** alert, not exactly-once Telegram.

Integrate due monitors into the existing owned installation service. Share each market
data request across users/sessions on the same feed/timeframe; do not create one scanner
per user or lower every full-universe scan to one minute. Use a bounded scheduler/data
queue within the existing worker ownership and connection limits, separate from slow
universe/report work. Record lag and unavailable data, retain sessions when a rotating
asset leaves the universe, and back off failures without interpreting them as no signal.
Resume persisted deadlines/cursors on restart; apply a chosen catch-up/expiry policy
instead of flooding a chat with historical events. Retain UTC event times, with Israel
time only for display; five-minute cadence must not depend on DST wall-clock arithmetic.

## 5. Original-close notifications without another divergence

Freeze `P0 = original P2 close (price2)` for the life of the root. A chase does not reset
this anchor. Distinguish this from the confirmation-bar close and any later entry price.
For direction `d=+1` bullish or `d=-1` bearish, define signed distance
`x = d * (observed_price / P0 - 1)`. Positive is favorable; negative is adverse.

### User-configurable favorable stop (default 0.5%)

Add **Settings → Chasing → Stop chasing after favorable move (%)**. Show **0.5** as
the default, an explicit percent unit, a Save control and helper text explaining the
original signal-close anchor and bullish/up versus bearish/down behavior. The same
positive magnitude applies to both directions; the user does not enter a negative number
for bearish signals. Use the configured percentage in alerts and session details too.

Store this as a numeric personal preference scoped to installation and authenticated
user, separate from the existing boolean Telegram opt-out categories. Reuse the
self-scoped access/revision pattern, so each user can change their own threshold without
changing the other user's sessions or requiring installation-editing privileges.
Save it durably in Postgres and load it without source edits, environment changes or
a service restart. Validate on client and server: reject empty/non-numeric/non-finite,
zero/negative values and percentages that imply a non-positive bearish target; document
accepted decimal precision. Saving this field must not change notification opt-outs.

**Proposed change scope:** apply saved edits to **new chases**, copying the percentage
and settings revision into each session. Show “Applies to new chases; running chases keep
their saved threshold” beside Save and display the active threshold on each running
session. Saving alone sends no alert and never revives a stopped chase. If editing
already-running chases is wanted, provide a separate explicit action that identifies the
affected sessions and immediately eligible stops; its UX is still a written decision.
This avoids silently stopping an existing chase when its user lowers the default.

Backtest runs must take an explicit stop-percentage parameter (default 0.5), store it in
the immutable run configuration/report and never read mutable live settings during
replay. Alternative percentages are recorded strategy variants with the same holdout
controls. Path-success targets and hypothetical trade exits remain separate parameters.

At the first eligible fresh observation with `x >= p/100` while still `chasing`, stop the
chase and enqueue one logical stop alert, where `p` is the saved per-session percentage.
The rule does not require prior drawdown or another divergence. Default example with
`p=0.5` and original close 100: bullish stops at 100.50 or above;
bearish stops at 99.50 or below. A jump beyond the threshold also qualifies; the alert
must show the actual observed move rather than fabricate a fill or exact threshold touch.
Use consistent decimal/tick precision so an exact threshold touch is not lost to binary
floating-point rounding; document treatment when the threshold lies between tradable ticks.

Suggested text: **“Price is now {configured percentage}% or more above/below the original signal candle
close in the bullish/bearish divergence direction. Chasing stopped.”** Include original
close, threshold, observed price, signed move, observation time and source. Send it to
that user's Chasing destination, subject to their personal message preference. A muted
notification does not prevent the stop; retain the terminal reason in the dashboard.

Stop immediately when the monitor observes the trigger, even if Telegram delivery is
delayed. Stop alerts are one-shot terminal events, distinct from repeating return-to-close
messages. Decide whether the once-per-minute chat cap also covers this terminal event;
suggest prioritizing it over pending return cards and, if capped, retaining one pending
terminal alert without delaying the state transition. Do not suppress it just because
a recent return-to-close message used a notification slot.

The threshold comes from personal Settings, with default 0.5%; remaining observation choices are price source, freshness,
intraminute touch versus closed-minute evidence, and what happens if the threshold was
crossed during an outage but price has already returned. Historical OHLC alone cannot
resolve whether a click or threshold touch occurred first inside one minute. Record
that ambiguity and apply a declared policy rather than invent event ordering. If the
threshold and a new divergence are discovered in the same still-chasing check, propose
one terminal stop alert and suppress its competing non-terminal chase card.

### Non-terminal close-return notification alternatives

Concrete policies to discuss:

1. **Recovery only:** arm after `x <= -A`; notify when a later fresh observation reaches
   `x >= -E`. Rearm only after another adverse excursion beyond `-A`. `A` is meaningful
   drawdown; `E` is a near-close tolerance. This misses immediate favorable moves.
2. **Immediate move plus recovery (suggested experiment):** separately notify once on
   the first post-alert favorable crossing `x >= F`, without requiring drawdown; also
   apply policy 1. If a separate non-terminal immediate-move message is wanted, choose
   `F < p/100`; at/above the saved favorable threshold the required stop rule takes precedence.
3. **Every eligible recross:** notify whenever price crosses the selected return boundary,
   with hysteresis and cooldown. More responsive, potentially more noise; do not notify
   every minute merely because price remains on one side.

Choose `A`, `E` and `F` in ticks, basis points or a fixed-at-origin volatility measure;
zero is an experimental baseline, not an assumed production threshold. Choose last trade,
bid/ask/mid quote, or closed 1m close. Exact floating-point equality is inappropriate.
Specify whether touching by wick counts: minute OHLC only proves a touch within a minute,
not its order relative to drawdown in that same candle. A closed-minute policy is easier
to reproduce but can miss a quick intraminute return. Stale quotes must not trigger.

The cap must be a durable **rolling 60 seconds**, not just distinct wall-clock minute
buckets, which could allow messages one second apart. Decide whether the cap applies
per root or across the user's entire Chasing chat (suggestion: across this user's price
notifications, consolidating roots in one card when needed). It is separate from the
existing Telegram **sound** slot, which silences additional messages but does not stop
them. Shared divergence-alert limits, if wanted, are an additional decision.

Reserve eligible emissions transactionally to prevent concurrent fluctuations from
duplicating events; keep a suppressed crossing pending for coalescing or discard it
according to an explicit policy. Retry only the original logical event while still
relevant; do not accumulate historical crossing notifications during an outage. Ambiguous
network retries can still duplicate a visible message under existing Telegram semantics.
These close-return events do not stop chasing or mark entry. The separate configurable favorable
stop above terminates chasing and still does not mark entry.

## 6. Four-chat manual setup, after implementation is approved

| Recipient | New destination | Required private mapping |
| --- | --- | --- |
| Existing user 1 | Chasing | installation + verified Auth user + Telegram user + chat |
| Existing user 1 | Live positions | same recipient, distinct chat |
| Existing user 2 | Chasing | second verified recipient, never the owner's fallback |
| Existing user 2 | Live positions | same second recipient, distinct chat |

1. Privately verify the two existing dashboard memberships and their Telegram account
   ownership. Confirm the bot and each person's current recipient registration. Do not
   share IDs or credentials in the plan, commits or browser bundles.
2. Each recipient manually creates their two groups with only themselves and the bot.
   Use clear names such as `<personal label> · Chasing` / `Live positions`; keep all four
   chat IDs distinct and avoid shared/original routes. Check bot permissions for the
   agreed buttons/message dismissal.
3. Capture and validate the four chat mappings using a new controlled onboarding flow.
   Reuse membership checks from the existing helper, but extend it for two route names
   and both recipients. Coordinate with the active listener: never launch a competing
   poller, clear its cursor or remove a webhook blindly.
4. In the proposed owner-only dashboard registration view, select the verified user and
   register `chasing` / `live_positions` chat IDs with per-route enrollment times and a
   revision check. Validate identity/membership before enabling. Preserve the existing
   13 routes, preferences and registration age. This UI/API does not exist today.
5. Add **Receive chasing notifications** and **Receive live-position notifications** to
   Settings → My Telegram alerts for each user. Own settings remain editable by viewers
   without gaining mapping-management rights. Show unregistered destinations as unavailable.
6. Verify access isolation and callbacks in disposable fixtures first. No onboarding demo
   or test alert is implied; any later real-message test must be explicitly authorized.

## 7. Backtest design: original alerts versus chase alerts

### Questions and event clocks

Primary question: does waiting for a defined chase entry reduce adverse excursion after
an actionable alert, and what opportunity/delay cost does it introduce? Do not present
lower drawdown among successful chases as proof the whole strategy is better.

The expanded study also asks whether a defined hypothetical trading policy is profitable,
when success rates are higher, whether signal-candle **relative volume** adds predictive
value, and whether lower-frame divergences are more frequent/useful after a parent
divergence than at matched adverse-price times. The [companion research design](rsi-chasing-research-design.md)
defines CSV intake, bounded ladder/control policies, volume features, conditional-rate
denominators, success/exit proposals, costs, holdout validation and uncovered scenarios.

Record separately: P2 close, structural confirmation when applicable, provider-available
time, monitor-check time, alert-enqueue time, Telegram accepted-send time if historically
known, hypothetical fill time, and real user-click time if later collected. Call the
provisional P2-close timestamp `event_at` in research even though the current payload
calls it `confirmed_at`. Use explicit assumed latencies when live delivery evidence is
missing; never claim a reconstructed theoretical alert was actually sent.

### Comparison policies and price anchors

- **Original entry O:** next executable lower-timeframe bar open strictly after the
  simulated original alert becomes available, including chosen polling/provider/delivery
  delay. Adjust fill price for spread/slippage and account for fees separately in any
  net returns. Never enter at the original P2 close using knowledge or prices only
  available after that close.
- **Chase entry C1:** next executable bar open after the **first** eligible same-direction
  chase alert under the selected policy, with identical delay/fill conventions. Later
  chase entries C2/C3 may be reported as predefined separate variants. Do not choose the
  lowest bullish/highest bearish future chase or the best-performing chase retrospectively.
  Replay the run's configured favorable stop before admitting new chase entries: roots stopped before
  an eligible entry remain in the all-original cohort as stopped/no-entry, not silently
  excluded successes. Declare ordering for a pending simulated fill versus the stop;
  once simulated entry is accepted, the chase stop no longer applies to that simulated
  session. Real I'm in actions remain separate from these simulated events.
- **Signal-price diagnostics:** also calculate excursions from original/chase P2 closes,
  clearly named as reference-price evidence. Do not mix these with O/C fill outcomes.
- **Price-trigger entry R:** optional separate variant entering after the agreed
  recovery/immediate-move notification. It is not a divergence chase entry.
- **Human entries:** later real I'm in clicks are observational `user_click` records,
  separate from deterministic `simulated_entry` events. Neither dataset proves broker
  execution without a reported/verified fill. Do not manufacture historical human clicks.

Use a fine execution/price series with the same exact instrument, venue and adjustment
basis as the signal series. Missing bars or closed markets delay the fill to the next
eligible executable bar; do not interpolate. Same-bar stop/target ordering cannot be
resolved from OHLC; choose conservative ordering or mark ambiguous, never infer a win.

### Explicit windows and metrics

Freeze an experimental configuration before each run. Suggested initial horizons,
expressed in the **original signal timeframe's completed bars**, are `H={1,3,5,10}`;
start with maximum chase waiting budget `W=5` original bars as a proposed research value.
These are research candidates for discussion, not live expiry requirements. Calendar
boundaries/session alignment must be fixed in the dataset manifest. The waiting cutoff
`T_W` is the Wth original-bar close strictly after original alert availability.

Report both:

1. **Equal exposure:** for each O/C/R entry, measure from its fill to the close of the Hth
   original-timeframe bar whose close is strictly after that fill. Both strategies receive
   the same number of close boundaries; also report elapsed market/wall time because
   entry may occur partway through the first bar.
2. **Common origin window:** use a fixed end `T_H`, the Hth original-bar close strictly
   after original alert availability. Measure O and C through that same end; C is
   eligible only if filled before `T_H` and before `T_W`. Report absent/late chases explicitly.
   Do not silently omit roots for which a chase occurs after the evaluation window.

Also report the requested **after-alert** comparison directly: for original and chase
alerts, anchor to the first fresh observed price at/after their respective simulated
delivery times and measure adverse/favorable excursion forward from that observation
under both windows. Keep this separate from next-bar executable fill MAE and from P2
reference diagnostics. If fine price observations are unavailable, label this metric
unavailable rather than using a pre-alert bar's entire high/low. Report alert-to-fill
movement and delay so a claimed improvement can be traced to its actual timing.

For fill `E>0`, direction `d` and the price path between fill and the selected end:

- **MAE (entry adverse excursion):** bullish `100*max(0,(E-min(low))/E)`; bearish
  `100*max(0,(max(high)-E)/E)`. Use only prices occurring after fill. This is not the same
  as peak-to-trough portfolio drawdown.
- **MFE:** bullish `100*max(0,(max(high)-E)/E)`; bearish
  `100*max(0,(E-min(low))/E)`.
- **Paired benefit:** `MAE_O - MAE_C`, in percentage points, on identical eligible roots;
  show median, mean, p75/p90/p95, sample count and share with a reduction. Break out
  bullish/bearish, feed/asset, timeframe and chase ordinal.
- **Return/opportunity:** signed terminal price return, entry-price improvement
  `100*d*(E_O-E_C)/E_O`, entry delay, original-to-chase MAE, favorable movement missed
  before chase, and no-chase/late-chase rate. Price return is a diagnostic, not a full
  strategy P&L without a defined exit/capital policy.
- **Recovery:** time to cross a specified reference (P0 or fill E, reported separately)
  after an adverse excursion; report unresolved/censored observations. Quantify fraction
  and time to MAE thresholds (proposed sensitivity grid 0.5%, 1%, 2%).
- **Operational behavior:** chase alerts per root/user/day, immediate/recovery price
  notification counts, coalescing/cap suppression, stale/missing-data minutes, entry
  switches, time to eligible opposite divergence and opposite-alert delivery lag.
  Include favorable-stop count/rate, saved stop percentage, time to threshold, stop-alert lag, entries prevented and
  subsequent price paths over the fixed research windows. Stopping monitoring does not
  erase research follow-up or establish that skipping an entry was beneficial.

Publish two cohorts: all original roots, including those with no chase, and paired roots
with eligible chase fills. In an optional all-root wait-for-chase policy, no chase means
no trade: include that cash/no-entry result when comparing opportunity, rather than
pretending it is a zero-MAE completed trade. For uncertain tail observations report
coverage/censoring, not success. Group confidence intervals by root/asset-time block;
multiple P1 pairs, overlapping roots and timeframes are correlated observations.

Research now includes both excursion analysis and **fully specified hypothetical
profitability comparisons**. Proposed exits include fixed-horizon marking/closing and
first eligible opposite alert on a declared candle timeframe with a fixed terminal
timeout. Choose costs, sizing, concurrent-exposure limits, short availability and exits
before reporting net win rate, expectancy, profit factor or portfolio drawdown. These
are simulation conventions; they do not authorize live entry decisions or trade closing.

### Preventing lookahead and preserving reproducibility

Replay chronologically, exposing only candles whose close **and availability** are no
later than each simulated check. Provisional detection must use the prefix at P2 close;
structural confirmation requires the right-hand candle. A provisional signal that later
fails confirmation remains in the original-alert cohort. Persist every root/chase event
as it becomes known; do not reconstruct them solely from the final chart's confirmed
pivots. Replay the five-minute opposite-monitor schedule only after simulated entry.

Use deterministic ordering for same-timestamp events: market data availability, eligible
monitor check, queued alert availability, next eligible fill/callback, state transition.
Document strict/inclusive boundary rules and verify callbacks cannot consume an event
that was not available yet. For historical revised data, disclose that original as-seen
provider versions may be unavailable; test delay/revision sensitivity rather than claim
perfect reconstruction.

Create a read-only dataset manifest with source/version/hash, download date, exact
symbols/venues, quote currency, timezones/session calendars, native/resampled boundaries,
adjustments/roll conventions, range, warm-up, gaps, quality exclusions and data-license
constraints. Pin source SHA, detector/policy hashes, horizons, latencies, seeds, costs,
fill rules and universe membership. Retain raw snapshots plus normalized candles and
event/fill/result tables so rerunning produces the same report hash.

No cost figures or history availability have been verified here. A later data audit must
establish minute-history coverage and entitlements per provider/instrument. Current
coarse OHLC can test coarse divergence variants; it cannot establish minute crossing
order or realistic minute execution. Current CSV upload accepts only 1h/4h/daily/weekly/
monthly and caps files at 20,000 rows/2 MB; use a separate offline research importer for
larger minute datasets rather than pretending the UI already supports them.

Use actual bid/ask where available; otherwise freeze documented instrument-specific
spread/slippage/commission assumptions and run zero-cost diagnostic, base and stressed
cost scenarios. Include funding/borrow/roll where relevant before claiming net P&L.
Validate splits across all OHLC fields, exact cash index versus futures/CFD identities,
delayed feeds and exchange closures. Use dated membership for rotating universes; a
current constituent list is survivorship-biased. Never silently replace an unavailable
instrument/provider. Start with one verified instrument/feed and period, then expand.

Choose policies/thresholds on a development period; freeze them before chronological
validation/holdout or walk-forward evaluation. Report every tried variant and counts.
Do not select thresholds based on the final test period's drawdown improvement.

## 8. Proposed delivery stages and acceptance evidence

| Stage | Concrete output | Completion condition |
| --- | --- | --- |
| 0 — Written decisions | Resolve the priority questions and record policy version | User agrees what is being monitored and what the buttons mean; no live changes |
| 1 — Data feasibility | Audit supplied 4h/1h/30m/15m/5m/1m TradingView CSVs, preserve volume, synchronize coverage | Exact feed/session identities, volume type and common history verified; missing data explicitly excluded |
| 2 — Offline research | Original/chase MAE, transition probabilities/lift, volume associations, defined success and hypothetical net profitability reports | Frozen policies/exits/costs, all-root cohorts, prefix/lookahead tests, holdout evidence and reproducible hashes; no Telegram/production dependency |
| 3 — Private persistence and settings | Additive schema/API, personal registration, two message preferences, user-editable favorable stop percentage, monitor status/history | Disposable Postgres access/isolation and upgrade tests pass; no production migration |
| 4 — Worker/callback implementation | Shared due scheduler, callbacks, price policy, outbox and restart recovery | Fake clock/providers/Telegram demonstrate lifecycle and races; external effects disabled |
| 5 — Manual onboarding and later activation | Four verified chats, saved mappings/preferences, reviewed source release | Separate authorized setup; existing production service only; safe verification without alerts/orders |

Suggested implementation seams: new `divergence_chasing.py` for the pure lifecycle and
monitor decisions; additive `cloud/divergence-chasing.sql`; a separate offline research
entry point for dataset replay. Extend `platform_app.py` at the original-alert lifecycle,
`telegram_personal.py`/`telegram_checklist.py` for owned callbacks, and the existing
dashboard self-service/owner registration APIs. Keep research outcome calculations
separate from monitoring and broker modules. These filenames are proposals, not code
introduced by this planning change.

Required later acceptance cases:

- Bullish → bullish and bearish → bearish until that user's I'm in; opposite direction
  only afterward; the second user's state is unchanged. Exercise immediate entry and
  repeated/late/unauthorized callbacks, including Done/entry races and stale cards.
- A scheduled check at +60s/+300s, long report work, restart, data lag and market closure;
  no open-candle leakage, no missed eligible events between five-minute checks.
- Immediate favorable move, drawdown/return without divergence, exact boundary, gap
  across anchor, same-candle ambiguity, hysteresis/rearm, durable rolling-60s cap across
  a minute boundary, and chasing continuing after non-terminal return notifications.
- Bullish +0.5% / bearish -0.5% from original close, inclusive boundary and just short,
  immediate move and drawdown recovery, gap beyond threshold, stale prices, muted user,
  duplicate checks, restart/retry and simultaneous divergence or I'm in. Require one
  logical stop notification, no later chase work and no automatic opposite monitoring;
  verify entered sessions are unaffected and the anchor never resets to a chase close.
- Personal favorable-stop Settings: default 0.5%, a second saved value in both directions,
  percent/fraction conversion, invalid numeric inputs, cross-user isolation, revision
  conflicts, persistence/restart, active-session snapshots and new-session use without
  restarting. Verify no alert from Save alone, no revival of stopped sessions and backtest
  reproducibility even after the live setting changes.
- Multiple P1 pairs for one P2, overlapping roots/timeframes, feed change, asset removal,
  opted-out/unregistered users, re-enable without replay, and mapping/preference revisions.
- Sequential lower-frame availability, adverse-gate pause/resume, missing/intermediate
  rungs, fixed stage timeouts and final 1m notification/search completion without entry.
  Confirm the final card's chosen entry policy and no automatic restart after more drawdown.
- Volume retention, no-volume versus zero-volume, original/child/confirmation timing,
  cross-timeframe reconciliation, matched conditional-rate denominators, costs, small
  volume-bin samples and chronological holdout boundaries. No leaked final 4h volume at
  an earlier child event, and no real click inferred from any simulated fill.
- First opposite event atomically stops searching even if delivery fails; retry preserves
  evidence. No shared fallback, cross-user state access, duplicate transition or order call.
- Python/JavaScript prefix parity, provisional failure remaining in cohort, fill/MAE timing,
  tail censoring and no-chase denominators. A deterministic fixture must reproduce the
  same output after restart/replay. Appending future candles must not change earlier
  event decisions or already complete fixed-window outcomes; censored outcomes may mature.

Use pure tests for lifecycle/replay and mocked external APIs; integration tests require
the repository's explicit loopback disposable `trading_bot_test` database guard. Relevant
existing suites include `test_rsi_tolerance`, `test_scanner`, `test_telegram_personal`,
`test_telegram_dismissal`, `test_telegram_routing`, `test_telegram_delivery`,
`test_finding_followup`, `test_csv_backtests`, `test_logs_backtest`,
`web/telegram-alerts.test.mjs`, `cloud/test_backtest_engine.mjs` and
`cloud/test_followups.mjs`. Extend focused tests for new behaviors; do not call production
services to satisfy a test.

## 9. Production and release boundaries

Follow [`AGENTS.md`](../AGENTS.md) and [`POSTGRES_MIGRATION.md`](../POSTGRES_MIGRATION.md).
Never start another production scanner, use production DB for tests, reset delivery
ledgers to force messages, or send test alerts/orders during deployment checks. Do not
enable legacy executable publication in `.github/workflows/standalone-release.yml`.
The repo contains paper-trading scaffolding; this feature must remain isolated from it.

No push is needed for this plan. If a future change is pushed, record the exact SHA and
branch; discover all Actions and external deployment checks for that SHA, allow checks
to register, await terminal outcomes and repair failures. For an actual publication,
verify destination health/version and affected behavior safely, including saved status
and access isolation, without generating alerts/orders. A green build or skipped legacy
publish is not deployment proof. Standalone artifacts require Linux and Windows builds
and frozen-app smoke tests. Report links, verified SHA, outcome and any unavailable
verification explicitly. Roll out additive schema and feature-disabled code first,
preserve existing sessions/delivery history, then enable only reviewed recipients.

Planning validation: repository references, requirements and diff reviewed; runtime tests
are not applicable to this documentation-only change. Production configuration, current
second-user enrollment, minute-history access and any performance benefit remain unverified.
