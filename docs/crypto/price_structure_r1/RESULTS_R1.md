# US-B CRYPTO Price Structure R1, Level Curation, Results

Contract: `docs/crypto/price_structure_r1/CONTRACT_R1.md`
sha256 `68eca59e2c5b4b851ddc1dcde42a8b9df9f9afa213adddb070408e3f979d6543`, frozen before any metric
below existed. Freeze record in `data/research/crypto/price_structure_r1/contract_freeze_r1.json`.
Parent: `docs/crypto/price_structure_r0/CONTRACT_V2.md` (`d41fb4f5cd6d…`).

Nothing was committed and nothing was deployed. C1 / C1x, AUTO, the order paths, PAPER, LIVE, the
liquidity map and the order flow layers are untouched. The preview is an isolated static page.

## 1. Verdict

**A. NOT_USABLE**, by the rule frozen in contract section 9.

The single trigger is `M4 major reversal coverage = 0.2593`, against the A floor `< 0.40`.

**M4 is the only axis that fails.** The other five are all inside the `B` bounds:

| Metric | R0 | R1 | B bound | R1 |
|---|---|---|---|---|
| M1 repaint violations | 0 | **0** | `== 0` | pass |
| M2 publishable levels, median / p95 | 21 / 27 | **9 / 13** | `<= 10` and `<= 16` | pass |
| M3 hourly sets holding a duplicate pair | 0.5889 | **0.0404** | `<= 0.10` | pass |
| M4 major reversal coverage | 0.4444 | **0.2593** | `>= 0.60` | **fail, and below the A floor** |
| M5 retest liveness | 1.000 | **1.000** | `== 1.0` | pass |
| M6 confirmed breakouts per day, median | 18 | **6** | `<= 12` | pass |

So the honest sentence is not "R1 failed". It is:

> **The three allowed changes each hit the axis they were aimed at, by large margins, and the price
> was paid on the one axis the contract said in advance they could not fix. R1 bought readability
> and sold coverage.**

Both runs are scored by the same code in the same process on the same bars, so every number in the
R0 column above is a re-measurement, not a quotation of the R0 document. The R0 engine rescored by
R1's metric code returns `A_NOT_USABLE` for its own reason, `M2 median 21`, reproducing the R0
result exactly.

## 2. What held

**M1 repaint = 0** over 47,521 snapshots and 1,236 audited objects, with two audits R0 did not have:
`sources` is append only and every level and retest state transition is checked against the declared
graph of contract section 7.

**Prefix truncation identical at 6 of 6 cut points.** The engine given only the bars that closed at
or before T reproduces the full run's published snapshot at T across all 27 fields, including the
whole published level list and the whole open retest list.

| T | identical | publishable | book | published | retests open |
|---|---|---|---|---|---|
| 2026-09-04T00Z | yes | 9 | 11 | 5 | 3 |
| 2026-09-11T00Z | yes | 9 | 10 | 5 | 0 |
| 2026-09-16T00Z | yes | 8 | 9 | 6 | 4 |
| 2026-09-25T00Z | yes | 5 | 10 | 5 | 0 |
| 2026-09-30T00Z | yes | 10 | 10 | 10 | 1 |
| 2026-10-03T22Z | yes | 9 | 11 | 9 | 2 |

Two independent runs are byte identical. The data is the same frozen series: 76,320 bars, sha256
`516e980db0d2…`, and the overlap with the frozen D2 history is **60,574 minutes with 0 mismatches**
on all six fields. `theta_1h = 3.0` and `theta_5m = 6.0` are imported from R0's held out calibration
and were not recalibrated. The swing layer is literally R0's module, and M7 confirms it: the
`trend_structure` and `range_state` distributions are identical to R0 to the snapshot.

37 R0 tests still pass unchanged. 33 new R1 tests pass.

## 3. Change 1, symmetric cross timeframe merge

R0 dropped a 5m candidate landing near a 1h level but said nothing about the reverse, and since a 1h
bar's high **is** the maximum of its twelve 5m highs, the 5m level was always created first and the
1h candidate was stacked on top of it.

| | R0 | R1 |
|---|---|---|
| hourly published sets holding a near duplicate pair | **58.9%** (467 / 793) | **4.0%** (32 / 793) |
| near duplicate pairs, summed over hourly sets | 586 | **36** |
| of those, cross frame pairs | 364 | **15** |
| candidates merged | 144 | 185, of which **37 cross frame** |
| candidates silently suppressed | 26 | **0** |

The readout R0 showed as `published: … 85,696.2, 85,696.2` no longer happens. Two publication slots
are no longer spent on one price.

