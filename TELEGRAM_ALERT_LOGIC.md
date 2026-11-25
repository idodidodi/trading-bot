# Telegram alert logic — canonical reference

## Stock RSI higher/lower-timeframe watches — delayed SIP update local, 2026-11-25

Last reviewed **2026-11-25** against `stock_rsi_watch.py`, `stock_rsi_data.py`,
`stock_rsi_runtime.py`, `platform_app.main`, and `cloud/stock-rsi-watch.sql`.
The earlier implementation is deployed with the watch disabled; the delayed-SIP
update described below is local and has not been published or enabled. Existing periodic PDF schedules, RSI + PB
PDFs, report destination, portfolio routes and ordinary scanner alerts are unchanged.

### Schedule and candle definitions

- Skip daily watches on the **first trading session of each week**, including a
  Tuesday after a Monday holiday (explicitly reconfirmed 2026-11-25).
- On other trading sessions, daily discovery begins three hours before the
  regular close. Tracking continues until 15 minutes before the next trading
  session opens. On the last session of the week it overlaps the weekly watch.
- Weekly discovery begins three hours before the last trading session's regular
  close. This is one continuous cycle through the next trading session's opening,
  normally Friday to Monday. There is no Monday restart or separate Monday watch.
- Calendar: `market_calendar`, America/New_York, scheduled holidays and early
  closes. Israel display times follow Asia/Jerusalem DST; 23:00 is not hardcoded.
  Unscheduled exchange closures require a calendar update.
- Higher-timeframe discovery uses the current daily/weekly candle only, while
  provisional. The parent is re-evaluated at the regular close, then frozen closed;
  post-market trades do not extend it. New parents are not admitted once the delayed
  data cutoff reaches that close. This allows delivery of a pre-close observation
  during the first 16 minutes after the wall-clock close, explicitly marked delayed.
- Lower timeframes are **15m, 30m, 1h, 4h**, independently, in the same direction.
  Use Alpaca SIP split-adjusted data, regular plus 04:00–20:00 New York extended
  sessions. Clock-aligned 4h buckets are 04–08, 08–12, 12–16 and 16–20; 1h/30m/15m
  align to local clock boundaries. No overnight/weekend prices are invented.
  Tracking persists through closed hours and resumes when actual bars are available.
- Completed native 15m bars plus completed minutes construct provisional lower
  candles. All watch requests use explicit historical `feed=sip`, split adjustment,
  and a watermark of `floor(wall clock to minute) - 16 minutes`, retaining a
  one-minute buffer beyond the free historical SIP restriction. Missing/stale
  endings (over two minutes) suppress evaluation; a closing candle is evaluated
  only once the watermark reaches its end. Cached split-adjusted history is
  refreshed at each New York date boundary. At least 250 candles are required.
  Freshness is measured relative to the delayed watermark, not the wall clock.
  No IEX or real-time fallback is used. Schedule start/expiry and delivery shutdown
  remain wall-clock based: daily stops 15 minutes before next open, weekly at next
  open. Consequently the final delayed portion at expiry is not reported later.

### Qualification, limits and messages

Use the existing regular RSI detector and its frozen per-cycle rules, including
P2 Bollinger touch, RSI tolerance and pivot spacing. No hidden divergence or
pin-bar requirement. The first pivot must be confirmed; the second is evaluated
with `provisional=True` (no future/right-hand candle confirmation). “Ready” means
the exact previously reported lower pivot pair survives its candle close, not
that a right-hand pivot or the higher candle has necessarily closed.

One parent is admitted per symbol/direction/cycle. Choose its most recent qualifying
P1 deterministically; recheck that exact pair. Lower events also retain their exact
pair, but several qualifying P1s in one P2 candle count as one lower match. A candle
already closed at the data watermark when first observed cannot enter the provisional stage. A forming
candle may have started before its parent watch, but its first observation must
follow admission in data time and it must still be open in that delayed snapshot.
It may already be closed by wall clock, particularly on the 15m timeframe.

The target is five distinct validated stocks per direction per cycle. Repeated
timeframes/candles on one stock do not increase that count. Stop admitting parents
for a filled direction. Daily cycles stop creating new lower events once both
targets are reached, while settling already reported pending candles. Weekly
cycles keep tracking admitted parents until expiry, with **four announced distinct
lower candles per stock per timeframe** (up to sixteen). Provisional and close
messages share one slot; invalidated matches consume their announced slot. An
invalid parent is not recycled during that cycle. Historical validated counts are
not reduced if a later parent invalidation is reported.

Dedicated destinations (exported positive IDs verified via read-only Telegram
`getChat` on 2026-11-25; actual API IDs below):

| Stage | Group | Bot API chat ID |
| --- | --- | --- |
| Forming higher divergence | RSI Higher TF Reached | `-5596046632` |
| Forming same-direction lower divergence | RSI Lower TF Reached | `-5462582401` |
| Exact lower pair valid at close | Stock RSI Setup Ready | `-5481883301` |
| Higher or lower invalidation, with failed condition | RSI Stock Setup Invalidated | `-5517376122` |

Every newly generated watch alert (higher, lower, ready and invalidation) has
**DELAYED DATA** in its title and states the **16-minute buffer plus polling/delivery
time**. It shows the delayed data cutoff, detection timestamp and send timestamp
in Israel time with UTC offset. Higher/lower **PROVISIONAL/CLOSED in delayed data**
is separate from each candle's status by clock at delivery. A lower provisional
observation arriving after wall-clock close explicitly says it is historical and
awaits delayed close validation. Pivot prices/RSI and the shared parent identifier
remain included. The data cutoff is the permitted evaluation boundary, not a claim
that every symbol traded at that exact instant; existing freshness guards still apply.
Invalidations explain failed price, RSI, pivot or band-touch conditions. Unavailable
data is never an invalidation or a no-match. The third group receives only validated
closures whose provisional lower message was delivered. If delivery fails until
the delayed watermark reaches the lower candle's close, skip that stale provisional message and its ready
message. Successful Telegram acceptance followed by a lost commit remains an
at-least-once edge case, as with existing delivery.

### Coverage, persistence and rollout

Search S&P 500 → remaining Nasdaq US common stocks → remaining S&P MidCap 400 →
other US-listed US common stocks. Nasdaq/broader fallback requires verified market
cap of at least **USD 2 billion**; absent cap cannot pass. FMP classifications and
security-name exclusions remove ETFs, funds, warrants, preferred shares, rights,
units and debt. Share-class tickers are normalized for Alpaca. Index membership
uses the existing refreshed/cached membership sources, with fallback dates recorded.
If FMP's screener is unavailable, continue known S&P 500/S&P 400 membership and
explicitly record Nasdaq/broader coverage unavailable. Never call that a complete
US scan. Broad-universe records are frozen per cycle; provider failures retry.

The existing signal process owns a separate watch thread using the same worker
ownership/effects gate, not another scanner process or execution engine. It polls
at 30-second intervals, checks admitted parents/lower candles before the next
50-symbol discovery batch, and records coverage/errors. This is best-effort polling;
request duration and universe size affect detection latency. There is no guaranteed
instantaneous delivery or guaranteed five matches when market conditions lack them.

`stock_rsi_watches` stores cycle rules, discovery cursor, coverage, counts, exact
pivot pairs, matches and ordered delivery events. State/outbox are checkpointed
before sends; `telegram_delivery.deliver` provides per-event deduplication, existing
sound-slot behavior, worker ownership checks and retry leases. No ordinary routing,
personal fanout, PDF delivery or trading-order integration is involved. Expired
cycles cannot generate or deliver fresh alerts. Local dry-run/Telegram controls
remain effective. Worker ownership loss propagates to the shared shutdown guard.

Apply the SQL migration explicitly, then opt in with `STOCK_RSI_WATCH_ENABLED=true`
only after access and group permissions are verified. The default is false.
Read-only checks on 2026-11-25 found **recent Alpaca SIP access rejected** and **FMP
company-screener subscription access rejected**. Subsequent authenticated GET-only
checks using production credentials verified delayed SIP access (HTTP 200): AAPL,
MSFT and SPY on November 23–24 included pre-market from 04:00 NY and after-hours
through 19:59 (MSFT November 24 ended 19:56). Today's window ending 17 minutes
before the request returned 121 minute bars per symbol. History requests returned
551 daily and 1,359 hourly bars per symbol. These samples establish account access,
not full-universe completeness or every timeframe's signal accuracy. Broad Nasdaq
and other US coverage remains limited by FMP entitlement; index fallback records
that limitation. No test messages were sent. The delayed implementation can be
enabled only after publication approval and source/deployment verification.
Local verification uses `test_stock_rsi_watch.py` with mocked providers/Telegram
and a disposable loopback PostgreSQL database; it does not establish production
signal accuracy or actual delivery readiness.
Delayed-SIP local verification on 2026-11-25: **68 tests passed**, including all
28 watch tests and Alpaca/report/delivery regressions, using a newly created
disposable loopback PostgreSQL database with mocked providers and Telegram.
New regressions cover delayed query cutoffs, higher close deferral, post-close
discovery of delayed provisional data, 15m provisional-to-ready transitions,
message labels and delayed delivery deadlines versus wall-clock cycle expiry.
Verification on 2026-11-25: 100 targeted scanner/report/watch/delivery tests passed;
the 23 watch tests passed again after final data-validation and delivery-expiry
changes. Compilation and `git diff --check` passed. Read-only `getChatMember`
confirmed the bot is an administrator in the first two groups and a member in the
ready/invalidation groups; actual end-to-end delivery has not been exercised.

