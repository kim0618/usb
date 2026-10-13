# US-B CRYPTO Price Structure R2, Missing Level Sources, Results

Contract: `docs/crypto/price_structure_r2/CONTRACT_R2.md`
sha256 `b7839fb9cbd0fccbe019eee40c4a944cb753f4a7a333324f39880a071548069e`, frozen before any metric
below existed. Freeze record in `data/research/crypto/price_structure_r2/contract_freeze_r2.json`.
Parents: `CONTRACT_R1.md` (`68eca59e2c5b…`), `CONTRACT_V2.md` (`d41fb4f5cd6d…`).

Nothing was committed, pushed or deployed. C1 / C1x, AUTO, the order paths, PAPER, LIVE, the
liquidity map, the market structure collector and the order flow layers are untouched. The preview
is an isolated static page.

## 1. Verdict

**A. NOT_USABLE**, by the rule frozen in contract section 11.

The final combination is **`SWING + S1`**. Its single trigger is
`M4 major reversal coverage = 0.2963`, against the A floor `< 0.40`.

**M4 is the only failing axis.** The other five are all inside the `B` bounds:

| Metric | R0 | R1 | R2 final | B bound | R2 final |
|---|---|---|---|---|---|
| M1 repaint violations | 0 | 0 | **0** | `== 0` | pass |
| M2 publishable levels, median / p95 | 21 / 27 | 9 / 13 | **10 / 14** | `<= 10` and `<= 16` | pass |
| M3 hourly sets holding a duplicate pair | 0.5889 | 0.0404 | **0.0517** | `<= 0.10` | pass |
| M4 major reversal coverage | 0.4444 | 0.2593 | **0.2963** | `>= 0.60` | **fail, and below the A floor** |
| M5 retest liveness | 1.000 | 1.000 | **1.000** | `== 1.0` | pass |
| M6 confirmed breakouts per day, median | 18 | 6 | **6** | `<= 12` | pass |

The task's third label, `C. SOURCE_COVERAGE_STILL_INSUFFICIENT`, is what the **substance** says: the
three allowed families do not reach the coverage the gate asks for, and no other axis fails. The
**letter** of the frozen rule is `A`, because M4 is below the A floor rather than merely below B.
The two are separated here rather than blended, exactly as R1 did, because relabelling `A` as `C`
after seeing the number is the move the contract forbids. Either way the next step is the same one
the task sets for `A` and for `C`: **Price Structure as a standalone study stops**, and whether to
extend the structural source set is the user's decision.

## 2. The headline finding

> **Three new structural source families, alone and together, did not break R0's coverage ceiling.**
> Coverage measured against the **whole alive book**, including every level that was never shown,
> was 0.4444 in R0, never exceeded 0.4444 in any of R1's four variants, and **reaches exactly
> 0.4444, never more, in all eight R2 variants** including the one with all three families on.

| Variant | M4 published | M4 whole book |
|---|---|---|
| R0 | 0.4444 | 0.4444 |
| R1 = `SWING_ONLY` | 0.2593 | 0.2963 |
| `SWING + S1` (final) | 0.2963 | 0.3333 |
| `SWING + S2` | 0.2593 | 0.2963 |
| `SWING + S3` | 0.2963 | 0.4074 |
| `SWING + S1 + S2 + S3` | 0.3704 | **0.4444** |

Twelve pre-registered variants across three studies, and the set of prices at which this layer can
have a level never covers more than 12 of the 27 major reversals. That is the result. It is not a
statement about publication, ranking, lifetime or curation, all of which were measured separately
and all of which are inside their bounds.

## 3. The equivalence blocker, which is what makes the comparison mean anything

Contract section 2.1: R2 with all three families off must reproduce R1 exactly.

**It does.** `metrics.score` on the R2 engine with no family enabled is **byte identical** to the
`R1` block of R1's frozen `metrics_r1.json`, across M1 to M7, the counts, the lifecycle census and
the publication cap, and it returns R1's own verdict for R1's own reason
(`A_NOT_USABLE`, `M4 coverage=0.2593 below 0.40`).

That is checked first and the run aborts if it fails. It is also checked at object level in the unit
tests: the R2 book fed only swings agrees with the R1 book field for field, including `strength`,
`confirm_tf`, `sources`, the merge counters and the promotion counter. Every difference reported
below therefore came from a new source and not from a rewrite.