**The residual 36 pairs are real and have a cause.** The merge zone is decided at the instant the
candidate arrives, using the ATR of that moment; M3 re-measures the zone at the snapshot. When ATR
expands, two lines that were legitimately distinct at birth fall inside the later, wider zone. That
is a point-in-time decision behaving correctly, not a leak, and merging them later would move a
drawn line, which the repaint contract forbids.

## 4. Change 2, the 5m level lifecycle

No threshold was swept. The lever was lifetime and observed reaction to price, with two single
pre-registered values taken from R0's own frozen 48 hour 5m lifetime, quartered and halved.

| | R0 | R1 |
|---|---|---|
| publishable levels, median / p95 / max | 21 / 27 / 29 | **9 / 13 / 14** |
| whole book including unpublished, median | 21 | **10** |
| levels created | 242 | 227 |
| published per snapshot, median | 9 | 7 |

Lifecycle census over the 227 levels created: 179 were 5m sourced alone, 37 became `MULTI_TF`, 11
were 1h only. **86 levels never left `PROVISIONAL`**: price never came back to them and they were
never drawn. 33 were promoted by a 1h source arriving. Transitions: 126 `PROVISIONAL -> CONFIRMED`,
116 `CONFIRMED -> WEAKENED`, 24 `WEAKENED -> CONFIRMED`, and expiries split 84
`PROVISIONAL_UNTOUCHED`, 92 `AGE_24H`, 40 `FAR_AND_UNTOUCHED`.

## 5. Change 3, the per level retest state machine

`SUPERSEDED` is gone from the state space. Machines run concurrently, one per level, observed
maximum concurrency 6.

| Termination | R0 | R1 |
|---|---|---|
| resolved: `RETESTED` / `RETEST_HELD` | 165 | **143** |
| resolved: `HELD`, the break nothing came back to | 6 (`NO_RETEST`) | **36** |
| resolved: `FAILED`, the failed break | 14 | **13** |
| displaced before it could answer | **393** `SUPERSEDED` | **45** `EXPIRED` (41 `RELEVEL`, 3 `LEVEL_EXPIRED`, 1 `UNDECIDED`) |
| **share that resolved** | **31.9%** | **80.3%** |

M5 liveness stays 1.000: 338 of 338 machines whose window had elapsed were terminal. `retest_state`
is still published as a scalar but now names the latest breakout's own machine, and the snapshot
additionally carries the open machines nearest to price.

The event rate fell with it: 580 confirmed breakouts became 239, median per day 18 to 6, max 47 to
21, sweeps 313 to 186. Of R1's 46 breakouts confirmed on the 1h frame, **30 exist on that frame only
because a 5m originated level was merged up to the 1h confirming frame**, which is the section 3
effect reported separately as the contract required.

## 6. What failed, and the attribution

M4 fell from 12 of 27 to 7 of 27. The contract warned that nothing in R1 adds a level source and so
nothing was expected to raise M4; it fell instead. Four post verdict runs, with each half of change 2
switched off, locate the cost. **No bound was moved and no variant is adopted.**

| variant | M2 med | M2 p95 | dup share | M4 | M4 whole book | M6 med |
|---|---|---|---|---|---|---|
| R0 | 21 | 27 | 0.589 | **0.4444** (12) | 0.4444 | 18 |
| **R1 frozen** | **9** | **13** | **0.040** | **0.2593** (7) | 0.2963 | **6** |
| touch gate off, 24h lifetime | 11 | 16 | 0.165 | 0.3333 (9) | 0.3333 | 8 |
| touch gate on, 48h lifetime | 13 | 19 | 0.279 | 0.3704 (10) | 0.4074 | 11 |
| change 2 fully off, changes 1 and 3 only | 20 | 25 | 0.271 | 0.4074 (11) | 0.4444 | 16 |

Four readings, each of which is a measurement:

1. **The shorter lifetime costs more than the touch gate.** Restoring 48 hours recovers 3 of the 5
   lost hits; removing the touch gate recovers 2. The per case forensics agree: of the 5 reversals
   R0 covered and R1 did not, **4 had their covering level expire before the check** and 1 was still
   `PROVISIONAL`.
2. **The touch gate is not the clean logical trap it looks like.** The tempting story is that "a
   level is confirmed by its first revisit" and "price turns at its first revisit" are the same
   event, so the gate can never be in time. Measured, the split is 29 levels in tolerance confirmed
   after the check against 27 before it, which is close to even. The story is directionally right
   and quantitatively weak, and it is reported as weak.
