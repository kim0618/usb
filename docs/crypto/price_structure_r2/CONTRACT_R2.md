# US-B CRYPTO Price Structure R2, Missing Level Sources, Contract

Status: **FROZEN BEFORE ANY R2 RESULT IS COMPUTED**
Frozen at: 2026-10-05 (Asia/Seoul)
Parents:
`docs/crypto/price_structure_r0/CONTRACT_V2.md` sha256
`d41fb4f5cd6d28f48a309c3122b9eb5afc1bb45a0f430b009eacb72ea2c0bb22`
`docs/crypto/price_structure_r1/CONTRACT_R1.md` sha256
`68eca59e2c5b4b851ddc1dcde42a8b9df9f9afa213adddb070408e3f979d6543`

Scope: research and an isolated preview only. C1 / C1x, AUTO, PAPER, LIVE, the order paths, the
production deploy path, the liquidity map, the market structure collector and the order flow layers
are all out of scope and unchanged. Nothing here is committed, pushed or deployed.

## 0. Where this starts

R0 returned **A_NOT_USABLE** on three axes at once. R1 curated the same level set and returned
**A_NOT_USABLE** on **one** axis: M4 major reversal coverage fell to 0.2593 while M1, M2, M3, M5 and
M6 all landed inside the `B` bounds. R1's own measurement located the ceiling:

- whenever a publishable level existed inside the M4 tolerance it **was** published, in every
  variant (the publication cap bound exactly one case in the whole study),
- coverage measured against the **whole alive book**, including levels never shown, **never exceeded
  0.4444** in any variant,
- 13 of R0's 15 misses turned at prices inside already traded territory, and using 1h levels alone
  made coverage worse, not better.

So the constraint is **existence, not publication**: the swing sources do not create a level at the
price where the reversal happens. R2 asks whether a small, pre-registered set of new structural
sources changes that, without giving back what R1 bought.

## 1. Question

Does adding at most three new structural level sources raise **M4** while keeping **M1, M2, M3, M5
and M6** inside the bounds R1 already satisfies?

The question is scored on R1's ruler, unchanged, so that R0, R1 and R2 are comparable to the
snapshot.

## 2. Carried over unchanged, by reference

R2 **imports** the R0 and R1 modules rather than restating them, so that "unchanged" is enforced by
the code and not only asserted.

| Carried | Where it lives in R2 |
|---|---|
| Data, the 1m source, the ATR definition, warmup and replay windows, the D2 crosscheck | `price_structure_r0.data` |
| Swings, HH/HL/LH/LL, `trend_structure`, the range geometry of `RangeState` | `price_structure_r0.swings` |
| Session VWAP and `vwap_position` | `price_structure_r0.vwap` |
| Swing threshold calibration and its held out warmup window | `price_structure_r0.calibrate`; `theta_1h = 3.0`, `theta_5m = 6.0` imported, **not recalibrated** |
| `buffer(p, tf)`, the two close breakout rule, `rebreak_cooldown = 24`, the side rule, `Breakout`, `Sweep`, `RangeEdge` | `price_structure_r0.events` |
| Sweep, failed break, touch (`tol_touch = 0.15 * ATR_5m`, `touch_cooldown = 6`), role (`tol_role = 0.15 * ATR_5m`) | unchanged |
| `MAX_PUBLISHED = 10`, at most 5 per side, the ranking key | unchanged |
| The 1m decision grid, PIT rule, the frame order at a shared instant | unchanged, extended only by section 3.6 |
| The symmetric cross timeframe merge, `merge_tol = 0.25 * ATR` | `price_structure_r1.levels`, generalised only as section 3.3 states |
| The 5m level lifecycle: `PROVISIONAL` 12h, `WEAKENED` after 12h, 5m expiry 24h, 1h expiry 10 days, far-and-untouched expiry | `price_structure_r1.levels`, unchanged |
| The per level retest state machine and its state space | `price_structure_r1.events`, unchanged |
| The whole metric suite M1 to M7, the publication cap check and the verdict function | `price_structure_r1.metrics`, unchanged and imported |

**No threshold in R0 or R1 is changed by this contract.** The 24 hour 5m lifetime, the 12 hour
provisional lifetime, the 48 bar retest window, `theta_5m`, `theta_1h`, the M4 tolerance of 0.25%,
the 2.0% major reversal definition and every bound in section 11 are all left exactly as frozen.

### 2.1 Equivalence blocker

