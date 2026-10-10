# US-B CRYPTO Price Structure R0, Results V1

Contract: `docs/crypto/price_structure_r0/CONTRACT_V2.md`
sha256 `d41fb4f5cd6d28f48a309c3122b9eb5afc1bb45a0f430b009eacb72ea2c0bb22`, frozen before any metric
below existed. It supersedes `CONTRACT_V1.md`
(`ecbdde684be6e92c7d463872a4f2dcba69ee9b492cecd239869049e055a6289a`), amended while only the dataset,
the ATR series and the D2 crosscheck had been computed. Both freeze records are in
`data/research/crypto/price_structure_r0/contract_freeze_v{1,2}.json`.

Nothing was deployed. C1 / C1x, AUTO, the order paths, PAPER and LIVE are untouched.

## 1. Verdict

**A. NOT_USABLE**, by the rule frozen in contract section 14.

The trigger is `M2 median active levels = 21`, against the bound `> 15`. The verdict is not resting on
that one number: the layer also misses the `B` bar on two further independent axes, `M4 = 0.444`
against `>= 0.60` and `M6 median 18 confirmed breakouts per day` against `<= 12`. Even with M2
removed entirely the outcome would be `C`, never `B`.

What failed is **level selection and event rate**. What did not fail is the part the study existed to
test first:

> **M1 repaint violations = 0 over 47,521 snapshots and 1,905 audited objects, and the engine fed
> only the bars that closed at or before T reproduces the full run's state at T field for field.**

The mechanism is causally sound. The curation on top of it is not.

## 2. Data

| | |
|---|---|
| Source | Bybit v5 public `/market/kline`, `category=linear`, `BTCUSDT`, `interval=1`, read only |
| Collected | 2026-08-12T00:00Z to 2026-10-04T00:00Z, 76,320 bars, no gaps |
| sha256 | `516e980db0d21716c1cc9cda47572a20d1498eebe1d439bed05e44218ee63cb2` |
| Warmup | 2026-08-12 to 2026-09-01, publishes nothing, used for the section 16 calibration |
| Replay | 2026-09-01T00:00Z to 2026-10-04T00:00Z, 47,521 published snapshots on the 1m grid |
| D2 crosscheck | **60,574 overlapping minutes, 0 mismatches** on open, high, low, close, volume, turnover |

The overlap with the frozen D2 history agrees to the last decimal on every field, so the fresh fetch
is the same series the rest of the crypto research runs on.

## 3. Calibration, held out

`theta` was set on the warmup window only, from the fixed ladder, against densities stated before
measuring. Full table in `calibration_v1.json`.

| Frame | Target | Chosen | Density achieved | Met |
|---|---|---|---|---|
| 1h | <= 2.0 swings/day | **3.0** | 1.00 /day | yes |
| 5m | <= 8.0 swings/day | **6.0** | 10.75 /day | **no, ladder exhausted** |

The 5m frame could not be made sparse enough by any rung on the ladder. Contract section 16 says to
take the largest rung and report the shortfall, which is what happened. **This is the first visible
sign of the result**: on the 5m frame, "a retracement large enough to call a swing" and "few enough
swings to call them levels" do not both hold at any single threshold. The 30 percent overshoot here
becomes the level inflation in section 5.

## 4. What held: causality and stability

**M1, repaint. 0 violations, 0 unlogged disappearances.** Immutable fields were a closed, declared
list; every published object was compared against its first publication on every snapshot, plus a
full final sweep over all 242 levels, 811 breakouts, 440 sweeps and 412 swings.

**The prefix truncation test is the strong form of that claim.** At six cut points the engine was
given only the bars that had closed at or before T and run to completion. Its published snapshot at T
was compared with the full run's snapshot at T across all 21 fields, including the entire published
level list:

| T | identical | active levels | published |
|---|---|---|---|
| 2026-09-04T00Z | yes | 22 | 6 |
| 2026-09-11T00Z | yes | 25 | 5 |
| 2026-09-16T00Z | yes | 20 | 7 |
| 2026-09-25T00Z | yes | 15 | 10 |
| 2026-09-30T00Z | yes | 26 | 10 |
| 2026-10-03T22Z | yes | 23 | 10 |

A layer that peeked at the future, or that revised a pivot once more bars arrived, fails this test
immediately. It passes at every cut point. Two independent full runs also produce byte identical
metrics, so the result is deterministic.