Carried without recomputation: `theta_1h = 3.0`, `theta_5m = 6.0` from R0's held out warmup
calibration; the whole metric suite and the verdict function imported from `price_structure_r1`; the
data is the same frozen series, 76,320 bars, sha256 `516e980db0d2…`, with **60,574 minutes of
overlap against the frozen D2 history and 0 mismatches** on all six fields. 34 new R2 tests pass.
The 37 R0 and 33 R1 tests still pass unchanged.

## 4. What each family did, the measurement contract section 7 required

All figures are the `Swing + F` variant against `SWING_ONLY`, scored by the same code in the same
process.

| | S1 `D` prev session | S2 `RANGE` range edge | S3 `FLIP` S-R flip |
|---|---|---|---|
| **new M4 hits** | **+1** (7 to 8) | **0** | **+2, and −1** (net 8) |
| M4 coverage | 0.2593 to **0.2963** | **0.2593**, unchanged | 0.2593 to **0.2963** |
| M4 whole book | 0.2963 to 0.3333 | 0.2963, unchanged | 0.2963 to **0.4074** |
| candidates offered | 106, over 53 sessions | 14, over 7 episodes | 77 claims |
| **overlap with the swing book** | 65 of 106 merged (61 into a 5m level, 4 into a 1h level); 41 created a line | **13 of 14 merged** (7 into 5m, 6 into 1h); 1 created a line | 76 of 77 added a token to an existing level; **0 created a line** |
| **M2 publishable median** | 9 to **10** (+1) | 9, **unchanged** | 9 to **15** (+6) |
| M2 publishable p95 | 13 to 14 (+1) | 13, unchanged | 13 to **22** (+9) |
| **M3 duplicate share** | 0.0404 to **0.0517** | 0.0404, unchanged | 0.0404 to **0.2598** |
| M3 duplicate pairs | 36 to 43 | 36, unchanged | 36 to **250** |
| M3 cross frame pairs | 15 to 5 | 15, unchanged | 15 to 7 |
| **M6 breakouts per day, median** | 6, unchanged | 6, unchanged | 6 to 4 |
| M6 breakouts total | 239 to 229 | 239 to 237 | 239 to **156** |
| M6 by frame | 5m 193 to 160, 1h 46 to 69 | 5m 193 to 191, 1h 46 | 5m 193 to 64, 1h 46 to **92** |
| **M1 repaint** | **0** | **0** | **0** |
| prefix truncation | identical 6 / 6 on the final combination | | |
| **admitted** | **yes** | no, adds no hit | no, M2 median, M2 p95 and M3 all breached |

### 4.1 S1, previous session high and low: one hit, and it came through the merge

S1 is cheap. 106 objects over 53 UTC sessions, two per boundary, each expiring the moment the next
session supersedes it, and 65 of them landed in the zone of a level that already existed. The
publishable median rose by exactly one, which is what a source that mostly merges costs.

The one hit it bought is **not a new line**. The post verdict forensics name the case:

- 2026-09-02T10:00Z, a `SWING_LOW` reversal at 76,199.1, a 3.887% move, tolerance 190.5.
- In `SWING_ONLY` the covering level **existed and was never drawn**: a 5m swing low at 76,364.0
  created 09-01T20:15Z, which expired 09-02T08:20Z as `PROVISIONAL_UNTOUCHED`, an hour and forty
  minutes before the check, having never been touched.
- With S1 the same level was **promoted to `CONFIRMED` at 09-02T00:00Z by `PROMOTED_BY_D_SOURCE`**,
  because the previous session's low landed inside its merge zone. It was published, and it covered
  the reversal.

So S1's measured contribution runs through the **promotion** path, not through a new price. A second
structural source arriving at a price is evidence the touch gate was asking for and not getting.
That is a mechanism worth recording, and it is one case out of 27.

**One case is the resolution of this sample.** 27 reversals means a single case is 3.7 percentage
points. The admission rule, frozen in advance, asked only for `hits > baseline hits`, and S1 met it
by one. The honest statement is that S1 is admitted by the frozen rule on the strength of a single
reversal, and that nothing in this study distinguishes a one case gain from noise.

### 4.2 S2, the range boundary: structurally redundant, and that is informative

S2 changed **nothing** that M2, M3 or M4 can see. Not a little: nothing. Same publishable median,
same p95, same duplicate share, same duplicate pairs, same coverage, same whole book coverage.

The reason is in the definition, and it was knowable in advance although it was not foreseen: R0's
range is **built out of four confirmed 1h swings**, so its boundaries `H` and `L` are swing extremes
by construction. **13 of the 14 boundary objects merged into a level that was already in the book.**
Exactly one created a line, over 33 days.