Post-publication receipt (recorded locally after the verified rollout): commit
`0bfc1ff880112fd2a0a0b17cffe8b5a166367703`, branch `codex/stock-rsi-watch`, deployed
to `trading-linux:/home/ido/apps/trading-bot-postgres` on 2026-11-25. Exact-SHA
[Linux/Windows tests, builds and frozen-app smoke tests](https://github.com/idodidodi/trading-bot/actions/runs/45595851818)
and [desktop builds](https://github.com/idodidodi/trading-bot/actions/runs/45595852089)
passed; the Cloudflare branch check passed. Legacy executable publication remained
intentionally skipped. No production UI deployment was requested or performed.
Every installed runtime blob matched the commit. Health and source manifest matched
scanner `94697a0433fdd8aa`, UI `6097720c5ac73e84`; exactly one worker held ownership.
The additive watch schema and worker grants were verified, with zero watch rows.
`STOCK_RSI_WATCH_ENABLED=false` is explicitly set in the production environment;
the disabled worker path and sixteen simulated ready events passed without live
sends or production signal writes. Unauthenticated API access returned the expected
401. Source rollback backup:
`/home/ido/apps/trading-source-backups/20261125T103731594047Z`.
The rollout bundle, private environment backup, schema backup and verification
scripts are in `/home/ido/stock-rsi-watch-rollout`. Activation remains blocked on
the data entitlements described above.

Reviewed **2026-11-24** for the prepared NEAR 5m RSI ATR gate; **2026-11-24** for exclusive periodic stock-PDF routing (local, not deployed); **2026-11-23** for verified stock-report schedules, daily RSI PDFs and market-cap metadata; **2026-11-23** for additive NEAR routing (local, not deployed); **2026-11-22** for the prepared trend-only pin-bar correction; **2026-11-21** for the locally prepared NEAR 5m RSI14 override (not deployed); **2026-11-20** for regular/hidden RSI and the explicit NEAR-only Telegram subscription (source and rollout requirements reviewed); **2026-11-19** for Pin-Bar candle photos and caption controls; **2026-11-19** for staged Clerk identity handling (local only, not deployed); **2026-11-18** for fresh configuration reads with conditional payload transfer (prepared locally, not deployed); **2026-11-15** for the opt-in local PostgreSQL working backend (verified deployed at `ebe79c1` on 2026-11-18); **2026-11-14** for market-session request eligibility; **2026-11-13** for optional local cache freshness/publication; **2026-11-07** for global five-minute Telegram exclusion and personal sound controls; other sections retain their stated review scope. This file is the single current alert-behavior reference. Historical strategy/deployment notes describe their releases; when they conflict, use the implementations cited here. Telegram delivery, credentials, recipient data and callback ownership remain in this service.

## NEAR 5m RSI large-candle exception — prepared locally (2026-11-24)

For the NEAR group's plain RSI alerts only, the selected 5m checkbox now means
exception-only delivery: the divergence's second pivot candle must either itself
have a high-low range of at least 3× preceding ATR(14), or immediately follow a
candle meeting that threshold. Both bullish/bearish and regular/hidden divergences
use the same gate. RSI remains 14 close. Other frames, assets, pin-bar alerts and
Wyckoff annotations are unchanged. The checkbox stays selected to enable exceptions;
unchecking it suppresses exceptions too. Settings explains this behavior.

`near_rsi_atr.annotation` computes Wilder ATR from true ranges: seed with 14 ranges,
then `(13 * previous ATR + TR) / 14`. True range includes the previous close;
the initial available candle uses its high-low range. Compare each candidate's
high-low range against ATR **before** that candidate, excluding its own true range.
Equality at 3× qualifies. Only closed candles through the divergence close are used;
no future data, direction/color requirement or longer lookback exception is added.
A gap resets ATR history and a non-adjacent prior candle cannot qualify. Insufficient
history or zero ATR does not qualify. Evidence records the qualifying candle,
ATR, range, ratio and whether it is the signal candle or its immediate predecessor.

`scanner.scan_once` attaches this evidence after signal validation, before storage.
`telegram_asset_subscriptions.eligible` calls `near_rsi_atr.allows` during delivery:
missing, old-version or nonqualifying evidence suppresses the 5m RSI alert, including
previously queued retries. Qualified alerts keep existing enrollment, destination
routing and deduplication. Findings and RSI scans remain available to other consumers;
5m scanning is still necessary to find exceptional alerts. No subscription/schema
migration or RSI baseline reset is needed. This change is not yet deployed.
Local verification: 99 backend regression tests passed against disposable Postgres,
including current production routing; 16 UI tests and the static build passed.



## Current Portfolio — verified activated (2026-11-24)

Read-only Telegram API verification confirmed the user-supplied Current Portfolio
group is `-5276778238`, with `idodiTradingBot` an administrator. Production managed
configuration revision 51 is fully applied and enables CRWV, ALK, NVDA, ANT, AMAT
and AUR on 4h, daily, weekly and monthly. All six enable RSI, Pin Bars and daily
SMA crosses; all except ALK also enable RSI + PB. This change does not enable
additional strategies or alter scan qualification. ANT remains subject to limited
history following its recent enrollment.

The local `telegram_portfolio.py` delivery hook adds copies to this group for
those exact six canonical IDs and the four listed frames. Notification metadata
is read from the relevant signal table; RSI uses its structured payload. Existing
market and personal routing remains in place. Reports, demos, unknown notification
types, other assets and lower timeframes are excluded. The example configuration
is disabled: activation requires `telegram-portfolio.json` beside the runtime
modules, enabled with a positive enrollment timestamp. Findings whose confirmation
predates enrollment cannot be copied, including old queued findings. Membership
is the latest user-supplied list, not every asset marked portfolio. Sunday updates
replace the list; retained assets preserve enrollment and new assets receive a new
`asset_enrolled_at` timestamp. Removed symbols stop receiving copies. No reply
leaves the current list unchanged. The reminder is scheduled for Sundays at 09:00
Asia/Jerusalem.

Copies reuse `telegram_delivery.deliver` with the original event identity and
separate chat destination for durable deduplication and retries, existing sound
settings and shared dismissal behavior. A portfolio delivery RuntimeError leaves
the event retryable after attempting existing groups. Worker ownership and
retryable database exceptions propagate. No fallback sends portfolio-only copies
to another destination. A portfolio send may succeed before another destination
fails; the normal ledger prevents sending its copy twice on retry. Trend-pin-bar
copies, if such a strategy is enabled in future, reject retired reversal findings.

Verification: five local and staged-production unit tests cover exact assets/frames, enrollment,
notification strategy metadata, report/demo exclusion, independent shared delivery
on portfolio failure and worker ownership propagation. Telegram membership and
managed configuration were inspected without sending alerts or starting a scanner.
Publication was authorized and the existing Linux worker was updated at 10:12
Israel time on November 24. `PORTFOLIO_DEPLOYMENT.json` records the source commit,
base routing hash, deployed hashes and backup. Both deployed file hashes matched;
the worker is active and managed revision 51 remains fully applied. A live-config
smoke test using mocked delivery accepted all 24 symbol/frame combinations and
rejected lower frames, other symbols and pre-enrollment findings without writes,
alerts or orders. Five tests passed against the staged production module. The
production routing retains its existing fixes; `scripts/patch-portfolio-routing.py`
applies only this hook. Natural portfolio delivery has not yet been observed;
GitHub checks are verified separately against the final pushed SHA.
Sources: `telegram_portfolio.py`, `telegram_routing.py`
(`deliver_routed`), `telegram-portfolio.example.json`, `test_telegram_portfolio.py`.

## S&P 500 PDF reports — local update, production not yet verified (2026-11-23)

Sources: `stock_reports.schedules/period/new_run/evaluate/process/artifacts/eligible`,
`stock_report_data.fetch_batch/fetch_preclose/market_caps`, `stock_report_pdf.render`,
`telegram_documents.deliver_document`, and `cloud/stock-reports.sql`.

- When `stock_reports.enabled` is true, the independent full-constituent report scan
  runs without price preselection or portfolio exclusion. It writes report rows and
  PDF bytes only: it never creates live signals, orders, strategy baselines or pending
  RSI + PB setups. Permanent-asset alert behavior is unchanged.
- **Daily RSI divergence and RSI + PB:** each US trading session, scheduled 20 minutes
  after the regular close to allow closing data to arrive. Only completed daily candles
  are evaluated. Each strategy/direction has its own matching PDF (up to four), with all
  matching stocks in that PDF. No daily standalone Pin Bars or BB Pin Bars reports.
- **Weekly/monthly standalone Pin Bars and BB Pin Bars:** existing matching PDFs move
  to two hours before close on the last trading session of the week/month. Both bullish
  and bearish directions remain separate. The later Sunday/first-of-month run no longer
  includes these strategies. No additional after-close copy is created.
- **Weekly/monthly RSI divergence and RSI + PB:** retain Sunday 08:00 / first calendar
  day 08:00 Asia/Jerusalem, respectively, for the preceding completed week/month.
  RSI uses the existing `scanner.detect(..., provisional=True)` pivot-close stage:
  its input candle is closed, but later right-side pivot confirmation is not required.
  RSI + PB retains its existing immediate-next-candle qualification and direction rules.
  Daily RSI reports use these same detector semantics; “after close” is not a change
  to confirmed-pivot detection. No indicator parameters were changed.
- The existing `market_calendar` determines sessions, scheduled holidays, early closes,
  and New York DST. Unexpected exchange closures require a calendar update. Different
  timeframes due on the same date produce independent report sets. The worker revisits
  reports at most every five minutes when idle; scan/provider/Telegram latency means
  actual delivery follows the scheduled instant rather than guaranteeing an exact minute.
- Pre-close reports freeze their snapshot at the scheduled instant, not the retry time.
  They combine closed native historical candles with one partial current candle built
  from completed daily bars earlier in the period and regular-session one-minute bars
  through the snapshot. Missing earlier sessions or a last minute bar more than five
  minutes stale make the snapshot unavailable. Later trades are excluded even on retry.
  PDFs/captions say **Provisional**; the price column says **Latest price (USD)** and the
  time group says **Snapshot**, not final close. These signals can change before close.
- All matching PDF stock tables now include **Market cap (USD)**, formatted in M/B/T.
  FMP `market-capitalization-batch` uses the existing `FMP_API_KEY`; entitlement, rate
  limits, malformed values or absent credentials can leave cells **Unavailable**.
  Lookup is only for matching unique tickers, once per report run; the snapshot is
  persisted and reused across its PDFs/retries. PDF notes identify source, retrieval
  time and available provider dates. Values are latest available company market caps,
  not historical values at the signal candle. They never filter signals or change
  non-PDF alerts. Market-cap production access has not been verified for this update.
- Membership and company/sector metadata refresh together. On refresh failure, use the
  latest saved membership, then the dated repository universe; missing names/sectors are
  disclosed as unavailable. Target 250 history candles, with each strategy enforcing its
  own minimum. Limited warm-up is disclosed separately and does not invalidate an
  otherwise eligible signal. Newly listed stocks may have shorter history.
- Empty strategy/direction pairs with evaluated data share the existing no-matches PDF;
  missing/short history goes into coverage notes instead of being treated as no matches.
  Coverage may add another PDF beyond the four matching PDFs. A stock may qualify for
  both RSI strategies. PDF grouping still uses signal candle open/end timestamps.
- Runs freeze membership, feed, rules, strategy subset, period, cutoff and deadline.
  Checkpoints, ownership leases, persisted PDF bytes and existing Telegram reservations
  remain. Daily retries use the New York session identity and remain eligible until New
  York midnight, including after Israel midnight. Pre-close retries expire at that day's
  session close; legacy RSI runs expire at Israel midnight. Provider failures retry hourly
  when at least an hour remains, otherwise produce disclosed unavailable coverage.
  Expired unfinished runs become `missed`; no historical catch-up runs are created.
  Already saved legacy runs/PDFs keep their frozen contents and original identity.
- **Dedicated PDF routing (local change, not yet deployed on 2026-11-24):** when
  `TELEGRAM_STOCK_REPORT_CHAT_ID` is set, every periodic stock PDF goes exclusively
  to that group: daily/weekly/monthly, all strategies/directions, and no-match/coverage
  PDFs. This bypasses strategy/General and personal/subscription fan-out for PDFs only;
  ordinary signal alert routing is unchanged. A failed dedicated destination is retried
  under the existing report deadline and never falls back to another group. The shared
  delivery ledger, worker ownership check, one-sound-per-minute cap and report dry-run
  gates remain. Empty configuration preserves legacy PDF routing. Invalid positive or
  nonnumeric IDs fail before sending. Previously delivered documents are not replayed.
  Read-only Telegram `getChat` verified the requested group as **Periodic Stocks Reports**,
  Bot API chat ID **-5347004785**, type `group`, with document sending allowed.
  The positive exported ID 5347004785 is not the Bot API destination.
  Source: `telegram_documents.deliver_document`, `telegram_delivery.deliver`.
  Local verification: 42 report/schedule/delivery tests passed against disposable
  Postgres with mocked Telegram, including all PDF categories, duplicate suppression,
  the shared sound cap, invalid IDs and no fallback after destination failure.


Deployment prerequisite: reapply `cloud/stock-reports.sql` as schema owner to allow
`daily` in the existing run table; it preserves historical rows/documents and permissions.
Local verification: 84 report, schedule, RSI, RSI + PB and Alpaca tests passed on the
production-based branch against disposable Postgres, with report-provider/Telegram calls
mocked. The upgrade was rerun over the old timeframe constraint and retained saved PDFs.
Single-page and 100-stock multipage PDF previews were rendered and visually inspected.
No production scanner was started and no test alerts/orders were sent.
This section describes local code only; it does not claim this release is running in production.


Verified production rollout (2026-11-23): source
`35c180fc52fc0373383052a67d82588a7c2f7b41`, branch `codex/stock-report-schedules`,
is installed at `trading-linux:/home/ido/apps/trading-bot-postgres`. This verification
supersedes the pre-publication status above. The active runtime database is local
PostgreSQL `trading_runtime`, not Supabase. The report migration preserved counts,
content checksums, delivery flags and report configuration. A full database dump is
at `/home/ido/stock-report-rollout-35c180f/trading_runtime-before.dump`; source backup:
`/home/ido/apps/trading-source-backups/20261123T170815929713Z`.
All installed runtime Git blobs matched the approved commit. Served scanner fingerprint
`a06a2b6d08b98509` and unchanged UI `484da62cc65f7d4d` matched the source manifest.
Both existing services were active; exactly one worker owner and the API's 401
unauthenticated boundary were verified. Pure schedule/PDF smoke tests passed without
persisting report rows, starting a scanner or sending test Telegram alerts/orders.
A read-only FMP sample returned AAPL market cap dated 2026-11-23; this does not prove
coverage for every constituent or future natural delivery.
[Linux/Windows tests, builds and frozen-app smoke tests](https://github.com/idodidodi/trading-bot/actions/runs/45223092693)
and [desktop checks](https://github.com/idodidodi/trading-bot/actions/runs/45223092615)
passed on that exact SHA. The Cloudflare branch-preview check passed; production UI
redeployment was unnecessary. Legacy executable publication stayed disabled.
This rollout note is a local verification record, not another published source revision.

## Additive NEAR routing — prepared locally (2026-11-23)

This section supersedes the exclusive-routing and scan-demand behavior of the
November 21 release below. Production was inspected read-only on November 23 and
still has those exclusions; the correction here is not yet deployed.

The NEAR subscription is an additional destination. NEAR RSI follows the existing
shared crypto RSI route (General remains its fallback) and enabled personal RSI
routes, as well as the dedicated subscription. Trend pullback pin bars similarly
use shared/personal crypto pin-bar routes plus the dedicated subscription. Legacy
EMA/BB pin bars again reach eligible other groups, but remain excluded from the
dedicated NEAR group, whose pin-bar choice selects only trend pullbacks. Retired
reversal payloads remain suppressed before delivery to any destination.

The general 5m Telegram mute remains: only explicit subscriptions receive those
copies. Dedicated group selections and enrollment cutoffs apply only to that
destination, including when it also appears in a shared or personal route. Other
personal opt-outs, enrollment times and sound preferences remain effective.
Deduplication is by bot/destination plus event, so one event reaches each eligible
group once, including when multiple routes name the same group. A dedicated
subscription delivery failure leaves the event retryable but does not prevent
personal copies; accepted destinations are skipped on retry. Missing shared
configuration does not prevent NEAR subscription or personal delivery.

Other eligible shared/personal groups now count as scan consumers even when NEAR
checkboxes are off. Trend pullback scans can remain active for those groups on
supported configured frames; existing asset eligibility and higher-frame RSI
context dependencies still apply. A dedicated destination alone does not count
as broad demand when its box is off. Resumed strategies retain fresh cutoffs and
baseline behavior; this change does not reset queues or replay suppressed history.

Sources: `near_alerts.needed/plan/allows_destination`,
`telegram_routing.select/deliver_routed`, `telegram_personal.route_for/deliver_personal`,
`telegram_asset_subscriptions.deliver_subscriptions`, `telegram_delivery.deliver`,
`scan_schedule.identity`, `candle_strategy.scan`.
Verification: 82 targeted tests passed against disposable Postgres with mocked Telegram transport; regression tests
in `test_near_redundant_routing.py` cover independent copies, destination dedup,
opt-outs, retry isolation, scan demand, 5m exclusion and retired reversals.
No production settings, queue rows, messages or orders were changed.

## Trend-only pin-bar correction — prepared locally (2026-11-22)

This correction supersedes the reversal qualification described for the November 21
release below. `trend_pin_bars.load_rules/detect` version `trend-pin-bars-v4` only
emits trend-aligned pullback pins: bearish after a downward impulse and upward
pullback, bullish after an upward impulse and downward pullback. Existing wick,
five-candle impulse and 1–12 candle pullback criteria are unchanged. The standalone
countertrend reversal fallback is removed. The exact Kraken 4h bearish alert closing
22 November at 18:00 Israel is now rejected, as is its bullish mirror. The previously
accepted 19:00 reversal is also rejected under the user's corrected requirement;
04:30 remains accepted and 13:45 remains excluded. No future candles are used.

`telegram_asset_subscriptions.deliver_subscriptions` rejects queued new-pin-bar
payloads unless `setup` is `trend_pullback`, including retries from the retired
reversal rule. Existing delivered messages and stored history remain intact. The
version change establishes fresh strategy baselines without historical replay.
Settings now labels this strategy “Pin bars: trend pullback.” RSI, Wyckoff annotation,
selected timeframes and the exclusive NEAR destination are unchanged. This correction
has not yet been pushed or deployed; the production record below describes v3.

## NEAR notification controls — verified production (2026-11-21)

The owner-only Settings grid chooses RSI divergence and pin bars (trend pullback plus reversal)
independently on 5m, 15m, 30m, 1h, 4h, daily and weekly. The existing single NEAR
subscription remains the destination authority; no browser-supplied chat/user ID
is accepted. `cloud/near-group-preferences.sql` verifies installation ownership
and runtime identity, serializes saves and checks revisions. Empty selections
pause that category for this group. Group migrations cascade preferences.
Unchanged selections retain their enrollment cutoffs; newly enabled cells receive
a fresh cutoff. Old queued alerts cannot bypass an unchecked box via broad routes.
Legacy EMA/BB pin-bar copies are excluded from this group's broad route; the grid's
pin-bar row selects the new pullback/reversal rules. NEAR RSI and the new pin bars go only to the explicit NEAR group on every
selected frame. There is no General, market-category or personal-group fallback,
even on a delivery failure. Legacy NEAR EMA/BB pin-bar copies are suppressed;
other assets retain their existing routing. Other NEAR strategies such as RSI + PB
are outside this subscription change.
Sources: `web/app.js` (`nearGroupControls`), `telegram_asset_subscriptions.py`,
`telegram_routing.py`, `near_alerts.allows_destination`.

Migration initially enrolls trend pin bars on the existing enabled NEAR RSI
subscription's frames, using the migration time as enrollment. Rerunning preserves
saved settings. It creates independent trend state/signals and demand-state tables.
These alerts use the existing notification queue, photo renderer and per-destination
ledger, with one signal identity per source, strategy version, frame, direction and
candle. Repeated scans do not resend. This feature requires the SQL migration before
scanner/API/UI deployment; the migration and release are now verified below.

For NEAR only, the normal scanner considers active group subscriptions, live signal-service subscribers. Broad shared and personal Telegram routes no
longer count as consumers of NEAR RSI/pin bars because these alerts are group-only. Needed higher
RSI frames remain scanned for a subscribed 5m context consumer; RSI + PB and its
opposing evidence remain separate dependencies. Legacy EMA and BB pin-bar checks
can be skipped when no consumer needs them. If a frame has no strategies left,
the scanner performs no feed fetch or strategy evaluation. Selections are refreshed
on the next cycle; delivery rechecks current settings. A resumed strategy starts
at a fresh cutoff rather than replaying its paused interval. Sources:
`near_alerts.snapshot/needed/plan`, `scanner.scan_once`, `scan_schedule.identity`,
`candle_strategy.scan`. Conservative signal-service demand does not prune by
strategy version or direction; these subscribers may still require inspection.

### Pullback and reversal pin-bar qualification

`trend_pin_bars.py` is independent of existing EMA/BB pin-bar strategies. It uses
five completed pre-pullback candles: at least three of the four close-to-close
changes agree with the trend, and net movement exceeds the mean high-low range
of those five candles. One to twelve subsequent candles may alternate direction, but their closes must
remain at or behind the impulse’s strongest final close and the final pre-signal
close must be against the trend. Including the signal candle's extreme, retracement must be
positive and no more than two-thirds of the trend's first-to-last close move; the signal
close must remain inside the pullback rather than beyond the prior trend close.
A bullish trend requires a dominant lower wick; a bearish trend requires a dominant
upper wick. The dominant wick must exceed twice the body and exceed the other wick;
the short wick is at most 0.66 of the long wick. Body color does not determine bias.
The signal is emitted at pin-bar close, including during an ongoing pullback: no
later candle or breakout confirmation is used. Version: `trend-pin-bars-v3`.

The user’s Bybit NEARUSDT perpetual 15m example at 2026-11-21 04:30 UTC−4 is
verified against saved public OHLC in `tests/fixtures/near-bybit-2026-11-21-0430.json`:
a six-candle mixed pullback with approximately 55.38% retracement qualifies at
04:45 UTC−4 without any later candles. This is a regression example, not a live
feed change: live NEAR stays on Kraken spot. The user accepted the 19:00 reversal and explicitly excluded 13:45 indecision.
Both now have historical regression fixtures; 19:00 qualifies and 13:45 does not.

Reversal pins use the same strict wick/body criteria. The preceding ten candles
must have net movement opposite the pin’s direction exceeding their mean high-low
range. A bearish pin must reach or exceed their highest high; a bullish pin must
reach or fall below their lowest low. No subsequent reversal confirmation is needed.
When one candle qualifies for both setups it emits only one finding, prioritizing
trend pullback. The saved `setup` and Telegram heading distinguish `trend pullback`
from `reversal`. The Settings pin-bar row enables both accepted setups; no indecision
category is enabled. A real Kraken 15m example closing on 2026-11-21 at 17:45
Israel time was reviewed with `Wyckoff stage (estimated): Markup`; the estimate
used only history available at that close, including Kraken volume.

### Wyckoff stage annotation

NEAR RSI and pin-bar findings save an informational estimate from the same frame,
using only candles closed by the signal's timestamp. The Telegram line is
`Wyckoff stage (estimated): Accumulation`, Markup, Distribution, Markdown or Unclear.
It does not gate signals or claim a full Wyckoff schematic/phase identification.
`kraken_feed.fetch_kraken` retains finite nonnegative volume from OHLC element 6,
keyed by candle start, excludes the final uncommitted bar and uses the existing
paced request. There is no separate volume request. Invalid/missing volume does
not block an otherwise valid price signal; the line explicitly says `no volume`.
Volume is attached to the fetched snapshot and summarized in the saved finding's
annotation, not retrofitted into the historical OHLC cache.

`wyckoff_context.analyze` requires 40 completed candles. The latest 20 define mean
high-low range, net move and directional efficiency (absolute net move divided by
sum of absolute close changes). A move of at least three mean ranges with efficiency
at least 0.35 estimates Markup/Markdown. Otherwise a range with net movement at most
two mean ranges and total width at most eight mean ranges can estimate Accumulation
following a preceding 20-candle decline of at least three mean ranges, or Distribution
after an equivalent rise. Range estimates additionally require valid volume on all
40 candles, positive total volume and recent volume-weighted close-location pressure
at least +0.10 or at most -0.10 respectively. Other cases remain Unclear. These are
explicit implementation heuristics, not thresholds prescribed by Wyckoff.
Sources: [Kraken OHLC](https://docs.kraken.com/api-reference/market-data/get-ohlc-data),
[Wyckoff method](https://www.wyckoffanalytics.com/wyckoff-method/),
`wyckoff_context.py`, `near_alerts.annotation`, `platform_app.message`,
`trend_pin_bars.message`, `pin_bars.message`.

The final marked-example revision passed all 12 focused engine/database/settings
tests, all 16 UI tests and the dashboard build. Both accepted historical candles
qualify using only data through their own closes; the excluded 13:45 candle fails.

Local verification: the integrated 80-test backend suite passed against disposable
PostgreSQL; five focused engine tests (including an added range/volume test) and 16
UI tests passed. Browser interaction verified the real settings component with
mock data and its checkbox/save feedback. Real Kraken NEAR/USD history supplied
three 5m examples (two bearish, one bullish), without replaying them into queues.
No production settings, alerts or orders were changed during this verification.

### Production verification — 2026-11-21

Deployed source `410b93ad041352061caef87da4564a1ac065eb40` from branch
`codex/near-notification-controls` to `trading-linux:/home/ido/apps/trading-bot-postgres`
and the existing scanner/API services. Applied `cloud/near-group-preferences.sql`
to `trading_runtime` after verifying a private database backup. Health and installed
source matched UI fingerprint `4af21f3a4d4e3544` and scanner `758bf630e4d515b1`;
exactly one scanner owned the worker lock. The authenticated owner API read passed.
The sole destination is `-1004484031372`; RSI and new pin bars are selected on
5m, 15m, 30m, 1h, 4h, daily and weekly. All seven pin-bar baselines initialized
naturally. Legacy pin-bar calculations still run where live signal-service subscribers
need them, but their NEAR Telegram copies remain suppressed. Higher-timeframe RSI
context remains an independent scan consumer.

Cloudflare Pages production deployment `22040f83-8a33-4fe4-80d9-8f068464a7df`
serves the exact built assets at https://app.family-trading-bot.dynv6.net with the
existing Clerk configuration unchanged. The deployment metadata records the exact
source SHA; Pages' production branch metadata remains `codex/clerk-auth`.
[Pages deployment](https://22040f83.family-trading-bot-dashboard.pages.dev).
[Linux/Windows builds and frozen-app smoke tests](https://github.com/idodidodi/trading-bot/actions/runs/44953509127)
and [desktop checks](https://github.com/idodidodi/trading-bot/actions/runs/44953509054)
passed on that SHA, as did the external Workers build check. The legacy publication
job remained intentionally disabled. No test alerts or orders were sent; these checks
do not claim receipt of a future qualifying alert. Wyckoff labels remain estimates.

The implementation and historical regression fixtures are committed in the isolated
checkout `/private/tmp/near-notification-ui`; the original working checkout's unrelated
changes were preserved. This post-deployment record is a local documentation update,
not an additional published source revision.

## NEAR/USD five-minute RSI profile (prepared 2026-11-21)

Only the configured asset `NEARUSD`, symbol `NEAR/USD`, on `5m` uses RSI 14
on candle closes with the existing Wilder smoothing. This replaces RSI 3 for
all four plain RSI divergence types: regular/hidden, bullish/bearish. The
Bollinger, pivot, tolerance and closed-candle qualification rules remain unchanged.
Other assets, other NEAR timeframes and RSI + PB retain their existing parameters.
Sources: `rsi_profiles.py`, `scanner.scan_once`, `scanner.indicators`.

The profile has its own version and scan-state identity. Its first scan establishes
a fresh baseline without sending historical matches. Only that profile is scanned
for NEAR 5m; no parallel RSI 3 path is enabled. The delivery worker cancels pending
or retrying plain NEAR 5m RSI 3 queue rows before routing. Findings and completed
sent ledgers remain intact. Existing pivot-close event grouping and per-recipient
deduplication still apply: multiple comparisons for one event send once, while
regular and hidden events remain distinct. Sources: `platform_app.delivery_worker`,
`platform_app.signal_alert_event`, `telegram_asset_subscriptions.deliver_subscriptions`.

Stored-history Python and legacy cloud replay entrypoints apply the same scoped
profile; Python replay rows expose their effective rules. CSV upload replay still
supports 1h and above only. Generic detection engines remain parameter-driven so
RSI + PB keeps its own rules. No broker execution allowlist is changed: a pinned
older strategy version does not authorize execution of the new RSI14 version.
Sources: `backtest.replay`, `cloud/functions/backtests/engine.mjs`,
`cloud/functions/backtests/index.ts`, `csv_backtests.parse`, `signal_contract.py`.

Production was read-only verified at source `96decf4536c5eb0d32b913767e9f70ae87a0dbf1`
before this change. The enabled NEAR-only subscription now points to supergroup
`-1004484031372`, following migration of the original group `-5412729128`.
It retains 5m, 15m, 30m, 1h, 4h, daily and weekly. The earlier migration recovery
verified bot membership and natural delivery; this change does not reset enrollment,
change routing or send test messages. Group renaming alone does not change routing.
The RSI14 override described above remains local until explicitly approved and
verified deployed; it does not claim a change to current production calculations.

Local verification: 65 Python tests passed against a fresh disposable PostgreSQL
database, plus both JavaScript replay suites. Coverage includes all four divergence
types, Wilder close calculation, scoped parameters, a fresh scanner baseline,
restart deduplication, old queue cancellation and one subscribed delivery for
multiple comparisons. Telegram transport was mocked; no test alerts were sent.


## Staged Clerk authentication and personal settings

The opt-in Clerk browser mode sends personal-setting requests to the configured
Linux API. `clerk_auth.SessionVerifier` maps a verified Clerk subject to the
existing application UUID through an administrator-controlled mapping;
`clerk_dashboard_api.Gateway.dispatch` sets this UUID as the transaction-local
`auth.uid()` identity and retains the existing SQL membership/role checks.
Browser-supplied user IDs and email/metadata claims do not select recipients or
grant access. The gateway's database role cannot create or alter these mappings.
Session changes clear the UI and invalidate pending responses. Tests against a
disposable database verify separate owner/viewer/editor preference rows and
rejection of cross-user edits. This implementation is not deployed; production
still uses Supabase authentication and existing Telegram routing/delivery state.
Full local runtime migration and permission-parity verification remain cutover
prerequisites. No signal qualification, queue, notification sound or delivery
behavior is changed by this staged authentication work.

The prepared local runtime cutover keeps the same scanner and Telegram workers,
including pending deliveries, deduplication and callback offsets. Stored-history
replays use the existing `backtest_jobs.worker` queue; CSV uploads use a separate
bounded low-priority process (`local_csv_replay.py`), with no scanner or sender.
`cloud/local-replays.sql` preserves SQL authorization and rechecks CSV save
permissions. Production routing and the final data transfer remain unverified.


## Configuration read freshness

`managed_assets.get` checks both one-time migration flags and the current configuration content hash on every call. Unchanged payloads reuse a process-local parsed copy, while revisions and applied revisions are always read freshly. Changes to payload content are fetched immediately even if an external writer leaves the revision unchanged. Returned configurations are independent copies. Database failures propagate; there is no time-based cache or stale fallback. The migration lock/recheck, explicit strategy opt-outs, worker ownership and effect checks remain authoritative. Sources: `managed_assets.get`, `managed_assets.migrate_rsi_pb`, `managed_assets.migrate_ma_cross`, `postgres_store.assert_worker`.

## Qualification and timing

- RSI findings are validated against the Markdown-derived rules, including close-based divergence, inclusive RSI tolerance, pivot spacing and second-pivot Bollinger-band wick touch. A provisional divergence is available at the pivot candle close. Structural confirmation after the configured right candles remains a finding but does not send another Telegram alert. Legacy clock-review slots are cancelled. Multiple first-pivot references share the logical second-pivot alert identity within their divergence type. Hidden findings have separate identities and a Hidden RSI message label; old records without a type are regular. Sources: `platform_app.py` (`validate_signal`, `Store.insert`, `signal_alert_event`, `signal_alert_delivered`, `delivery_worker`), `scanner.py` (`detect`).
- Scanner baselines, selected-universe start times, continuous-feed gaps and provider freshness gates remain in `scanner.scan_once`. First runs establish a baseline rather than flood Telegram with history. Catch-up findings are summarized, not sent as new signal alerts. No new API subscriber, replay or acknowledgment resets any baseline or queues a Telegram message.
- Pin-Bars and BB Pin-Bars use independently versioned closed-candle rules and the existing selection/baseline cutoff. A newly persisted eligible signal enqueues its existing notification only outside catch-up. BB Pin-Bars share the Pin-Bars routing category. Sources: `pin_bars.py`, `bb_pin_bars.py`, `candle_strategy.py:12` (`scan`).
- RSI + PB holds a versioned pending RSI setup and evaluates only its next expected candle, with market/session-aware scheduling. It does not substitute a later candle. Qualification, opposite-wick shape, direction, stale-data protection and missing-next-candle handling remain in `rsi_pb.py:28`, `:37`, `:82`, `:95`, and `:131`. Only new qualifying non-catch-up findings enqueue `rsi-pb:<id>` notifications. Source message rendering: `rsi_pb.message`.
- Daily momentum detection remains available in the dashboard and signal API. This change does not introduce a new momentum Telegram route or emit simulated trade outcomes from the signal service.
- `quiet_hours` is 23:30–06:30 Asia/Jerusalem, with timezone/DST conversion. Existing callers decide when to use silent delivery; the separation does not broaden that policy. Source: `platform_app.py:271` and `send_telegram`.

<<<<<<< HEAD
## Candle diagram delivery (EMA and BB Pin Bars)

Local source sends each `pin-bars:` and `bb-pin-bars:` queued alert as one PNG photo with the existing formatted signal text as its caption. At persistence time, save up to 24 closed OHLC candles ending at the signal candle in `chart_candles`; later delivery uses that snapshot without fetching current data or including candles after the signal. The diagram outlines the signal candle, shows OHLC prices and the Israel-time confirmation, and uses green/red bodies for close versus open (body color does not define signal bias). Existing stored signals without a snapshot render their single saved OHLC candle. No schema migration or qualification/version change is required.

The photo uses the existing routed delivery reservation and message ID, personal eligibility/preferences, shared fallback, sound controls and retry behavior. A missing signal or rendering/upload error leaves delivery pending for the existing retries; no separate text-only success is recorded. Dry runs do not render or upload. Rendering is deferred until the routed sender is called, after timeframe exclusion and recipient eligibility checks; suppressed five-minute events never render a photo. Other strategy alerts retain their existing format. Done/Undo edits photo captions and deletion removes the complete photo message; durable original captions survive listener restarts. Legacy checklist controls also edit captions. Telegram captions are limited to 1,024 UTF-16 units in the sender; oversize captions fail before upload. Ambiguous accepted sends retain the existing at-least-once limitation.

Sources: `pinbar_chart.py` (`snapshot`, `render`, `notification_sender`, `photo_request`), `candle_strategy.py` (`scan`), `platform_app.py` (`delivery_worker`, `send_telegram`), `telegram_personal.py` (`prepare_message`), `telegram_dismissal.py` (`edit`, `handle`, `expire`), `telegram_checklist.py` (`prepare`, `handle`). Production rollout is not yet verified. On 2026-11-19 Telegram accepted one preview in each of the owner’s four verified personal Pin Bars groups (Forex, Crypto, Stocks, Other); this does not verify production rollout. Personal preview tests use explicitly labeled synthetic data, silent delivery and no callback controls; production routing and control persistence are tested separately against a disposable database.

=======
## Hidden RSI and exact-asset Telegram subscriptions

Implementation; verify activation against the installed source and subscription row:

- The live RSI scanner and Python/hosted RSI replays include regular and hidden
  divergences. Hidden bullish requires Close[P2] > Close[P1] and
  RSI[P2] ≤ RSI[P1] + 1; hidden bearish requires Close[P2] < Close[P1] and
  RSI[P2] ≥ RSI[P1] − 1. The existing 2–60 spacing, strict closing-price pivots,
  RSI 3/Wilder, BB20/2, and P2 lower-band bullish / upper-band bearish wick touch
  remain. Equal price pivots do not qualify. The inclusive RSI tolerance can
  admit up to one point against the ideal RSI direction, as for regular signals.
- Core regular rules retain version `b4b8c578656c86fb`; the appended skill extension
  is versioned separately. Hidden evidence carries `divergence_type=hidden` and
  `version=rsi-hidden-v1-<core hash>`, which the signal API preserves. Finding IDs,
  Telegram event IDs and previously delivered/suppressed checks distinguish the
  types. Both types alert at pivot close; later structural confirmation is not a
  second Telegram alert. Hidden API lifecycle updates use the hidden version.
- The reusable Python detector remains regular-only unless explicitly enabled.
  RSI + PB, existing chasing consumers, and the regular higher-timeframe context
  stay regular-only. Hidden deliveries do not enroll automatic chasing. No
  executor configuration or version allowlist is changed: the separate NEAR RSI
  demo executor requires explicit reviewed strategy versions, so its existing
  regular-version approval does not authorize hidden signals. Regular baselines,
  regular publication version, and RSI + PB version remain unchanged.
- `cloud/telegram-asset-subscriptions.sql` adds operator-managed subscriptions
  keyed by chat and canonical asset ID. The worker has SELECT only. The additional
  copy requires a provisional pivot-close RSI payload, exact asset/timeframe,
  enabled subscription, and signal close at/after enrollment. Old records without
  an asset ID, other symbols, other strategies, monthly findings for this
  subscription, and pre-enrollment backlog are excluded. Re-enabling through the
  configuration script advances enrollment.
- Copies use the existing destination/event delivery ledger, 90-second leases,
  retries and sound slots; failures stay retryable. They have no General fallback.
  Private-chat Done/Undo belongs to the destination's Telegram user ID. These
  operator subscriptions are independent of personal category/silence preferences;
  disable them in their own subscription row. Dry-run and worker/effects ownership
  still gate external delivery. No manual test message is sent during activation.
- An explicit 5m subscription is the only exception to the existing broad 5m mute.
  Shared/group/category destinations remain suppressed. For 5m, the queue outcome
  still records broad suppression; actual direct-copy success is in
  `telegram_delivery` and the delivery activity log. Other timeframes keep normal
  shared delivery followed by the additional exact-asset copy.

`scripts/configure-near-alerts.py` previews by default; `--apply` uses the
**authoritative runtime database** via `TRADING_CONFIG_DATABASE_URL` (or the
existing `TRADING_DATABASE_URL`). Apply requires an operator connection permitted
to update config and subscriptions; it does not run the scanner. It selects the
existing exact `NEARUSD` / `NEAR/USD` asset, requires its confirmed Kraken feed,
and configures group **Chasing**, Bot API chat **-5412729128** for **5m, 15m, 30m, 1h, 4h, daily, weekly** RSI.
It preserves other assets and existing non-RSI strategy coverage using
`strategy_timeframes`; the asset frame list is the union needed by all strategies,
while the new subscription is limited to the seven requested frames. Existing
monthly RSI scanning/context and other destinations are preserved. The scanner
intersects strategy limits with enabled asset frames. There is no monthly RSI
copy to this new subscription.
The script rejects shared routes already pointing at this chat. Existing personal
references also fail closed unless `--replace-personal-route` is explicitly given;
that option removes only references to this exact group, preserving all other
recipients and destinations. It locks/checks the config revision, commits the
route replacement/config/subscription atomically and reads config/subscription back.
New frames establish their normal no-history-alert baseline. No alert is promised
when data is unavailable or no pattern meets the filters.

Read-only production inspection, 2026-11-20: runtime revision 47 was applied;
NEAR was enabled for monthly/weekly/daily/4h/1h/5m and used Kraken for all except
monthly. The authoritative runtime is PostgreSQL `trading_runtime` on Linux
loopback, with runtime/effects enabled. Read-only Kraken requests returned 720 closed candles for each requested frame
except weekly (231, still above the strategy minimum). Neither shared routing,
the fallback, nor personal registrations targeted 5412729128. The user supplied the Telegram export for private group Chasing (export ID
5412729128). Read-only `getChat` then verified Bot API ID **-5412729128**, title
**Chasing**, type **group**; the positive export ID is not a private-user chat.
`getMe` identified `@idodiTradingBot`. The verified group has one existing personal `chasing` route owned by Telegram
user 8016839817; the approved NEAR-only enrollment replaces that reference using
the explicit flag. Other routes are preserved. This subscription honors the
existing group-dismissal enable setting and uses group dismissal
controls and does not bind buttons to the positive export ID as a user identity.
The Supabase management SQL endpoint was unavailable, but is not the authoritative
configuration destination. These checks changed no production settings and sent
no messages or orders. Deploy the existing Linux scanner/service and the additive
SQL/config changes together; no Cloudflare UI publication is required. Hosted replay
source retains parity but is not proof of an active hosted runtime deployment.

Sources: `scanner.py`, `platform_app.py`, `signal_contract.py`, `signal_service.py`,
`rsi_context.py` (regular context input), `rsi_pb.py`, `managed_assets.py`,
`telegram_routing.py`, `telegram_asset_subscriptions.py`,
`cloud/telegram-asset-subscriptions.sql`, `scripts/configure-near-alerts.py`,
`cloud/functions/backtests/engine.mjs`, `cloud/functions/csv-backtests/rules.mjs`,
`skills/rsi-divergence/SKILL.md`.
>>>>>>> 74f1cc5 (Add hidden RSI divergences and exact NEAR Telegram subscriptions)

## Daily SMA crosses (Golden / Death cross)

- Strategy `ma-cross`: close-based SMA50 and SMA200, evaluated only on completed **daily** candles. Golden/bullish: previous SMA50 ≤ SMA200 and current SMA50 > SMA200. Death/bearish: previous SMA50 ≥ SMA200 and current SMA50 < SMA200. Equality on the current candle does not trigger. Invalid averages do not qualify.
- Require 201 valid closed candles to compare consecutive averages. No price-position, volume, or slope filter. A signal reports both averages, close, source and daily close time; it does not place an order.
- Enable once for all configured assets, including disabled assets without enabling the assets themselves; preserve subsequent strategy opt-outs. New and dynamically selected assets default to this strategy. All existing asset exclusions, market-hours/calendar rules, stock minimum price, provider validation and feed eligibility remain effective.
- Daily scanning is independent of the asset's other strategy timeframes: append a daily check when needed, running only `ma-cross` on that additional timeframe. Do not turn on other strategies for daily or run crosses on intraday/weekly/monthly bars. Missing daily feeds or fewer than 201 candles are reported as unavailable/insufficient history.
- The first eligible scan establishes a baseline without historical alerts. Subsequent cross events use the shared closed-candle strategy persistence and durable queue. Repeated scans/restarts do not enqueue an already recorded event. Catch-up findings are retained without individual Telegram notifications. Persistent below/above status is not a repeating alert; another qualifying crossing can alert.
- Queue prefix `ma-cross:`; independent `ma_cross_state` and `ma_cross_signals`. All markets use the single `other-signals` route (UI label **Other Signals**), separate from the `other.*` market categories. An empty/unavailable destination retries instead of falling back to General. Existing delivery deduplication, sound slots, retries and dismissal behavior apply.
- Personal copies require an explicitly registered `other-signals` personal route and honor that user's preference. Adding the shared destination does not automatically register personal destinations.
- `cloud/ma-cross.sql` upgrades hosted validation, route preferences, daily signal API, runtime tables and existing asset defaults. Apply before the matching worker/UI deployment. The installation's destination is held in routing settings, never hardcoded in application source.
- Local verification on 2026-11-06: Telegram `getChat` confirmed the exported **Other Signals** group identity, and a read-only check found no existing reference in the live shared routes and one personal-route reference. The prepared routing update clears that old personal assignment while retaining unrelated settings. After integrating the current production signal service, 130 targeted backend tests passed in disposable Postgres; all 41 UI tests and the static dashboard build passed. The complete backend suite passed: 545 tests, with one environment-dependent skip; this includes feed activation history requirements. No test messages were sent. Production verification on 2026-11-06: source commit `9483608df49bfd2e432707474ec1a80c05104f05` is running in the existing Linux service with one worker owner, healthy endpoints and matching UI/scanner fingerprints. Cloudflare production version `dcc5dea3-b219-4b59-8748-f5d58317f7c0` serves the exact matching UI. Both OS builds and frozen-app smoke tests passed (the Windows job passed on retry after a Node startup timeout). Shared routing revision 3 contains the dedicated Other Signals destination; the old `crypto_auto_chase` personal assignment is removed. All 34 currently enabled configured assets have `ma-cross`; 8 disabled assets remain disabled. The authenticated daily-signal API passed a read-only smoke check. The first natural scan established EURUSD’s daily baseline without replaying historical crosses; DAX/Colmex reported insufficient daily history (26 candles against 201 required). The initial scan cycle was still progressing, so this is not a claim that every configured feed has enough history or has completed its first baseline. No test alerts or orders were sent.

New crossover events also flow through the existing signal API, with SMA evidence; no execution behavior is added.

Sources: `ma_cross.py`, `scanner.py` (`scan_once`), `candle_strategy.py`, `managed_assets.py` (`ensure`, `migrate_ma_cross`), `market_feeds.py` (`verify`), `telegram_routing.py`, `telegram_personal.py`, `cloud/ma-cross.sql`, `scripts/configure-other-signals.py`, `web/app.js`.

## Routing and delivery

For existing market-specific strategies, `telegram_routing.select` chooses the market/strategy route with configured General fallback. Daily SMA crosses use the dedicated `other-signals` route and never fall back to General. It durably remembers destination choice per logical event. Unknown legacy market metadata is recovered using the existing stored evidence. `deliver_routed` can use General fallback for a destination-specific error; it does not reroute ordinary transient failures arbitrarily. Sources: `telegram_routing.py:37`, `:43`, `:48`, `:64`, `:92`.

Personal delivery uses registered recipients and existing opt-in category preferences/routes. Personal failures do not stop other recipients; per-destination dedup supports retry. Group dismissal and personal owner-bound dismissal retain their respective behavior. Sources: `telegram_personal.py:15`, `:22`, `telegram_checklist.py:12`, `:16`, `telegram_dismissal.py`.

The existing `delivery_worker` first claims pending notification work, then RSI findings, respecting retry deadlines and `migration_holds`. Claims use `FOR UPDATE SKIP LOCKED` with a 90-second reservation; failed RSI attempts retain sanitized errors and capped backoff. An empty queue waits one second. Dry-run work is recorded as dry-run and is not automatically resent on later enablement. The shared database effect gate and installation owner still protect external sends. Sources: `platform_app.py:327`, `telegram_delivery.py:36`, `postgres_store.assert_worker`, `local_settings.delivery_dry_run`.

Telegram delivery is not exactly once after an ambiguous network failure. Preserve sender identity, logical event IDs, delivery journal, message IDs, leases and pending holds during deployment. API cursor acknowledgments are unrelated to Telegram acknowledgment/dismissal. Do not bulk replay alerts to test a migration.

## Execution separation and Telegram handoff

The service no longer starts `paper_worker` or imports broker executors. Broker ownership, accounts, policy and execution data are in the separate `idodidodi/trading-executor` repository. All ordinary detection-alert paths described above remain in this service.

Bybit's opposing-divergence close notice still uses the exact `demo_exit` renderer in `chasing_runtime.text_for`, the `live_positions` opt-in route, the original per-trade/per-user identity and original event timestamp. It says a reduce-only close is queued and explicitly does not assert submission/fill confirmation. Execution now commits that fact to its own durable outbox before the broker write. The authenticated `/v1/execution-events` receiver validates it, deduplicates project/notice ID, and transactionally creates the existing signal-service chase session/outbox entries. Only subscriptions explicitly granted execution-notice permission can call it. Recipient lookup, rendering, retry, callback state and Telegram sends stay here. Sources: `signal_service.execution_notice`, `trading-executor:demo_execution.divergence_close`, `trading-executor:signal_client.Client.flush_notifications`.

An API outage can delay this broker-generated notice until the durable handoff succeeds. The runner retains a 15-second idle interval and flushes pending notices after execution processing. This additional network boundary means identical wall-clock latency cannot be guaranteed; event time/message/routing are preserved. Neither handoff nor a Telegram message proves a broker fill.

The sole scanner also publishes opposing RSI evidence for PB-only consumers from its existing candle data. This adds API evidence only, not a Telegram route or a second scanner. New signal-contract `updated`/`invalidated` pivot lifecycle events never enqueue, retract or resend Telegram alerts. Consumer registration/backfill/replay is independent of existing alert state.

## Verification

Regression coverage includes `test_telegram.py`, `test_telegram_routing.py`, `test_telegram_personal.py`, `test_telegram_delivery.py`, `test_telegram_checklist.py`, `test_telegram_dismissal.py`, `test_signal_alert_grouping.py`, `test_chasing_runtime.py`, `test_crypto_auto_chase.py`, `test_position_monitor.py`, and the execution-notice handoff test in `test_signal_service.py`. Tests use disposable storage and mocked Telegram/broker responses. No production test messages or orders are part of verification.

## Chasing suspension

The owner requested cancellation of all chasing and use of its Telegram chats.
`chasing_runtime.CHASING_SUSPENDED` disables manual and automatic enrollment even
when an old `DIVERGENCE_CHASING_ENABLED=true` setting remains present. Signal
messages no longer attach Chase controls. Previously sent start, lookback and entry
buttons acknowledge suspension without changing state; authenticated exit reporting
for an existing position remains available.

The existing worker retires owner-owned, unentered `chasing` and `complete_first`
sessions as `expired`, clears their next deadline, cancels pending lookback and
increments their revision. It suppresses queued watch events. Telegram delivery
also gates the `chasing` and `crypto_auto_chase` routes, including rows loaded before
cancellation. No new cancellation messages are sent to those chats. Messages already
accepted by Telegram cannot be recalled by this suspension.

Demo execution rejects new `chase-4h-5m` intents. Existing submitted/open trades
continue reconciliation and exit management; RSI + PB execution remains separate.
The later Linux signals-only boundary below disables manually reported position monitoring in this service.
This change does not close positions or send test orders. Original RSI, Pin Bars
and RSI + PB signal routing and qualification are unaffected.

The dashboard shows suspension notices and offers no chase activation or chat
registration controls. The original controls remain as explicitly archived
functions; the strategy engines, historical sessions and route registrations are
retained for future reference. No Telegram groups or messages are deleted.

Reactivation requires a reviewed change to the suspension constant, an explicit
review of preferences, chat destinations and execution policy, and regression
checks. Previously cancelled sessions and suppressed alerts must not be replayed.

Sources: `chasing_runtime.py` (`enabled`, `cancel_unentered`, `tick`, `callback`,
`deliver_pending`, `worker`), `telegram_personal.py` (`deliver_personal`),
`idodidodi/trading-executor:demo_execution.py` (`intent`), `web/app.js` (`chasingControls`,
`cryptoAutoChaseControls`, archived controls).

## Production verification

The source suspension is included in the deployed `d53453c` baseline. Following explicit owner approval,
production runtime cancellation was verified on 2026-11-06: all 140 unentered
chases were retired (156 expired including previous history), zero active or
completed-unentered chases remained, manual and automatic chase preferences were
off, and the demo entry policy retained only RSI + PB. The 317 sent messages and
historical groups were retained. Live-position notification preferences remained
on; there were no open demo trade records at verification. No test alerts/orders
were sent and the existing worker was not stopped for this cancellation.

## Done/Undo dismissal and recovery


Personal alerts and shared alerts with group dismissal enabled use a server-side three-second grace period. Leaving a Telegram chat/folder does not cancel it. Pending deadlines survive worker restarts; dry-run pauses external effects. Personal callbacks require the enrolled user; shared-group callbacks require the matching message/chat.

After the grace period, Undo only acknowledges that the window ended; the listener handles deletion separately. Telegram's explicit `message can't be deleted` rejection (including its 48-hour deletion restriction) replaces the countdown with a completed/manual-delete notice and removes Undo. Already-missing messages count as deleted. Other API failures, including a failed fallback edit, keep the item pending with a persisted 30-second retry deadline. One failed item does not stop other pending deletions or callback polling. Database/ownership failures still propagate. Recovery applies to existing persisted pending messages when the updated worker runs; messages without recoverable database state are outside this repair. Production recovery has not been verified.

Sources: `telegram_dismissal.py`, `telegram_personal.py` (`process`, `next_deadline`), `telegram_checklist.py` (`api`, `_worker`); [Telegram deletion limits](https://core.telegram.org/bots/api#deletemessage).

## Colmex scanner integration

Published Colmex integration (deployed at revision `2e9c5aa`; the combined separation release still requires its own verification): `colmex_feed.py` connects the
scanner to the separate `colmex-mt4` package (`mt4/reader.py`). Colmex is a dedicated
primary provider with exact broker symbol identity, including `#`, and exchange
`Colmex-MT4`; no cross-provider fallback or assumed TradingView match is allowed.
The operator's 2026-11-06 UTC confirmation is recorded in `mt4/timezone-policy.json`.
Each read checks terminal/catalogue freshness, connection/configuration state, active
symbol and subscription revision, valid completed M1 OHLC, and live quote time.
Scanner integration supports 1h/4h/daily UTC aggregation. H4 starts at 00/04/08/12/16/20 UTC.
Only complete buckets in the most recent contiguous suffix are eligible. Missing
minutes or whole buckets reset that suffix; no market-break calendar is inferred.
The existing strategy minimum-history and first-scan baseline rules still apply.
Current SPX history only produced two eligible H4 bars in the read-only smoke test;
this does not meet strategy history requirements. Unknown gaps remain a readiness
blocker. Open-session unavailable Colmex reads retry in 30 seconds; the market-session gate below skips verified closed sessions and moves deadlines to reopening.

`cloud/colmex.sql` exposes authenticated `/api/colmex` requests to installation
members (including viewers), with owner identity taken from auth.uid(), per-user
revision checks, 10 requested exact broker-classified catalogue forex pairs per user, and a 50-symbol
shared collection capacity. Removal works offline. No arbitrary owner IDs or paths
are accepted. `colmex_runtime.py` reconciles active members' requests plus enabled
configured assets under one exporter owner named `scanner`, preserving the existing
portfolio and other exporter owners. A separate bridge thread in the existing worker
refreshes requests/status every 30 seconds, independent of long scans; it emits no
signals or alerts. The scanner takes the reconciled request set at each scan start.
Revoked members cease contributing demand;
an explicitly disabled configured Colmex symbol takes precedence over requests.
Requested pairs are scanned as shared 4H forex assets using RSI, Pin Bars and RSI+PB.
Existing alert routing and personal category preferences apply: requesting a pair
is not Telegram enrollment and does not make its alerts exclusive to the requester.
Removing a request stops its contribution, not other users' or portfolio demand.
No Telegram chat command is introduced; subscribers use the authenticated Assets UI.

Verified locally: exporter live reload; reader/time-mapping/gap tests; authenticated
Postgres ownership, revision, offline removal and capacity tests; synthetic scanner
baseline and signal persistence; UI request controls. Tests use disposable data,
not production signals or Telegram delivery. Deployment requires the SQL migration,
package and worker configuration, and the hosted UI together. No production asset
selection or alert route has been switched by this implementation.


## Linux signals-only boundary (2026-11-06 correction)

Position management belongs to the separate trading runtime. The Linux signal
entrypoint no longer starts `paper_trading.worker` (including Bybit demo execution,
reconciliation and exits) or `chasing_runtime.worker` (including position
monitoring and position notices). Their shutdown joins are removed too. The
implementation remains available for reference and a separate trading service.
Signal scanning, ordinary signal Telegram delivery, dismissal handling, market
summaries, backtests and the Colmex feed bridge remain in the signal service.

This supersedes the earlier statement that Linux should retain position management
and RSI + PB *execution*. RSI + PB signal detection and its signal alerts continue;
its demo order execution does not. On 2026-11-06, production demo execution was
disabled and position notification preferences were turned off. Manual and
automatic chasing remain off; there were no open demo trades or entered chase
positions. Alpaca paper execution was already disabled in the worker environment.
No orders were placed or closed by this correction. The boundary was published in `d53453c`; this combined separation release requires its own deployment verification.

Source: `platform_app.py` (`main` startup/shutdown), `idodidodi/trading-executor:paper_trading.py` (`worker`),
`idodidodi/trading-executor:demo_execution.py` (`_cycle`), `chasing_runtime.py` (`worker`). Do not restore
trading worker startup here when reactivating a separate trading service.

## Colmex native backfill

When `COLMEX_HISTORY_DIR` is configured, the three reviewed index symbols use
operator-imported, hash-checked Colmex H1/M5 archives plus current native H1/M5
and M1 exports. Archive imports discard the forming final row. Broker candles
retain their original timestamps; a shifted GER40 H1 candle is never relabelled
or split to fit a UTC boundary. Whole source intervals tile each UTC bucket.
Fresh native source revisions replace equal historical intervals. Native H1 is
authoritative when available; M5/M1 fill only intervals they actually cover.

Quote sessions were read from the live TGLColmex terminal on 2026-11-06: SPX
Mon–Thu 00:00–21:15, 21:30–22:00, 23:05–24:00 UTC, Friday without the final session;
GER40 weekdays 01:10–21:00 UTC; USDINDEX weekdays 01:00–22:00 UTC. Seasonal rules
use New York for SPX/USDINDEX and Berlin for GER40, corroborated by the native
archive across October/November transitions. These calendars distinguish regular
closed time only; holidays, outages and absent trading-session bars are not filled.
A missing open interval resets eligible history. Fully closed buckets are skipped;
partly open buckets retain exact UTC boundaries and only actual quoted OHLC.

Live identity, revision, freshness and UTC-policy checks still apply. New/unreviewed
symbols retain the strict M1 path. Existing minimum-history, first-scan baseline and
alert routing rules remain. Importing archives does not by itself select a provider,
activate an asset or emit historical/test alerts.

## Separation reconciliation

The Linux signal process does not start chase observation or position monitoring. Chasing stays suspended; production position-notice preferences remain off. Execution-fact API receipts retain the existing durable renderer and opt-in gates; only authenticated external executor facts may enter that delivery path. No position prices or broker states are polled by that path. Colmex native history backfill is governed by the separate rules above.

`signal_notice_delivery.worker` only delivers queued external `demo_exit` facts whose stored session is an external demo-exit receipt. It never invokes `chasing_runtime.tick` or its worker. Dry-run and missing-token settings disable this delivery worker, and existing recipient opt-ins and delivery ownership checks still apply. It does not drain legacy position-watch events.

## Discovery boundary — reviewed 2026-11-07

The optional authenticated discovery API (`signal_discovery.py`, routes in `signal_service.handler`) reads published signal events, scanner coverage, configuration and reviewed calendars. It never advances delivery cursors, changes scanner eligibility/baselines, invokes scanners, enqueues Telegram notifications or authorizes broker execution. Discovery session/freshness exclusions apply only to discovery results; they do not alter existing signal qualification or Telegram routing. Calendar/grant provisioning is manual and default-denied. Deploying discovery does not activate a scanner, change notification preferences or send test alerts.

NEAR/USD five-minute RSI subscriber events may include informational higher-timeframe cache context (see `SIGNAL_API.md`). This does not alter Telegram destinations; five-minute Telegram alerts are excluded as described below, independently of subscriber publication. Baseline cache fills and cache refreshes produce no alerts themselves. Enabling 5m in managed assets enables ordinary strategy processing; it is an explicit deployment step, not performed by a cache read.

### Personal notification preferences

`web/app.js` (`personalTelegramControls`) offers **Deliver silently** for each subscribed route/category and one personal **Quiet hours** control with enable, start, end and a read-only local timezone detected from the browser. Both the adjacent save button inside Quiet hours and the bottom save button save the same complete preferences; both show saving/errors/success and prevent overlapping saves. Saving persists the displayed browser timezone, including when the user saves from a different timezone; merely opening Settings does not change stored preferences. This matches existing subscription granularity (for example Crypto · RSI), not individual findings or tickers. Unsubscribed categories disable the silence checkbox but retain its saved value. Messages continue to arrive during quiet hours.

`cloud/telegram-notification-sound.sql` stores `silent` and `quiet_hours` in `public.telegram_alert_preferences`, keyed by installation and authenticated user. The API validates category booleans, HH:MM times and timezone names, rejects equal start/end when enabled, and uses the existing revision check and per-user transaction lock. Older clients that omit the new fields preserve saved sound settings. Defaults are no per-category silence and disabled quiet hours (22:00–07:00, UTC). Viewers may change only their own settings; the worker reads them through `bot_runtime.personal_recipients()`.

`telegram_personal.py` (`personal_silent`, `deliver_personal`) evaluates wall-clock time at each personal delivery attempt using the configured IANA timezone, including daylight-saving changes. Start is inclusive and end is exclusive; start later than end crosses midnight. Repeated DST hours use the same local-time rule. Final silence is incoming silence OR per-route silence OR active personal quiet hours. Missing settings retain existing behavior; invalid manually written enabled quiet hours fail silent rather than block delivery. Retry attempts reevaluate the current preferences/time; existing eligibility, suppression and destination deduplication are unchanged. These controls affect personal copies only, not shared groups or another recipient's copies.

`telegram_delivery.py` (`deliver`) passes silence to the sender and does not consume the per-destination sound slot for an already-silent message. `platform_app.py` (`send_telegram`) maps it to Telegram's `disable_notification`; no message is intentionally delayed or omitted by a sound preference. Telegram/client mute settings can still suppress sound when these preferences permit it.

Deployment prerequisite: apply `cloud/telegram-notification-sound.sql` after the existing dashboard/personal migrations and before publishing the UI/worker. It wraps the installed API without replacing other handlers and can be rerun. Local verification uses a disposable loopback Postgres database and mocked Telegram transport. Production activation requires verification of the migrated settings, source fingerprints and single worker owner for the deployed commit; local tests alone do not establish production state. The isolated preview uses the actual UI component with browser-local demo preferences.

## Five-minute Telegram exclusion — reviewed 2026-11-07

Five-minute (`5m`) signal alerts remain suppressed for shared routes, General fallback, personal category copies and queued retries. The sole new exception is an enabled exact asset/timeframe RSI subscription described above; it sends only to its enrolled destination before the broad-route suppression returns. RSI uses its structured payload; EMA/BB Pin Bars, RSI + PB and SMA-cross notifications resolve their persisted signal payload. Missing queued strategy records delay delivery instead of sending without a timeframe check. Suppression is recorded by the existing worker queue status handling. Previously delivered messages are not removed. Reports, summaries and connection tests retain their existing behavior.

Market scanning, five-minute candle processing, stored findings, higher-timeframe context and signal subscriber publication remain unchanged. Existing 1h/4h/daily/weekly/monthly Telegram behavior is preserved; this is not a new 1h/4h-only policy. Commit `50633b8` enabled NEAR 5m processing without adding an independent Telegram filter; its parent already routed all qualifying timeframes. Read-only production inspection on 2026-11-07 found 11 NEAR 5m deliveries to the configured Crypto RSI destination over the preceding seven days, all on November 7, and no other 5m crypto deliveries. This correction is locally verified only until separately deployed.

Sources: `telegram_routing.py` (`signal_timeframe`, `deliver_routed`), `platform_app.py` (`delivery_worker`), `test_telegram_routing.py`.

## Optional Linux working cache

`local_cache.py`, `managed_assets.cache_candles`, and `tiingo_feed.request_json`
add an opt-in local working cache. Live detection still uses the provider candle
sequence and the existing closed-candle/freshness validation. Tiingo response
freshness stays five minutes, checked against Postgres time; future timestamps
are rejected. Shared request budgets, worker ownership, baselines, duplicate
identities, signal queues and external-effects gates remain in Postgres. Local
cache failures surface as errors; they do not authorize offline delivery. Candle
publication must succeed before the scan proceeds, as before. No alert threshold,
routing or timing rule is changed. This describes tested local code, not verified
production activation. See `docs/local-scanner-cache.md` for storage operations.

## Preserved production listener recovery

Reviewed 13 November 2026 against the existing Linux source before the local-cache
rollout. `telegram_checklist.api` normalizes direct socket resets, HTTP protocol
errors, incomplete response reads and invalid JSON into sanitized `RuntimeError`
messages. The existing listener retry loop can then resume polling; no callback,
delivery, routing or deduplication rule changes. This records and preserves the
already-deployed production fix that was missing from the recorded Git revision.
Regression coverage: `test_telegram_listener_reconnect.py` (transport/body errors
and retry-loop continuation).


## Market-session request eligibility (reviewed 2026-11-14)

Sources: `market_sessions.py` (`state`, `closed_row`, `classify_unavailable`),
`scanner.py` (`scan_once`), `scan_schedule.py` (`identity`, `next_due`, `provider_lag`),
`colmex_feed.py` (`fetch_colmex`). This section describes source behavior;
production verification requires matching the deployed commit and manifest.

The scanner resolves each timeframe's provider before choosing its calendar. Live
cycles recheck wall time for each asset so a long scan cannot reuse an earlier
open-session decision; logs retain `request_at` separately from the cycle watermark. A
verified closed session skips the provider read, cached scan result, strategy
calculation and signal insertion, including catch-up and the independent daily
MA scan. Coverage says `market closed`, with `next_open_at` and a UTC reopening
explanation. Schedule identity includes the calendar version and market; stale
saved errors cannot override the gate. Both success and retry deadlines move out
of closed sessions. At reopening normal reads resume immediately; unavailable
Colmex data retains its 30-second retry, with no indefinite startup grace or
relaxation of quote freshness. Direct Colmex adapter calls also enforce this gate.
The closing UTC candle can be processed at the next opening; no OHLC is fabricated
and strategy baselines/deduplication are not reset. The existing reader and native
history aggregation are unchanged, so insufficient/incomplete history remains a
separate blocker (especially USOIL's strict M1 path).

Exact `colmex` / `Colmex-MT4` mappings for the reviewed TGLColmex installation:

| Symbol | Calendar timezone | Regular local sessions |
| --- | --- | --- |
| `GER40#` | Europe/Berlin | Monday–Friday 02:10–22:00 |
| `SPX500#` | America/New_York | Sunday from 19:00; Monday–Thursday 00:00–16:15, 16:30–17:00, 18:05–24:00; Friday 00:00–16:15, 16:30–17:00 |
| `USDINDEX#` | America/New_York | Sunday from 20:00; Monday–Thursday 00:00–17:00 and 20:00–24:00; Friday 00:00–17:00 |
| `USOIL#` | Europe/Berlin | Monday–Thursday 02:00–24:00; Friday 02:00–22:00 |

The three index sessions use the terminal specifications recorded on November 6
and native-history seasonal evidence recorded in the exporter repository's
`docs/STATUS.md`. Read-only November 14 snapshots corroborated the repeated
November 9–13 breaks and last Friday quotes: GER40 20:59:59 UTC, SPX and USDINDEX
21:59:59 UTC, USOIL 20:59:58 UTC. Exporter 2.20 was connected with fresh snapshots
and no configuration error. USOIL's regular hours are corroborated by the
[Colmex Pro specification](https://www.colmexpro.com/wp-content/uploads/2023/12/Contract-Specifications-Colmex-Pro-Ltd-Dec-2023-CFDs.pdf)
and the observed week. USOIL's seasonal interpretation is not established: its
request calendar is limited to November 6, 2026 through March 28, 2027 01:00 UTC,
and becomes `unknown` when the next opening crosses that boundary. Reverify
before the next European DST change. That older publication is from a different Colmex entity;
it conflicts with current terminal SPX/USDINDEX breaks and is not used to override
them. A schedule match alone never establishes that a historical outage was healthy.

Dated exceptions come from [Colmex Israel's November 2026 client notice](https://www.colmex.co.il/wp-content/uploads/2026/11/Update_November_2026_Clients.pdf),
whose table uses Israel time. On November 26, SPX closes 20:00 Israel and has an
extra November 27 01:00–02:00 window; Friday closes 20:15. USDINDEX closes 20:15
both days. USOIL closes 20:30 Thursday and 22:00 Friday. GER40 remains normal.
The implementation converts these to exact UTC-date windows, including the SPX
midnight crossing. No other broker holiday is invented. New monthly notices or
unscheduled closures require review and a calendar-version update; unknown
holiday outages remain failures rather than being silently excused.

Existing forex classification retains the Sunday 17:00–Friday 17:00 New York
week; broker-specific intraday/holiday overrides for requested pairs are not
verified. US equities on Alpaca/NYSE/NASDAQ/AMEX/NYSE ARCA use the existing
`market_calendar.py` holiday/early-close rules and regular sessions, corroborated
against [NYSE's official calendar](https://www.nyse.com/trade/hours-calendars).
The existing 20-minute closing-data allowance is labelled separately and permits
final delayed bars, not extended-hours trading. Crypto remains 24/7. CSV history
is not a live request. Other provider/symbol mappings (including Tiingo gold and
non-US equities) remain explicitly `unknown`, with normal freshness validation
and retry behavior; they never inherit an index or US-equity calendar by name.
Independent stock reports/universe history jobs and MT4's subscribed recorder
are not paused by this scanner gate. Terminal heartbeat and subscription
reconciliation continue during closures, and bridge outages remain independently
visible in Colmex status. No exporter binary or package change is required.

Unavailable checks record calendar version, session state and failure classification
alongside the original reason. `scripts/audit-market-sessions.py` classifies exported
Market check JSON offline without database/provider access. It distinguishes a
closure-compatible stale quote from an open-session failure and from a terminal
or history failure occurring during a closure. It does not automatically convert
historical errors into successes. Read-only review of 10,123 retained checks from
November 7 06:30 to November 14 06:29 UTC found 7,963 closure-compatible stale
quotes, 1,930 open-session data failures and 11 other failures during closures.
These are repeated asset/timeframe checks, not counts of distinct outages. The
open failures include USOIL incomplete buckets and real intraday quote gaps;
those are deliberately not suppressed. Raw production logs remain outside Git.

Local verification: 149 targeted Python tests passed against a dedicated disposable
Postgres database, covering boundaries, DST, holidays, closed-session request blocking,
reopening, preserved open-session errors, provider routing and stock strategy fixtures.
Offline replay maps all nine current Colmex asset/timeframe failures to closed
sessions with distinct next-open times. This is not production deployment verification.

## Optional PostgreSQL working data (15 November 2026)

`working_postgres.py` and `local_cache.py` add a loopback-only PostgreSQL backend for provider-response caching, candle copies and publication acknowledgements. Existing cache TTL and 24-hour republishing remain. Configured local failures propagate; there is no silent hosted-history fallback. Local CSV replay reads the local store. Shared worker fencing, provider quota reservations, command ownership, finding identities, callbacks, delivery offsets and deduplication remain in Supabase. No new live alert is generated by seeding or replay. This describes prepared local code, not a verified production configuration.

### Preserved deployed Assets controls — 2026-11-24

The ATR settings release includes the previously deployed UI-only change
`44fae6c`: asset-save feedback is inline and unsupported Colmex timeframes are
validated in the Assets editor. This preserves existing user controls while adding
the NEAR ATR explanation. It does not change asset selections or scanner logic.