R2 with all three new families switched off **must reproduce R1 exactly**: the same verdict and a
byte identical metric payload for M1 to M7, the counts and the publication cap. This is checked
first and reported. **If it does not hold, that is an implementation defect and a blocker, and no
R2 verdict is issued.** It is the only way to know that a difference measured later came from a new
source and not from a rewrite.

## 3. The source lattice

### 3.1 Tokens

A level carries `sources`, an **append only** set over

```
{ 5m, 1h, D, RANGE, FLIP }
```

`5m` and `1h` are R1's. `D`, `RANGE` and `FLIP` are the three new families of sections 4, 5 and 6.
`provenance` is derived and is `MULTI` when more than one token is present.

### 3.2 Major

```
MAJOR = { 1h, D, RANGE, FLIP }
major(level) = sources ∩ MAJOR ≠ ∅
```

Every R1 rule that tested `"1h" in sources` now tests `major`. That is the whole of the
generalisation and it is declared here, before any result:

- `confirm_tf` is `1h` when `major`, else `5m` (R1 section 3, "one identity means one confirming
  frame").
- a level is born `CONFIRMED` with no `PROVISIONAL` state when `major` (R1 section 4).
- `strength = touches + (2 if major else 0) + (1 if touched within 24h else 0) - (1 if WEAKENED)`.
- the `WEAKENED` scan skips `major` levels (R1 section 4).

The justification is R1's own and it is the same one: these sources are sparse by construction and
are defined by completed, publicly observable structure rather than by a 5m retracement, so they are
hourly scale objects, and an hourly scale object needs an hourly close to be considered broken.
When no new family is enabled, `MAJOR ∩ sources` is `{1h}` or empty and every rule above reduces to
R1's, which is what section 2.1 checks.

### 3.3 Merge zone

A candidate of any source that lands in the same structural zone as an **alive** level merges into
that level's identity, whatever the order of arrival. On merge the candidate is discarded, the
incumbent's `touches` and `merges` are incremented, its source token is added, and its `price`,
`id`, `tf`, `pivot_at` and `created_at` are **never recomputed**. A drawn line never moves.

The zone tolerance is a property of the pair and not of who arrived first. Define the **frame of a
source**: `5m -> 5m`, and every token in `MAJOR -> 1h`. Then

```
zone(a, b) = 0.25 * ATR_5m   if frame(a) == 5m or frame(b) == 5m
             0.25 * ATR_1h   otherwise
```

This is a strict generalisation and it **widens nothing**: `5m`/`5m` is R0's same frame rule,
`1h`/`1h` is R0's same frame rule, and anything against `5m` is R1's cross frame rule.

### 3.4 Lifetime, the max rule

Each source carries its own expiry predicate. A level is expired when **every** token in its
`sources` is individually expired. Because `sources` is append only, a level's lifetime can only
ever lengthen, so a line is never removed sooner because something was added to it.

| Token | Expired at |
|---|---|
| `5m` | `now - max(created_at, last_touch_at) > 24h`, R1 unchanged. While the level is `PROVISIONAL` the 12h untouched rule of R1 section 4.1 applies instead |
| `1h` | `now - max(created_at, last_touch_at) > 10 days`, R1 unchanged |
| `D` | `now >= day_expiry`, the next UTC day boundary after the instant the `D` token was added (section 4) |
| `RANGE` | the range episode that contributed the token has ended (section 5) |
| `FLIP` | `now - max(flip_at, last_touch_at) > 10 days` (section 6) |

The expiry reason logged is the predicate of the **last** token to become expired, so a single
source level logs exactly the reason R1 logged. R1 section 4.1 rule 4, the far-and-untouched expiry
(`|close - price| > 10 * ATR_1h`, untouched for 24h, older than 24h), applies to **any** level
regardless of `sources` and is unchanged. It is what bounds the pool of long lived levels.

### 3.5 Events

A new source level is an ordinary book level. It is touched, swept, broken and retested by the
machinery of R0 and R1, with no exception and no new event type. Because it is `major` its breakouts
are evaluated on 1h closes, as section 3.2 states.

### 3.6 Frame order at a shared instant

R1's order is 1h, then 5m, then the 1m publication. R2 inserts the day boundary objects between the
1h step and the 5m step, and the flip promotion inside the 5m step immediately after the retest
machines are stepped:

```
1h close  ->  1h swing candidates  ->  range episode evaluation
          ->  D candidates (only when at_ms is a UTC day boundary)
5m close  ->  5m swing candidates -> touch scan -> breakouts -> sweeps
          ->  retest step -> FLIP promotion -> weaken scan -> expire scan
1m        ->  publication
```

Everything in this order is computed from bars that have already closed. No partial bar is read.

## 4. S1, previous session high and low, token `D`

**Definition.** At each UTC day boundary instant `T`, the UTC day `[T - 24h, T)` is complete and
known. Two candidates are emitted:

- `PREV_DAY_HIGH` at the maximum 1m high of that day
- `PREV_DAY_LOW` at the minimum 1m low of that day

The session is the UTC day, which is the boundary the whole study already uses for M6 and for the
session VWAP reset. No other session definition is tried.

**PIT.** Both prices are functions of bars that closed at or before `T`, and the candidates are
emitted at `T`. Nothing is known one instant earlier and nothing is revised later.

**Birth.** `major`, so born `CONFIRMED`, with `origin_kind` `PREV_DAY_HIGH` or `PREV_DAY_LOW` and
`side` taken from where the last close stood, exactly as R1 does for a swing candidate.

**Lifetime.** The `D` token expires at `T + 24h`, the next day boundary, where the next day's object
of the same kind is created. Reason `DAY_SUPERSEDED`. This is definitional and not a tuned number:
"the previous day's high" is a reference defined for the day that follows it and for no longer. A
pure `D` level therefore contributes **exactly two** publishable lines at any instant.

**Merge.** Through section 3.3 like any other candidate. A previous day extreme that coincides with
an existing level adds no line; it adds the `D` token, which by section 3.4 extends that level's
lifetime to at least the end of the day. This is the cheapest thing in the contract and it is
expected to be where S1 does most of its work.

## 5. S2, range high and low, token `RANGE`

**Definition.** R0 and R1 already compute a frozen range: four confirmed 1h swings, width
`<= 6 * ATR_1h`, every high within `0.35 * W` of `H` and every low within `0.35 * W` of `L`. On the
transition into `IN_RANGE` the boundaries `(H, L)` are frozen and never recomputed. R0 and R1 fed
those two prices to the breakout scanner as `RangeEdge` objects and **deliberately kept them out of
the level book**, so they were never published, never ranked, never touched and never merged.

S2 is exactly that decision reversed: on the `RANGE_FROZEN` transition two candidates are emitted,
`RANGE_HIGH` at `H` and `RANGE_LOW` at `L`. The geometry is imported, not redefined.

**No double counting.** When S2 is enabled the standalone `RangeEdge` objects are **removed** from
the breakout and sweep target lists, because their price is now carried by a book level. Otherwise
one price would raise two breakouts. The range episode feedback that R0 drives from an edge break
(`on_edge_break`, which starts the 24 bar `RANGE_BROKEN` timer) is driven instead by a confirmed
breakout of the level that carries the current episode's `RANGE_HIGH` or `RANGE_LOW` token.

**Declared consequence.** A range boundary becomes `major`, so it is broken on **1h** closes rather
than on 5m closes as in R0 and R1. A range is therefore declared broken later than before. This
follows from section 3.2 and it is stated here because it will move M6 and the range log. Its effect
is reported separately in section 8 so it cannot hide inside a headline.

**Birth.** `major`, born `CONFIRMED`, `origin_kind` `RANGE_HIGH` or `RANGE_LOW`.

**Lifetime.** The `RANGE` token expires when its episode ends, which is R0's existing `RANGE_LOST`
or `RANGE_CLEARED` transition, the latter being the frozen 24 bar hold after a break. Reason
`RANGE_ENDED`. No new number. A pure `RANGE` level contributes **two** publishable lines while the
market is in a range and none otherwise.

## 6. S3, S-R flip level, token `FLIP`

**What the allowed family offers, and the half of it that is rejected before results.** The family
is "breakout origin / S-R flip level". The **breakout origin** reading, the extreme of the base the
impulse left, is **rejected here, before any R2 number exists**, for a measured reason: that price is
a swing extreme, so the swing source already creates a level there, and adding it would spend
density without adding a price the book does not already hold. R2 therefore takes the **S-R flip**
reading, and says plainly what it can and cannot do.

**Definition.** A flip is not claimed when a level breaks. It is claimed when the market confirms it:
at the instant a retest machine terminates `RETESTED`, that is, price returned into the broken
level's buffer and then closed back on the breakout side for two consecutive 5m closes, the broken
price has demonstrably changed role. At that instant:

- if the broken object is a **book level**, the `FLIP` token is added to it. It becomes `major` if it
  was not already, takes the 10 day horizon of section 3.4 and the major strength bonus. A logged
  transition records it.
- if the broken object is **not in the book**, which under R0 and R1 means a `RangeEdge`, a **new
  level** is created at that object's price with `sources = {FLIP}`, `origin_kind`
  `FLIP_FROM_RANGE_HIGH` or `FLIP_FROM_RANGE_LOW`, born `CONFIRMED`.

**PIT and repaint.** The termination is produced by R1's retest machine on closed 5m bars. Adding a
token does not move a price, and the new level case creates a line at an instant and never revises
it. `FLIP` is append only like every other token.

**Lifetime.** 10 days from `max(flip_at, last_touch_at)`, the same horizon an hourly level takes,
because a price the market has broken and then held is at least as structural as a confirmed 1h
swing. No new number. The pool is bounded by the far-and-untouched rule of section 3.4: a flipped
level that price has run away from and stopped trading at dies 24 hours after it is last touched.

**What S3 can and cannot do, stated in advance.** Except for the range edge case, S3 **creates no
level at a price that never had one**. It is a standing and lifetime source, not a price source. Its
whole mechanism for M4 is to keep alive the levels the market has already demonstrated it respects,
which is aimed at R1's measured finding that 4 of the 5 reversals R0 covered and R1 did not had
their covering level **expire before the check**. If M4 does not move under S3, the reading is that
lifetime was not the binding constraint, and that is a result, not a failure of the implementation.

## 7. Per source measurement, required

For each family, measured on its own `Swing + F` variant against the `Swing only` variant:

1. **new major reversal coverage**: `M4` hits, and the reversals hit by `Swing + F` that
   `Swing only` misses, named case by case with the covering level's price, `sources` and
   `origin_kind` as published **at the check instant**.
2. **overlap with the existing swing book**: candidates of the family that merged into an existing
   level against candidates that created a new one, split by the frame of the incumbent.
3. **additional active level count**: `M2` publishable median, p95 and max, and the delta.
4. **duplicate increase**: `M3` `dup_set_share`, `dup_pairs_total`, `cross_tf_pairs`, and the delta.
5. **breakout count impact**: `M6` median and max per day, the totals, the split by source and
   confirming frame, and the delta.
6. **repaint**: `M1` on that variant, including R1's two monotonicity audits, with the prefix
   truncation test run on the final combination at R1's six cut points.

## 8. Required comparisons

Four variants are built and scored by the same metric code in the same process, exactly as R1 scored
R0 and R1 together:

| Variant | Families enabled |
|---|---|
| `SWING_ONLY` | none. Must equal R1, section 2.1 |
| `SWING_S1` | `D` |
| `SWING_S2` | `RANGE` |
| `SWING_S3` | `FLIP` |

The remaining four subsets of the lattice (`S1+S2`, `S1+S3`, `S2+S3`, `S1+S2+S3`) are **also
computed and reported**, as diagnostics. They do not feed the combination rule of section 9 and they
do not feed the verdict. Reported alongside: the `RANGE_EDGE` to `LEVEL` migration count for S2, and
the count of 1h frame breakouts that exist on that frame only because a level became `major` through
a new token.

## 9. The final combination rule, fixed before results

**Admission.** Family `F` is admitted if and only if, measured on the `Swing + F` variant alone:

1. it adds coverage: `M4 hits(Swing + F) > M4 hits(Swing only)`, and
2. it does not break what R1 bought, that is the `Swing + F` variant satisfies **all** of
   `M1 == 0`, `M2 publishable median <= 10`, `M2 publishable p95 <= 16`,
   `M3 dup_set_share <= 0.10`, `M5 == 1.0`, `M6 median breakouts per day <= 12`.

Every bound in condition 2 is R1's `B` bound on a non M4 axis, carried over unchanged. Nothing new
is introduced and nothing is chosen after a number is seen. Condition 2 deliberately says nothing
about `M4`'s own `B` bound, because a family that is required to reach `0.60` on its own could never
be admitted and the rule would be vacuous.

**Combination.** The final combination is `Swing` plus every admitted family, taken in the fixed
order `S1, S2, S3`. If no family is admitted, the final combination is `Swing only`.

**The verdict is read on the final combination**, scored once. No family is added or dropped after
that score exists, and no further variant is built.

**Why the best subset is not selected.** The full lattice is reported, so after the fact it may be
visible that some other subset scores better on M4. Choosing it would be selection on the verdict
metric itself with **27 reversals** in the sample, which is a multiple comparison dressed as a
result. It is therefore not done, and the additive rule above stands whatever the lattice shows.

**Repaint is a contract violation, not a trade off.** If any variant reports `M1 > 0`, that family's
source definition is not point-in-time safe. It is reported as a violation of this contract, the
family is not admitted, and if `M1 > 0` on the final combination the verdict is `A`.

## 10. Repaint and PIT contract

Unchanged from R1 section 7, extended only by the token declarations already made.

**Immutable, per level:** `id`, `tf`, `price`, `origin_kind`, `pivot_at`, `created_at`. `tf` remains
the **originating** frame forever. For a level created by a new family, `tf` is `1h`, since by
section 3.2 the family is an hourly scale object; `origin_kind` is what names the family, and
`sources` is what the metrics read.

**Mutable, declared:** `touches`, `last_touch_at`, `breaks`, `last_break`, `flips`, `state`,
`sources`, `merges`, a retest machine's `state`, and the three new per token expiry anchors
(`day_expiry`, the range episode flag, `flip_at`), each of which is written once when its token is
added and never revised.

**Audited, not asserted:** R1's two monotonicity rules are kept and both now cover the new tokens.
`sources` is append only over the five token set. `state` moves only along R1's declared graph. A
retest machine's state moves only along R1's table and never leaves a terminal state. Any violation
counts into M1. The prefix truncation test is re-run on the final combination at R1's six cut
points: the engine fed only the bars that closed at or before `T` must reproduce the full run's
published snapshot at `T`, field for field, including the whole published level list and the whole
open retest list.

## 11. Verdict rule, fixed before results

Evaluated in order, first match wins. **Every bound is R1's, unchanged, and the function that
evaluates them is R1's `metrics.verdict` imported, not reimplemented.** Only the name of the third
branch changes, to the name the task gives it.

- **A. NOT_USABLE** if `M1 > 0`, or `M2 publishable median > 15`, or `M4 < 0.40`, or `M5 < 1.0`.
- **B. USABLE_FOR_STRUCTURE_LAYER** if `M1 == 0` and `M2 publishable median <= 10` and
  `M2 publishable p95 <= 16` and `M3 dup_set_share <= 0.10` and `M4 >= 0.60` and `M5 == 1.0` and
  the median daily confirmed breakout count is `<= 12`.
- **C. SOURCE_COVERAGE_STILL_INSUFFICIENT** in every other case.

**The reading of C is pinned here, before results.** On C the result names every failing axis, and
the axes are not interchangeable:

- if the only failing axis is `M4`, C means what its name says: the three allowed families do not
  reach the coverage the gate asks for, and the next decision is whether to extend the source set,
  which is the user's.
- if a non M4 axis also fails, that is **not** a coverage statement. It is the new sources spending
  more density, duplication or events than the budget allows, and it is reported as a **budget**
  failure under that name. Relabelling it as missing coverage after the fact is exactly the move
  this contract forbids.

**Next step by verdict, as the task sets it.** On `B`, the next step is combining this layer with the
Liquidity Map. On `A` or `C`, Price Structure as a standalone study **stops**, and whether to extend
the structural source set is a user decision. In every case revisions are **proposed, not applied**,
and nothing is committed, pushed or deployed.

No threshold in this document may be changed once a result has been seen.

## 12. Prohibitions carried from the task

- No threshold sweep, before or after results. No ladder of any kind.
- No arbitrary lifetime adjustment. The 24 hour 5m lifetime stays 24 hours and the 48 hour question
  R1 raised is **not** reopened; every new lifetime in section 3.4 is either definitional or an
  existing frozen constant.
- No RSI, MACD or any oscillator.
- No liquidity map, order book or order flow coupling of any kind.
- No C1 or C1x.
- No directional probability. No forward return is computed at all.
- No volume profile, order block, fair value gap, or any level source outside sections 4, 5 and 6.
- **No source is added after a result is seen.** The three families are the three in this document.
- No change to AUTO, PAPER or LIVE. No production change, no commit, no push. The preview is an
  isolated static artifact that no trading screen imports.

## 13. What R2 does not attempt

- It does not revisit curation. R1's three changes are kept exactly as frozen.
- One symbol, BTCUSDT. One 33 day window, 2026-09-01 to 2026-10-04. One regime, which ended in a
  strong rally. No claim about any other symbol or period.
- 27 major reversals is the whole M4 sample. A single case is 3.7 percentage points of coverage, and
  no difference in this study is claimed to be more precise than that.
- No edge claim of any kind. This layer describes structure; it does not predict and it is never
  measured against returns.