**M5, liveness: 801 of 801** retest machines whose window had elapsed were in a terminal state.
**M3, publication cap:** maximum published level count 10, never exceeded, median 9.

Spot re-derivation straight from the raw bars, independent of the engine: 60 of 60 swings, 60 of 60
breakouts and 60 of 60 sweeps reproduce exactly, and the session VWAP matches an independent
`sum(turnover)/sum(volume)` to the cent.

## 5. What failed

### M2, the level book inflates. Median 21 active, p95 27, max 29, bound 15.

242 levels created in 33 days from 412 swing candidates, with 144 merged away and 26 suppressed. The
book carries far more than a person would keep on a chart, and two structural reasons are visible:

**5.1 The cross timeframe rule is one directional, so levels duplicate.** Contract section 8 drops a
5m candidate that lands near an active 1h level, but says nothing about the reverse. A 5m swing
confirms long before the 1h swing that shares its extreme, because a 1h bar's high **is** the maximum
of its twelve 5m highs. The 5m level is therefore created first and the later 1h candidate sits
straight on top of it.

Measured: **58.9% of hourly published sets contain at least one near duplicate pair** within
`0.25 * ATR_5m`. Of 586 such pairs, **364 are a 1h level stacked on a 5m level**. The readout in the
preview shows it plainly: `published: ... 85,696.2, 85,696.2`. Two of the ten publication slots spent
on one price.

**5.2 The 5m frame cannot be made sparse, as the calibration already warned.** 10.75 swings per day
with a 48 hour lifetime is roughly 21 concurrent 5m levels before any merging, which is the whole
budget on its own.

### M4, major turning points are missed more often than caught. 12 of 27, bound 0.60.

Of the 15 misses, **only 2 were at a price the market had never visited**, where no prior level could
exist by construction. The other 13 turned at prices inside already traded territory with no
published level within 0.25 percent. Coverage restricted to reversals that were not new extremes is
0.480, so the metric is not mainly an artifact of unvisited price.

Misses skew to the larger moves: hits average a 3.0% swing, misses 4.8%. Two misses were only 220 USD
away against a 212 USD tolerance.

A diagnostic computed after the verdict was fixed, which does **not** feed back into it: coverage
using only the published **1h** levels is 7 of 27, worse than the 12 of 27 of the full set. The 5m
levels are producing the hits, not crowding them out, so trimming the 5m frame alone would make M4
worse, not better. Any revision has to add level sources, not only subtract.

### M6, the event rate is too high to read. Median 18 confirmed breakouts per day, max 47, bound 12.

580 confirmed breakouts in 33 days, **538 of them against 5m levels**, 42 against 1h levels, 2 against
range edges. 313 sweeps. The screenshot `preview/preview_allon.png` is the same fact as a picture: with
every layer on, the markers bury the candles.

The knock on effect lands on the retest machine. Contract section 10 keeps exactly one machine alive,
so at this event rate most breakouts never get an answer:

| Termination | Count |
|---|---|
| SUPERSEDED, replaced by a newer breakout | **393** |
| RETEST_HELD | 165 |
| RETEST_FAILED, the failed break | 14 |
| NO_RETEST, window elapsed untouched | 6 |
| RETEST_INCONCLUSIVE | 2 |

**68% of breakouts are superseded before they resolve.** `retest_state` as published is therefore
mostly a statement about whichever level broke most recently, not about a level a person is watching.

## 6. Metric table

| Metric | Result | A bound | B bound | |
|---|---|---|---|---|
| M1 repaint violations | **0** | `> 0` fails | `== 0` | pass |
| M2 active levels, median / p95 / max | **21 / 27 / 29** | `median > 15` fails | `<= 10` and p95 `<= 16` | **fail** |
| M3 published level count, max | **10** | | cap respected | pass |
| M4 major reversal coverage | **0.444** (12/27) | `< 0.40` fails | `>= 0.60` | **fail** |
| M5 retest liveness | **1.000** (801/801) | `< 1.0` fails | `== 1.0` | pass |
| M6 confirmed breakouts per day, median | **18** | | `<= 12` | **fail** |
| M7 trend and range joint | see below | | | reported |

M7, as a share of the 47,521 snapshots: `trend_structure` NEUTRAL 51.0%, BULLISH 25.8%, BEARISH
23.2%. `range_state` NO_RANGE 86.2%, IN_RANGE 7.9%, RANGE_BROKEN_DOWN 5.9%. The two fields are
independent by design and the cross tab shows no contradiction, though IN_RANGE only ever co-occurs
with NEUTRAL and BEARISH in this window, never with BULLISH.