R0's decision to keep range edges out of the level book, which looked like an omission worth
reversing, cost no coverage at all. Reversing it is measurable only in the event log: the 2
`RANGE_EDGE` breakouts became `LEVEL` breakouts, 5m frame breakouts fell 193 to 191, the 1h frame
count was unchanged at 46, and range episodes fell 8 to 7 because a boundary that is now `major`
needs an hourly close to be considered broken. Net 239 to 237 breakouts.

### 4.3 S3, the S-R flip: coverage at six times the duplicate budget

S3 is the family that moves the whole book number most, 0.2963 to 0.4074, and it is the family the
gate cannot pay for.

77 flips were claimed, all on a `RETESTED` termination, and **76 of them added a token to a level
that already existed**. Not one new line was created. So S3's cost is not stacking. Its cost is
**lifetime**: a level that used to die 24 hours after its last touch now takes the 10 day horizon,
and the lines accumulate.

- M2 publishable median 9 to **15**, p95 13 to **22**. The A floor is a median of 15, so S3 alone
  is at the floor.
- M3 duplicate share 0.0404 to **0.2598**, pairs 36 to **250**. That is **6.5 times the 0.10 bound**
  and it is R1's solved problem coming back through a different door: cross frame pairs actually
  fell, 15 to 7. The new duplicates are same frame levels that no longer expire.
- M6 median fell 6 to 4 and breakouts fell 239 to 156, because almost every level became `major` and
  so moved to the hourly confirming frame. 1h frame breakouts rose 46 to 92; **43 of them exist on
  that frame only because a new token made a 5m originated level major.**

**S3 also lost a reversal, and the mechanism is the publication cap.** 2026-09-11T14:00Z, a
`SWING_HIGH` at 79,909.7, a 5.279% move, covered in the baseline by a published `['1h','5m']` level
at 79,743.8 with strength 4. With S3 that level was still publishable, and so were two others inside
tolerance, but they ranked **6th, 7th and 8th** on their side of price. The five slots above price
were taken by flip levels with strengths **80, 61, 45, 42 and 22**, because `strength` counts
touches and a 10 day horizon lets touches accumulate without bound. R1 found exactly one case in the
whole study where the cap was the binding constraint. S3 makes the cap bind by making strength
unbounded in time.

## 5. The frozen combination rule, applied

Admission, contract section 9, read only from the four required variants:

| Family | adds an M4 hit | M1 = 0 | M2 med ≤ 10 | M2 p95 ≤ 16 | M3 ≤ 0.10 | M5 = 1.0 | M6 med ≤ 12 | admitted |
|---|---|---|---|---|---|---|---|---|
| S1 | yes | yes | yes | yes | yes | yes | yes | **yes** |
| S2 | **no** | yes | yes | yes | yes | yes | yes | no |
| S3 | yes | yes | **no** | **no** | **no** | yes | yes | no |

Final combination `SWING + S1`, scored once, verdict `A`. Prefix truncation identical at 6 of 6 cut
points. Two independent runs byte identical.

**The lattice is reported, and the best subset is not selected.** The full eight subset lattice is
in `variant_table_r2.json`, and it shows that `S1 + S3` reaches `M4 = 0.3704` with `M2 median 18`
and `M3 0.2535`, so it fails `B` on three axes and is below the `A` floor on M4 as well. No subset
in the lattice reaches `B`. Even if one had, choosing it after the fact would be selection on the
verdict metric with 27 cases in the sample, which the contract rules out in advance.

| variant | M1 | M2 med | M2 p95 | M3 | M4 | hits | M4 book | M5 | M6 med | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `SWING_ONLY` | 0 | 9 | 13 | 0.0404 | 0.2593 | 7 | 0.2963 | 1.0 | 6 | A |
| `SWING_S1` **final** | 0 | **10** | **14** | **0.0517** | **0.2963** | **8** | 0.3333 | 1.0 | **6** | **A** |
| `SWING_S2` | 0 | 9 | 13 | 0.0404 | 0.2593 | 7 | 0.2963 | 1.0 | 6 | A |
| `SWING_S3` | 0 | 15 | 22 | 0.2598 | 0.2963 | 8 | 0.4074 | 1.0 | 4 | A |
| `SWING_S1_S2` | 0 | 10 | 14 | 0.0517 | 0.2963 | 8 | 0.3333 | 1.0 | 6 | A |
| `SWING_S1_S3` | 0 | 18 | 24 | 0.2535 | 0.3704 | 10 | 0.4444 | 1.0 | 5 | A |
| `SWING_S2_S3` | 0 | 15 | 22 | 0.2598 | 0.2963 | 8 | 0.4074 | 1.0 | 4 | A |
| `SWING_S1_S2_S3` | 0 | 18 | 24 | 0.2535 | 0.3704 | 10 | 0.4444 | 1.0 | 5 | A |

