# RSI divergence chasing and backtest plan

Status: discussion draft; no runtime implementation or activation authorized by this document.
Prepared 31 October 2026 on `codex/rsi-chasing-backtest-plan`, from source commit
`908f3147da812cbe6949c2909246e3e0ad14d614`.

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
and must **not stop chasing**. Only **I'm in** changes monitoring direction. Exact trigger,
thresholds, expiry and overlap behavior remain decisions below.

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
| Supported bars | `scanner.TIMEFRAMES` and current provider adapters expose 1h, 4h, daily, weekly, monthly (adapter subsets vary); no current 1m/5m scanner path | Polling every minute is different from calculating RSI on 1m bars. Minute prices/bars and their history require an explicit adapter/data plan. |
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
| Monitoring candle timeframe | Keep the original signal timeframe, with due checks every 60/300 seconds; separately sample minute prices for close-return notifications | If the intent is faster **new divergences**, explicitly choose 1m or 5m RSI bars, or a smaller timeframe mapped from the original. A weekly chart cannot generate new closed weekly patterns each minute. What timeframe is wanted for chase and opposite detection? |
| Alert stage | Match today's P2-close provisional alerts; label them clearly | Require structural confirmation instead, accepting one monitoring-bar delay. Decide independently for chase and opposite, and test each policy separately. |
| What counts as the next divergence? | Any new qualifying same-direction P2 after the original P2, using the existing rolling set of eligible P1 pivots | Require each new P2 to diverge against original P2, original P1, or the last chase P2; perhaps require a more adverse close than the last chase. These produce different events and must be separate backtest variants. |
| Entry before another divergence | Offer a start card in Chasing immediately after enrollment in the original alert, with Done / I'm in | Otherwise the user cannot click I'm in until a chase/price notification appears. Decide whether the original personal RSI alert should also carry I'm in, or whether the start card is wanted. |
| Done in Chasing | Reuse three-second dismissal/Undo **for that message only**, with monitoring unchanged | Keep a Completed/Undo placeholder instead. Stopping an entire chase needs a separately named **Stop chasing** control and explicit confirmation of that feature; Done must not silently acquire that meaning. |
| Done in Live positions | Same message-only behavior; preserve reported entry and opposite evidence | An acknowledgement flag is possible, but must not claim the position is closed or restart monitoring. |
| Close the deal now | Hide it until supported, or show a clearly labeled unavailable action explaining there is no connected execution account | A separately approved **Mark closed manually** action could record user-reported closure without an order. Do not silently interpret Close the deal as either a broker close or manual bookkeeping. |
| Eligible opposite divergence | Require its event-availability time to be after the accepted entry click; evaluate all new events since the last cursor at each five-minute run | Include an opposite event already known before the click? Require P2 itself to form after entry? With confirmed detection, P2 may precede the click but confirmation may follow. Choose the intended rule explicitly. |
| New overlapping originals | Preserve separate root sessions, showing original timeframe/date and session ID on every card | One active root per user/feed/timeframe/direction, or explicit replacement. Never automatically overwrite an entered session. Define whether one divergence can notify several roots or is consolidated. |
| Expiry/cancellation | Add an explicit finite chase horizon and a separate manual Stop control after choosing their values | Wall time vs trading time vs original bars; opposite-monitor timeout; structural breach; asset removal. None are agreed automatic stop rules. |
| Preferences during monitoring | Two independent message checkboxes: Chasing and Live positions. Suppress messages without implying entry/closure; show monitoring state separately | Should opting out also pause computation? Decide resumption/backlog policy. Warn before disabling Live positions notifications for an entered session and make the consequence visible. |
| New-category defaults | Register disabled until each user's destinations and choices are verified | Existing categories default on; do not use that implicit default to send new categories to unverified chats. Decide whether existing RSI category opt-out also prevents new chase enrollment. |

Priority for the first discussion: timeframe/stage, Done semantics, entry access before a
second divergence, and price-trigger policy. These determine what the backtest must replay.

## 4. Proposed lifecycle and durable records

Keep immutable original findings and later divergence evidence separate from each
user's monitoring and message state. Suggested lifecycle:

