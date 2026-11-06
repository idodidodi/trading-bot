# Telegram position monitoring before broker integration

Implemented on `codex/chasing-5m-and-up`; not deployed or activated. This is manual
position reporting and exit alerts, not an execution engine.

## Cadence and entry reference

- Eligible personal provisional RSI originals are 1h and 4h, using the exact Kraken spot
  feed. Before reported entry, chase every five minutes using 5m and higher divergences,
  capped at the original timeframe. A first finding still ends the chase search.
- “I'm in” records reported entry and sends a Live positions start card. The existing
  sole Telegram listener handles the callback. The worker starts checking immediately,
  then once per minute, awaiting a fresh completed minute at/after the click.
- The first such minute close becomes the displayed **monitoring reference**, with its
  timestamp. It is not a broker fill or a claim about the user's actual execution price.
  Thresholds use this reference until a future entry-price workflow/platform supplies fills.
- Each subsequent fresh closed-minute price evaluates exits. This is sampled monitoring;
  intraminute touches/extremes and price paths during gaps are not inferred. It cannot
  guarantee detection or an exit at a broker stop price.

Thresholds use the original signal timeframe, even if the chase finding was on 5m.
If a chase divergence was already found before “I'm in,” it already qualifies as the
additional divergence; the monitor does not wait for an unnecessary third finding.
Otherwise the first fresh same-direction 5m-or-higher finding after entry extends the
policy. One-minute divergence triggers remain excluded.

## Exit policy

Without another divergence, alert at 0.5% loss or profit for a 1h original, or 1% loss
or profit for 4h. With another same-direction divergence, replace the fixed profit
exit with a 0.5% trail activated at +1% favorable movement from the monitoring reference.
For 4h, reaching +2% widens the trail to 1%, and that stage remains latched after a
retracement. The new wider level may move farther from market at that transition.
The original loss stop applies only before trailing activation. After activation the
trailing level is the only stop. Trails track the highest observed close for longs and
lowest for shorts. Thresholds are inclusive and percentages are evaluated with Decimal.

On a minute where an existing exit and a newly available divergence/width transition
coincide, the existing exit takes priority. This is an explicit close-sampled convention,
not a claim about intraminute event order. Repeated divergences do not reset the trail.

When an exit triggers, send one **EXIT ALERT** in Live positions with the reason,
observed price and move from the reference. The bot sends no order and does not mark
the position closed. Continue minute price checks, without repeated exit alerts, until
the owner clicks **“I've exited.”** That acknowledgement stops monitoring. Done/Undo
remains message dismissal only. Buttons remain bound to bot, group, message and owner.
Pending obsolete position alerts are suppressed after reported exit.

An opposing RSI divergence after reported entry triggers an EXIT ALERT only on the
original divergence timeframe (1h requires 1h; 4h requires 4h), regardless of the
chase entry timeframe. Another asset, same-direction, pre-entry or future finding
does not trigger this exit. Existing price exits take priority on the same observation.
The divergence is retained through a minute-price outage, and triggers the position
exit state when a fresh price returns. Monitoring continues until reported exit.

Chasing and RSI + 5Min RSI messages include a delivery-time current price: the latest
completed one-minute Kraken close, explicitly timestamped in UTC. Observations older
than two minutes, future bars and provider failures display price unavailable; the
original divergence close is never used as a current-price fallback. The shared
provider cache also serves these observations.

## Provider budget and failure handling

Kraken states public Spot requests at one per second or less remain within its limits:
[official rate-limit guidance](https://support.kraken.com/articles/206548367-what-are-the-api-rate-limits-).
The shared Kraken adapter spaces request starts by at least one second across this
process's scanner and monitor threads, and applies a fail-fast 60-second cooldown after
rate-limit responses. The monitor shares cached higher-frame bars until their next
native close and refreshes minute data for position checks. Delayed/empty higher-frame
responses are not frozen in that cache. No credentials or second scanner are introduced.

Provider failures or stale minute data suspend price-rule evaluation and produce one
Live positions warning per episode. Fresh data generates a recovery notice. Freshness
is evaluated after fetch completion as well, so slow requests do not pass using the
old scheduling timestamp. Cached bars are shared between sessions, while price/rule
state and notifications remain private per owner/session.

Cadence is nominal, not a hard one-minute SLA: network latency, provider cooldowns,
load and other applications using the same IP can delay observations. The warning tells
the owner to monitor manually during unavailable data. Live delivery remains subject
to existing worker ownership, dry-run/effects guards and recipient preferences.

## Research and deployment boundaries

The earlier annual 1m-versus-5m comparison remains an immutable snapshot from commit
`e951063`, using its then-current minute chase-check convention. This later cadence and
position policy was not applied retroactively to those published research artifacts.
The research replay now respects session due times for future runs. These unit/integration
checks do not establish strategy profitability or replace a new historical exit-policy study.

No schema migration is required for these JSON state additions. The 1m baseline branch
and production/main remain unchanged. Activation still requires publishing/reviewing this
branch and updating the existing Linux worker; the last verified SSH attempt timed out.
Do not start another production scanner or send demo alerts to work around that blocker.