S2 is invisible in this table wherever S3 is present, and adds nothing wherever S1 is present. The
whole lattice collapses to what S1 and S3 do.

## 6. What held

**M1 repaint = 0** in every one of the eight variants, over 47,521 snapshots and 1,275 audited
objects in the final combination, with R1's two monotonicity audits extended to the five token set:
`sources` is append only, and every level and retest transition is checked against R1's declared
graph. Each per token expiry anchor (`day_expiry`, the range episode, `flip_at`) is written once when
its token is added and is asserted never to be revised, including when a second day's object merges
into the same level.

**Prefix truncation identical at 6 of 6 cut points** on the final combination, across all 29
snapshot fields, including the whole published level list, the whole open retest list and the new per
token publishable census:

| T | identical | publishable | book | published | retests open | by source `5m / 1h / D` |
|---|---|---|---|---|---|---|
| 2026-09-04T00Z | yes | 9 | 10 | 6 | 3 | 9 / 6 / 7 |
| 2026-09-11T00Z | yes | 11 | 12 | 6 | 1 | 9 / 7 / 10 |
| 2026-09-16T00Z | yes | 10 | 10 | 7 | 4 | 9 / 6 / 8 |
| 2026-09-25T00Z | yes | 8 | 11 | 8 | 0 | 7 / 3 / 6 |
| 2026-09-30T00Z | yes | 11 | 11 | 10 | 1 | 7 / 7 / 8 |
| 2026-10-03T22Z | yes | 9 | 11 | 9 | 2 | 7 / 8 / 8 |

The token columns sum to more than the publishable count because a merged level holds several
tokens. That is the point of S1: 101 levels carried the `D` token at some time, while the
publishable median rose by one.

**M5 liveness 1.000**, 328 of 328 machines whose window had elapsed were terminal, resolved share
0.7904 against R1's 0.8033, maximum concurrency 6. **The publication cap was respected**, maximum 10
published, median 7. Lifecycle census over 247 levels created: 199 were 5m sourced, 75 became
multi token, 63 never left `PROVISIONAL`, 59 were promoted by a major source arriving, and expiries
split 99 `AGE_24H`, 62 `PROVISIONAL_UNTOUCHED`, 40 `FAR_AND_UNTOUCHED`, 34 `DAY_SUPERSEDED`.

## 7. The misses that survive, and what they are made of

19 of 27 reversals are still missed by the final combination. The post verdict forensics classify
every one of them:

- **17 of the 19 are at a price the market had already traded** before the reversal. Two are beyond
  the prior high or low, in territory with no history at all.
- The median distance from the reversal price to the **nearest level of any kind in the book**,
  published or not, is **522**, against an M4 tolerance of about 205. Four misses have no structure
  within 1,000, the worst at 5,304.
- Misses still skew to the larger moves: hits average a 3.13% swing, misses 4.58%. The three largest
  reversals in the window, 9.39%, 9.10% and 7.98%, are all misses, and the second of those has
  nothing in the book within 5,304.
- Exactly **one** reversal had a covering level in the book that was not on the screen, and the
  forensics show it was **`PROVISIONAL`**, not outranked. **The publication cap bound zero times in
  the final combination**, which reproduces R1's finding for the third time: whenever a publishable
  level existed inside the tolerance, it was published.

So R0's central measurement survives a third independent test with three extra source families
available: the layer misses because **no level exists at the price**, and the prices in question are
mostly prices the market has already traded.

## 8. Reading the result

1. **The curation contract is still worth keeping, and is still not the problem.** M1, M2, M3, M5
   and M6 all stayed inside the `B` bounds while a new family was added. Nothing in R1 sections 3 to
   5 needs to be argued again.
2. **The ceiling is a property of the source set, and these three families are not the missing
   source.** The whole book ceiling of 0.4444 was set in R0 and has not moved in twelve
   pre-registered variants. S1 is cheap and buys one case through the promotion path. S2 is
   definitionally redundant with the swing book. S3 reaches further into the book, 0.4074, but pays
   with 6.5 times the duplicate budget and reintroduces the publication cap as a binding constraint.