`vwap_position`: ABOVE 48.8%, BELOW 46.6%, ABOVE_UPPER 2.6%, BELOW_LOWER 1.9%, AT 0.1%.

Range behaviour is churny. Six freezes inside the replay window, several of them re-freezes of the
same high with a slightly lower low after a RANGE_LOST, and IN_RANGE holds only 7.9% of the time. The
geometry test in section 7 of the contract is doing its job but the episodes are short.

## 7. Contract gaps found while implementing

All three were found and closed **before** any output existed, and are written into contract section
17. Each was closed in the direction that removes a meaningless event.

1. **A break has a side.** Read literally, V1 would stamp an "up breakout" of a level the instant it
   was born from a swing low, because such a level is already below price at birth. Levels now carry
   a `side` and a breakout requires the side to change.
2. **A sweep has a side.** A wick over a level that sits under price is ordinary trading.
3. **Frame order at a shared instant** is declared as 1h, then 5m, then the 1m publication.

A fourth was found later, during testing, and is a code bug rather than a contract gap: entering
`RETEST_TOUCHED` was resetting the **failure** run counter as well as the hold counter, so a bar that
had already closed through the level was silently forgiven and "two consecutive closes" could never
complete. Fixed, pinned by `test_retest_failed_is_the_failed_break_and_decays`, and the replay was
rerun. It moved one machine from SUPERSEDED to RETEST_FAILED and changed no verdict.

## 8. Proposed revision, not applied

Contract section 14 requires naming the failing metric and proposing, not applying. Ordered by how
much each one is worth against the measured failure:

1. **Make the cross timeframe rule symmetric.** When a 1h candidate lands within tolerance of an
   active 5m level, retire the 5m level into the 1h one rather than creating a second line. Directly
   addresses the 364 measured stacked pairs and frees publication slots. Lowest risk of the three.
2. **Give the 5m frame a shorter memory rather than a larger theta.** The calibration shows theta
   cannot fix the density, and the M4 diagnostic shows 5m levels are where the coverage comes from,
   so the lever is lifetime and touch confirmation, not the swing threshold. For example require a
   5m level to be touched at least once before it is publishable.
3. **Let the retest machine be per level, not global.** 68% SUPERSEDED is a design limit, not a
   market fact. One machine per broken level, with a bounded pool, would make `retest_state`
   answerable for the level a person is actually watching.

M4 needs its own study. Nothing above adds a level source, and the finding that 13 of 15 misses sat
inside already traded price says the missing structure is real and recoverable rather than absent.

## 9. What this does not show

- One symbol, one 33 day window, one regime that ended with a strong rally. No claim about other
  symbols, other periods, or stability of these numbers.
- No forward test. Every number here is in sample on the replay window, though the swing thresholds
  were set on a held out warmup window.
- **No edge claim of any kind.** This layer does not predict anything and was not measured against
  forward returns. It is a description.
- M4's 0.25 percent tolerance and 2.0 percent reversal definition are one reasonable choice frozen in
  advance, not the only one. They were not swept.

## 10. Reproduction

```
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r0.collect   # read only
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r0.calibrate
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r0.replay
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/crypto/test_price_structure_r0.py -q
cd data/research/crypto/price_structure_r0/preview && python3 -m http.server 8731 --bind 127.0.0.1
```

37 tests pass. The preview is a standalone static page that talks to no backend, is imported by no
trading screen, and is not part of any deploy.

## 11. Files

| Path | What |
|---|---|
| `docs/crypto/price_structure_r0/CONTRACT_V{1,2}.md` | the frozen contract and its amendment |
| `backend/app/crypto/research/price_structure_r0/` | `data`, `swings`, `levels`, `events`, `vwap`, `engine`, `calibrate`, `replay`, `collect` |
| `backend/tests/crypto/test_price_structure_r0.py` | 37 tests |
| `data/research/crypto/price_structure_r0/metrics_v1.json` | every metric in section 6 with its inputs |
| `.../snapshots_hourly_v1.jsonl` | 793 published snapshots, the full output shape |
| `.../events_v1.jsonl` | 4,508 events: swings, levels, touches, breakouts, sweeps, expiries |
| `.../overlay_v1.json` | the chart overlay artifact, last 10 days |
| `.../prefix_truncation_v1.json` | the repaint test result |
| `.../preview/` | isolated preview page plus the two rendered screenshots |
