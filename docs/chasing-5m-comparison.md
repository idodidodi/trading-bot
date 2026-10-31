# Five-minute-and-up chasing variation

The prior implementation is preserved at `codex/chasing-1m-baseline`, commit
`0ac6198814b46ccc98ce522092c7eacfca20b28c`. The current variation is on
`codex/chasing-5m-and-up`. This iteration changes neither main nor production.

Chasing divergence triggers now use **5m → 15m → 30m → 1h → 4h**, checking for
newly completed eligible candles each minute. The first same-direction finding while
price is adverse to the original 4h close still stops chasing. One-minute prices remain
necessary for price-path measurement, the configurable favorable stop, return notices,
and hypothetical fills; one-minute RSI divergences are excluded from both strategy
alerts and observation-only follow-ups. Normal full-universe scanner timeframes are
unchanged. Runtime no longer computes one-minute chase divergence indicators.

The lifecycle version changes to `chasing-v2-5m`, separating newly created session
identities from the preserved baseline. Any future live activation must account for
existing sessions rather than treating this research branch as a completed rollout.

## Same-data annual comparison

Original signals: 2025-10-31 00:00 UTC inclusive through 2026-10-31 00:00 UTC exclusive.
Both variants use exactly the same six native Kraken NEAR/USD candle files and all 266
original signals. Stop = 0.5%; search = 12h; post-follow-up price-path observation =
12h. Hypothetical returns use the original alert's common 12h exit and 10 bps total
assumed costs. Empty minutes remain labeled and cannot receive entry or exit fills.

| Metric | With 1m | 5m and up |
|---|---:|---:|
| Original 4h divergences | 266 | 266 |
| Strategy follow-up alerts | 147 | 119 |
| Stopped on favorable move first | 119 | 147 |
| Median wait to follow-up | 13 minutes | 30 minutes |
| Net-positive follow-up entries | 54.422% | 54.622% |
| Mean net return per follow-up entry | -0.008% | +0.035% |
| Mean net return per original, unentered cash = 0 | -0.005% | +0.016% |
| Mean hypothetical entry adverse excursion | 3.098% | 3.045% |
| Further 2% adverse moves after follow-up | 67 / 147 (45.578%) | 59 / 119 (49.580%) |
| Worst post-follow-up adverse excursion | 20.784% | 20.133% |

All post-follow-up windows are complete. The 119 new follow-up roots are a subset of
the original 147: 28 former alerts now reach the favorable stop before any eligible
finding. Among the 119 retained roots, 89 alerts occur later and 30 in the same minute.
95 former 1m findings are replaced by a 5m-or-higher finding. New alert frames:
82 on 5m, 23 on 15m, 8 on 30m and 6 on 1h.

On the **same 119 roots with entries in both variants**, mean net return changes from
**-0.102% to +0.035%**, and mean hypothetical entry adverse excursion changes from
**3.202% to 3.045%**. This controls for the changed executed population, but still does
not establish predictive profitability: the year was already examined, the mean edge
is small under assumed costs, signals overlap, and no exit/stop filter was optimized or
validated on unseen data. The further-2% adverse share increased despite its lower count.
Original-entry volume results remain identical because those signals and outcomes did
not change. A 2% flag is research metadata, not an implemented broker stop.

## Reproduce without provider requests

Keep the original dataset and report directory unchanged. Run:

```sh
.venv/bin/python annual_near_backtest.py \
  --cache data/annual-near-cache \
  --candles data/near-one-year/candles \
  --output data/near-one-year-5m-and-up

.venv/bin/python compare_chase_variants.py \
  --baseline data/near-one-year \
  --candidate data/near-one-year-5m-and-up \
  --output data/near-one-year-5m-comparison
```

The new directory contains the same case-by-case Markdown, CSV and JSON metrics,
recovery/extreme times, failure flags and volume research as the original study.
The comparison adds a Markdown summary, a row for every original in `comparison.csv`,
and input hashes/paired summaries in `comparison.json`. It rejects mismatched candle
hashes, roots, original-entry outcomes, costs, rules, windows or replay hashes.

Verification: 30 focused state-machine, market-adapter, research and comparison tests
pass, including ignored 1m triggers with minute-price stops still active, exclusion of
1m runtime indicator detection, preservation of actual new 4h follow-ups, and rejection
of unequal comparison inputs. Database-backed runtime tests were not rerun for this
variation; no schema or database behavior changed.