| State | Event | Result / next due |
| --- | --- | --- |
| `chasing` | New same-direction divergence | Create recipient-specific chase outbox event; stay `chasing`, next due +60s |
| `chasing` | Eligible original-close price notification | Create rate-limited outbox event; stay `chasing`, next due +60s |
| `chasing` | Owner's valid I'm in callback | Atomically record reported entry, disable further chase/price events, move to `monitoring_opposite`, next due +300s |
| `monitoring_opposite` | No eligible opposite event | Advance evidence cursor, stay in state, next due +300s |
| `monitoring_opposite` | First eligible opposite event | Atomically freeze selected evidence and enqueue Live positions event; move to `opposite_found`; clear periodic search deadline |
| `opposite_found` | Delivery failure/retry | Retry the same logical alert, never restart periodic search or select a different opposite event |
| Any | Done / Undo | Apply only the agreed message action; no monitoring transition |

Potential `expired`, `stopped_by_user` and `closed_reported` states require the decisions
above. `opposite_found` is not proof of a closed trade. No automatic direction switching
on prices, message delivery, another user's action or a hypothetical backtest fill.

Suggested new private Postgres records (names provisional):

- **Sessions:** installation, dashboard user, root alert identity, immutable origin
  finding IDs, exact feed/venue/symbol, source and monitor timeframes, direction,
  rule/policy versions, P2 close/time, alert-created and accepted-send times if known,
  status/revision, next due, last processed evidence cursor and enrollment time.
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
- **Callback audit:** user, message binding, action/update ID, transition revision and
  result. Keep rejected/duplicate outcomes sufficient for diagnosis without secrets.

Use self-scoped authenticated dashboard APIs for personal state/preferences, with
installation membership checks. Only an authorized owner/admin can register verified
destinations for another user; reject arbitrary caller-supplied user IDs elsewhere.
Callbacks must validate bot, chat, message, enrolled Telegram user, session and revision.

Serialize I'm in and due monitor jobs with row locks/compare-and-set revisions. Duplicate
callbacks must return the already accepted result, not reset the entry time or deadline.
Callbacks on old cards can enter only their still-active root session. Disable stale
I'm in buttons on sibling cards where possible; server-side checks remain authoritative.
Decide the UX if Done deletion wins a race with I'm in; preserve a dashboard entry control
so deleting a card cannot make a still-active chase impossible to enter.

Commit state transitions and outbox insertion together. Before sending, recheck session
revision and opt-out policy so queued chasing cards are suppressed after entry; an
already in-flight Telegram request can still complete. Check preferences again for
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

Concrete policies to discuss:

1. **Recovery only:** arm after `x <= -A`; notify when a later fresh observation reaches
   `x >= -E`. Rearm only after another adverse excursion beyond `-A`. `A` is meaningful
   drawdown; `E` is a near-close tolerance. This misses immediate favorable moves.
2. **Immediate move plus recovery (suggested experiment):** separately notify once on
   the first post-alert favorable crossing `x >= F`, without requiring drawdown; also
   apply policy 1. This covers both voiced cases, with distinct message labels.
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
None of these price events stops chasing or marks entry.

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

Publish two cohorts: all original roots, including those with no chase, and paired roots
with eligible chase fills. In an optional all-root wait-for-chase policy, no chase means
no trade: include that cash/no-entry result when comparing opportunity, rather than
pretending it is a zero-MAE completed trade. For uncertain tail observations report
coverage/censoring, not success. Group confidence intervals by root/asset-time block;
multiple P1 pairs, overlapping roots and timeframes are correlated observations.

Initial research is excursion analysis. An optional later trading simulation can exit
at the first opposite alert's next executable price, using the five-minute event clock
and a fixed terminal timeout. Choose position sizing, concurrent-exposure limits, short
availability and exits before reporting win rate, expectancy, profit factor or portfolio
drawdown. This exit rule is a research experiment, not automatic live trade closing.

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
| 1 — Data feasibility | Coverage/latency/identity manifest for a small historical slice | Minute data verified where needed; unsupported markets explicitly excluded |
| 2 — Offline research | Event replay, O/C comparison CSV/JSON and readable report | Reproducible hashes, prefix/lookahead tests, complete/no-chase cohorts and stated costs; no Telegram/production dependency |
| 3 — Private persistence and settings | Additive schema/API, personal registration, two preferences, monitor status/history | Disposable Postgres access/isolation and upgrade tests pass; no production migration |
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
  a minute boundary, and chasing continuing after every price notification.
- Multiple P1 pairs for one P2, overlapping roots/timeframes, feed change, asset removal,
  opted-out/unregistered users, re-enable without replay, and mapping/preference revisions.
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