3. **Changes 1 and 3 alone do not fix M2.** With the lifecycle off, the publishable median is 20
   against R0's 21. The density is 5m volume, exactly as R0's calibration said when no rung of the
   ladder reached 8 swings per day. Nearly all of the M2, M3 and M6 improvement is change 2, which
   is also where the M4 loss is. **The trade is direct and it is not avoidable by tuning within
   these three changes.**
4. **Change 1 costs one hit, by a mechanism worth recording.** In the changes-1-and-3 variant a
   merged `MULTI_TF` level inherits the 1h strength bonus, rises into the five above-price slots,
   and evicts a freshly created 1h level that happened to sit at the coming reversal. That is the
   single case in the entire study where the publication cap was the binding constraint.

### The decisive diagnostic: it is existence, not publication

| | reversals scored | a publishable level existed in tolerance | of those, published | outranked by the cap |
|---|---|---|---|---|
| R0 | 27 | 12 | **12** | 0 |
| R1 | 27 | 7 | **7** | 0 |
| changes 1 and 3 only | 27 | 12 | 11 | 1 |

Whenever a usable level existed, it was published, in both runs. **Coverage against the whole book,
including levels that were never shown, never exceeds 0.4444 in any variant.** The ceiling is set by
what the swing sources create, not by what the cap selects. R0 reached the same conclusion from the
other side: 13 of its 15 misses turned at prices inside already traded territory.

Misses still skew to the larger moves: hits average a 3.02% swing, misses 4.54%. All four reversals
larger than 5.3% are misses.

## 7. Reading the verdict

The frozen rule returns **A**, because M4 fell through the A floor. The contract's C branch,
`NEEDS_NEW_LEVEL_SOURCE`, carries the instruction to freeze the R1 contract and study only the
missing level source in R2, and it is conditioned on M4 being the sole failing axis. **M4 is the sole
failing axis.** So the letter of the verdict is A and the substance is the C instruction, and the
two are separated here rather than blended, because relabelling A as C after seeing the number is
exactly the move the contract forbids.

What that leaves for the next step, as a proposal and not an application:

1. **The curation contract is worth keeping.** Three axes moved from failing to passing with the
   repaint guarantee intact and the prefix test still exact. Nothing in section 3 to 5 needs to be
   argued again.
2. **The 24 hour 5m lifetime is the one number to revisit, and only with a fresh pre-registration.**
   It carries most of the M2 and M6 gain and most of the M4 loss. Choosing between 24 and 48 hours
   after seeing this table would be a threshold sweep, so it is not done here.
3. **R2 is a level source question.** The missing structure is at prices the market has already
   traded, and no swing based source reaches it: 15 of 27 misses in R0, 20 of 27 in R1, with the
   book, not the cap, as the constraint in every variant.

## 8. What this does not show

- One symbol, one 33 day window, one regime that ended with a strong rally.
- No forward test. Every number is in sample on the replay window, though the swing thresholds were
  set on a held out warmup window and were not re-fitted for R1.
- **No edge claim of any kind.** This layer predicts nothing and was never measured against forward
  returns.
- M4's 0.25% tolerance and 2.0% reversal definition are R0's single frozen choice carried over, not
  swept.

## 9. Reproduction

```
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r1.replay
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r1.diagnose
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/crypto/test_price_structure_r1.py -q
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/crypto/test_price_structure_r0.py -q
cd data/research/crypto/price_structure_r1/preview && python3 -m http.server 8732 --bind 127.0.0.1
```

`replay` runs both engines and writes every number in this document. `diagnose` writes section 6 and
asserts that its frozen variant reproduces `metrics_r1.json` exactly.

## 10. Files

| Path | What |
|---|---|
| `docs/crypto/price_structure_r1/CONTRACT_R1.md` | the frozen contract |
| `backend/app/crypto/research/price_structure_r1/` | `levels`, `events`, `engine`, `metrics`, `replay`, `diagnose`; data, swings, VWAP and calibration are imported from R0 |
| `backend/tests/crypto/test_price_structure_r1.py` | 33 tests |
| `.../price_structure_r1/metrics_r1.json` | both runs scored, the before and after block, the verdict |
| `.../diagnostics_r1.json` | the attribution table, the per case forensics, the ranking measurement |
| `.../snapshots_hourly_r1.jsonl` | 793 published snapshots |
| `.../events_r1.jsonl`, `level_transitions_r1.jsonl`, `retest_transitions_r1.jsonl` | every event and every logged state transition |
| `.../prefix_truncation_r1.json` | the repaint test result |
| `.../preview/` | the isolated preview page and its two rendered screenshots |