3. **A 0.60 coverage bound and a 10 level publishable budget may not be jointly reachable from price
   alone**, and this study cannot tell those two apart, because both are frozen R0 choices and
   neither may be swept. What it can say is that the binding constraint was never the ranking, the
   cap or the lifetime: it was existence.
4. **What the next source would have to look like, as an observation and not a proposal.** It would
   have to place levels at interior, already traded prices without placing many of them, since the
   budget is one or two publishable lines. Nothing in the three allowed families does that: two of
   them produce extremes, which the swing source already produces, and the third produces standing
   rather than prices.

**No revision is applied.** Per contract section 11 and the task, on `A` or `C` Price Structure as a
standalone study stops here, and whether to extend the structural source set is the user's decision.

## 9. What this does not show

- One symbol, BTCUSDT. One 33 day window, 2026-09-01 to 2026-10-04. One regime, which ended in a
  strong rally. No claim about any other symbol or period.
- **27 major reversals is the whole M4 sample.** One case is 3.7 points. S1's entire measured gain is
  one case, and S3's is two gained against one lost. No difference in this study is claimed to be
  more precise than that.
- No forward test. Every number is in sample on the replay window, although the swing thresholds were
  set on a held out warmup window and were not refitted for R1 or R2.
- **No edge claim of any kind.** This layer describes structure. It does not predict, and no forward
  return was computed at any point.
- M4's 0.25% tolerance and 2.0% reversal definition are R0's single frozen choices, carried over and
  never swept.

## 10. Contract gaps closed during implementation, all before any result

Recorded in the same spirit as R0 section 17, because each is a place where the frozen text did not
fully determine the code:

1. **Whose frame is the incumbent's, in the merge zone of section 3.3.** The contract defines the
   zone over a pair of sources; an incumbent can hold several. The implementation uses the
   incumbent's **immutable originating `tf`**, which is the field R1 used, because that is the only
   reading under which section 2.1's equivalence holds. Stated and tested.
2. **A new token does not lift `WEAKENED`.** R1 promotes a `PROVISIONAL` level when a major source
   arrives and says nothing about `WEAKENED`. R2 keeps that exactly, rather than inventing a lift
   for the new tokens, so no new token behaves differently from the `1h` token it generalises. A
   level that was weakened while 5m only therefore keeps the label until something touches it.
3. **A flip at a price that already has a level merges rather than stacking.** Contract section 6
   says a flip of an object outside the book creates a level. The implementation routes that case
   through `offer`, so section 3.3's merge rule applies first. It fired once, as
   `TOKEN_ADDED_ON_MERGE`.
4. **A partial session emits nothing.** The first day boundary of the series has no complete
   preceding UTC day, so no `D` object is emitted there. Tested.

## 11. Reproduction

```
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r2.replay
PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r2.diagnose
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/crypto/test_price_structure_r2.py -q
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/crypto/test_price_structure_r1.py -q
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/crypto/test_price_structure_r0.py -q
cd data/research/crypto/price_structure_r2/preview && python3 -m http.server 8733 --bind 127.0.0.1
```

`replay` checks the equivalence blocker, builds all eight variants, applies the frozen admission
rule and writes every number in this document. `diagnose` writes section 4.1, 4.3 and 7 and asserts
that its recorder engine reproduces the frozen verdict.

## 12. Files

| Path | What |
|---|---|
| `docs/crypto/price_structure_r2/CONTRACT_R2.md` | the frozen contract |
| `backend/app/crypto/research/price_structure_r2/` | `levels`, `events`, `engine`, `metrics`, `replay`, `diagnose`; data, swings, VWAP, calibration, the metric suite and the verdict function are imported from R0 and R1 |
| `backend/tests/crypto/test_price_structure_r2.py` | 34 tests |
| `.../price_structure_r2/metrics_r2.json` | all eight variants scored, the blocker, the admission, the verdict |
| `.../variant_table_r2.json` | the lattice in one table |
| `.../diagnostics_r2.json` | the four post verdict forensics of sections 4.1, 4.3 and 7 |
| `.../prefix_truncation_r2.json` | the repaint test on the final combination |
| `.../snapshots_hourly_r2.jsonl`, `events_r2.jsonl`, `level_transitions_r2.jsonl` | the final combination's snapshots, events and logged transitions |
| `.../preview/` | the isolated preview page |
