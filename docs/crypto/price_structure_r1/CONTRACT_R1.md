# US-B CRYPTO Price Structure R1, Level Curation, Contract

Status: **FROZEN BEFORE ANY R1 RESULT IS COMPUTED**
Frozen at: 2026-10-04 (Asia/Seoul)
Parent: `docs/crypto/price_structure_r0/CONTRACT_V2.md`
sha256 `d41fb4f5cd6d28f48a309c3122b9eb5afc1bb45a0f430b009eacb72ea2c0bb22`
Scope: research and an isolated preview only. C1 / C1x, AUTO, PAPER, LIVE, the order paths, the
production deploy path, the liquidity map and the order flow layers are all out of scope and
unchanged. Nothing here is committed or deployed.

R0 returned **A_NOT_USABLE** with the mechanism intact: M1 repaint 0, prefix truncation identical at
6 of 6 cut points, raw re-derivation 60/60 on swings, breakouts and sweeps, M5 liveness 1.0. What
failed was curation: M2 median 21 active levels, M4 major reversal coverage 0.444, M6 median 18
confirmed breakouts per day.

R1 asks one question and changes exactly three things to ask it.

## 1. Question

Can the R0 level set be curated into a structure a person can read, **without adding any new level
source**?

"Readable" is not a feeling. It is the pre-registered numbers of section 8 against the bounds of
section 9, both carried over from R0 so that R1 is scored on the same ruler.

## 2. Carried over unchanged, by reference

The following are **not re-derived, not re-tuned and not re-implemented**. R1 imports the R0 modules
so that this is enforced by the code and not only by assertion:

| Carried | Where it lives in R1 |
|---|---|
| Data, the 1m source, the ATR definition, warmup and replay windows, the D2 crosscheck | imports `price_structure_r0.data` |
| Swings, HH/HL/LH/LL, `trend_structure`, range geometry | imports `price_structure_r0.swings` |
| Session VWAP and `vwap_position` | imports `price_structure_r0.vwap` |
| Swing threshold calibration and its held out warmup window | imports `price_structure_r0.calibrate`; `theta_1h = 3.0`, `theta_5m = 6.0` are reused, **not recalibrated** |
| `buffer(p, tf)`, the two close breakout rule, `rebreak_cooldown = 24`, the side rule of R0 section 17 | imports `price_structure_r0.events` for `buffer`, `Breakout`, `Sweep`, `RangeEdge` |
| Sweep definition, failed break definition and decay | unchanged |
| Touch definition, `tol_touch = 0.15 * ATR_5m`, `touch_cooldown = 6` 5m bars | unchanged |
| Role derivation, `tol_role = 0.15 * ATR_5m` | unchanged |
| `MAX_PUBLISHED = 10`, at most 5 per side, the ranking key | unchanged |
| 1h level expiry at 10 days, the far-and-untouched expiry | unchanged |
| The 1m decision grid, the frame order 1h then 5m then publication | unchanged |
| PIT rule: a bar is known at its close and not one instant earlier | unchanged |
| Retest geometry: 48 bars of the level's confirming frame, the touch test, the two close hold test, the two close failure test | unchanged |

**The 5m swing threshold is deliberately left alone.** R0's calibration showed no rung of the ladder
reaches 8 swings per day, and R0's own M4 diagnostic showed 5m levels are where the coverage comes
from, so raising theta would cost coverage and buy little density. No theta is swept in R1, before or
after results.

## 3. Change 1, symmetric cross timeframe merge

**R0's defect, measured.** R0 section 8 dropped a 5m candidate that landed near an ACTIVE 1h level
and said nothing about the reverse. A 1h bar's high **is** the maximum of its twelve 5m highs, so the
5m swing confirms first and the later 1h candidate is created straight on top of it. R0 measured
58.9% of hourly published sets carrying at least one near duplicate pair, 364 of 586 pairs being a 1h
level stacked on a 5m level.

**R1 rule.** A candidate of either frame that lands in the same structural zone as an existing alive
level **merges into that level's identity**, whatever the order of arrival.

- Same frame pair: zone is `|p_new - p_incumbent| <= 0.25 * ATR_tf` of that frame. Unchanged from R0.
- Cross frame pair, 5m against 1h in either direction: zone is `|p_new - p_incumbent| <= 0.25 *
  ATR_5m`. The tolerance is a property of the pair and not of who arrived first, which is what makes
  the rule symmetric. `0.25 * ATR_5m` is R0's own cross frame tolerance, carried over so the zone is
  not widened.
- On merge the **candidate is discarded**, the incumbent's `touches` is incremented, and the
  incumbent's `price`, `id`, `tf`, `pivot_at` and `created_at` are **never recomputed**. A drawn line
  still never moves, at any time, for any reason. This is the R0 non repaint guarantee and it is not
  weakened.
- The asymmetric suppression of R0 section 8 is **removed**. A 5m candidate near a 1h level is now
  merged, not silently dropped, so it leaves a logged `touches` increment instead of nothing.

**Provenance is preserved, not overwritten.** Each level carries `sources`, an append only set over
{`5m`, `1h`}, seeded from the frame of the candidate that created it and extended on every merge.
`provenance` is derived: `5m`, `1h`, or `MULTI_TF` when both are present. The immutable `tf` field
stays the **originating** frame forever, so the R0 immutability list is untouched and the before and
after duplicate measurement is computed on the same field in both runs.

**One identity means one confirming frame.** A level's `confirm_tf` is the slowest frame in
`sources`: `1h` once a 1h source is present, else `5m`. Breakouts, sweeps and the retest window of a
`MULTI_TF` level are therefore evaluated on the 1h frame. This follows from R0 section 9, which
already said a major level is a 1h object and needs a 1h close to be considered broken; it is stated
here because it is a consequence of merging identities and it will move M6. Its effect is reported
separately in section 8 so it cannot hide inside the headline.

**A 1h source promotes.** When a 1h source is added to a 5m level, the level leaves the lifecycle of
section 4 at once and is treated as a 1h level from that moment: it becomes `CONFIRMED`, it takes the
1h expiry, and it takes the 1h strength bonus. A logged transition records it.

## 4. Change 2, the 5m level lifecycle

**No theta sweep, by construction.** The lever is lifetime and observed reaction to price, exactly as
R0's proposal 2 said, and the thresholds below are **single pre-registered values, not ladders**.
They are taken from R0's own frozen 48 hour 5m lifetime, halved and quartered, so that no new number
is invented and nothing is chosen by looking at an R1 result.

A level whose `sources` are `{5m}` alone has a lifecycle. States, with the names from the task:

| State | Entered when | Published | Takes part in breakouts, sweeps, retests |
|---|---|---|---|
| `PROVISIONAL` (created) | at creation | **no** | **no** |
| `CONFIRMED` (touched) | first touch on a 5m bar **strictly after** the creation bar | yes | yes |
| `WEAKENED` | `now - last_touch_at > 12h` while `CONFIRMED` | yes, ranked lower | yes |
| `EXPIRED` | section 4.1 | no | no |

- A touch while `WEAKENED` returns the level to `CONFIRMED`, logged. That is the "reacts to real
  touches" test: a level that keeps getting hit keeps its standing, a level nothing trades at loses
  it and then dies.
- A touch on the **same 5m bar as creation** does not confirm. Confirmation needs a strictly later
  bar, which removes a degenerate self confirmation path.
- Only a touch confirms. A merge does not, even though it increments `touches`: a merge is a second
  swing at the same price, and the price action that produced it is already eligible to raise a touch
  through the ordinary touch scan.
- A `PROVISIONAL` level is invisible and inert. It raises no breakout, no sweep and no retest
  machine, because an event against a line nobody has been shown is not an event anybody can read.
  It can be touched, merged into, and expired, and nothing else.
- Levels whose `sources` contain `1h` are born `CONFIRMED` and have no `PROVISIONAL` state. The 1h
  frame already satisfies R0's sparsity target at `theta_1h = 3.0` (1.00 per day on warmup).

### 4.1 Expiry, each logged

1. `sources` contains `1h`: `now - max(created_at, last_touch_at) > 10 days`. Unchanged from R0.
2. `{5m}` and `PROVISIONAL`: `now - created_at > 12h`, reason `PROVISIONAL_UNTOUCHED`. A level
   nothing came back to within half a day was never a level.
3. `{5m}` and `CONFIRMED` or `WEAKENED`: `now - max(created_at, last_touch_at) > 24h`, reason
   `AGE_24H`. This **replaces** R0's 48 hour rule for 5m levels and is the only carried threshold R1
   changes.
4. Any level: `|close - price| > 10 * ATR_1h` and untouched for 24h and older than 24h. Unchanged
   from R0.

### 4.2 Strength

`strength = touches + (2 if "1h" in sources else 0) + (1 if touched within 24h else 0) - (1 if
WEAKENED else 0)`. The 1h bonus now reaches merged levels through `sources`, which is the point of
preserving provenance. The `WEAKENED` penalty is the only new term and it is declared here.

## 5. Change 3, the per level retest state machine

**R0's defect, measured.** Exactly one machine was alive at a time, so 393 of 580 breakouts, 68%,
terminated as `SUPERSEDED` and `retest_state` was a statement about whichever level broke most
recently rather than about a level a person is watching.

**R1 rule.** One machine per confirmed breakout, all of them live at the same time. `SUPERSEDED` is
**deleted from the state space**. States, with the names from the task and their R0 equivalents:

| R1 state | Entered when | R0 equivalent | Terminal |
|---|---|---|---|
| `BROKEN` | at confirmation | `AWAITING_RETEST` | no |
| `RETEST_PENDING` | price returns into the buffer: `low_5m <= p + b` for an UP break, mirrored for DOWN | `RETEST_TOUCHED` | no |
| `RETESTED` | after `RETEST_PENDING`, two consecutive 5m closes back on the breakout side of `p ± b` | `RETEST_HELD` | yes |
| `HELD` | the window elapses while still `BROKEN`: the break held and price never came back | `NO_RETEST` | yes |
| `FAILED` | at any point from confirmation on, two consecutive 5m closes through `p ∓ b` against the break | `RETEST_FAILED` | yes |
| `EXPIRED` | the window elapses while `RETEST_PENDING` (reason `UNDECIDED`), or the level expires (`LEVEL_EXPIRED`), or the same level breaks again (`RELEVEL`) | `RETEST_INCONCLUSIVE`, plus the only remaining displacement path | yes |

Every comparison, every counter and the 48 bar window are the R0 ones. What changes is **scope**: the
machine is attached to its own level instead of to a single global slot.

**Bounding the pool without reintroducing displacement.** At most one non terminal machine per
`level_id`. A new confirmed breakout of the **same** level terminates that level's open machine as
`EXPIRED` with reason `RELEVEL`; a level leaving the book terminates its machine as `EXPIRED` with
reason `LEVEL_EXPIRED`. Machines of different levels never interfere, so the pool is bounded by the
number of publishable levels, which is itself bounded by M2. The observed maximum concurrency is
reported.

**Evaluation frame.** Each machine is evaluated on 5m closes, as in R0. Its window is 48 bars of its
level's `confirm_tf`.

**Publication.** The snapshot gains `retests_open`, a count, and `retests`, the up to five machines
nearest to price by `|level_price - close|`, each with its level id, price, direction, state and
minutes left. `retest_state` is retained as a scalar for comparability and is defined as **the state
of the machine belonging to the most recent confirmed breakout**, which is no longer the same claim
as R0's global slot and is labelled as such in the preview. `failed_break_state` is unchanged and is
driven by the most recent `FAILED`.

## 6. Publication

Unchanged from R0 section 8: `nearest_resistance`, `nearest_support`, and the top `MAX_PUBLISHED =
10` by strength then distance, at most 5 per side. The candidate pool is now the **publishable** set,
`CONFIRMED` plus `WEAKENED`, which excludes `PROVISIONAL`. The published view of a level gains
`provenance`, `lifecycle` and `sources`, and keeps the immutable `tf` as the originating frame.

## 7. Repaint and PIT contract

The R0 contract is kept as written. Two declarations are added because R1 adds state.

**Immutable, per level, unchanged:** `id`, `tf`, `price`, `origin_kind`, `pivot_at`, `created_at`.
Any change to one of these is an M1 violation, as is the disappearance of a level without a logged
`EXPIRED`.

**Mutable, declared:** `touches`, `last_touch_at`, `breaks`, `last_break`, `flips`, `state`,
`sources`, and a retest machine's `state`. Two monotonicity rules are added and are **audited**, not
asserted:

1. `sources` is append only. A level that was ever seen with a source never loses it.
2. `state` moves only along the declared graph
   `PROVISIONAL -> CONFIRMED -> WEAKENED -> CONFIRMED`, any of them `-> EXPIRED`, plus the promotion
   `PROVISIONAL -> CONFIRMED` on a 1h merge. `EXPIRED` is terminal. A retest machine's state moves
   only along section 5's table and never leaves a terminal state.

Any violation of 1 or 2 counts into M1. No future data, no hindsight pivot, and the prefix truncation
test of R0 section 13 is re-run at the same six cut points: the engine fed only the bars that closed
at or before T must reproduce the full run's published snapshot at T, field for field, including the
whole published level list and the whole retest list.

## 8. Pre-registered evaluation

All metrics are computed on the replay window, 2026-09-01T00:00Z to 2026-10-04T00:00Z, on the 1m
grid, exactly as in R0.

**Both engines are measured by the same code in the same process.** The R0 engine is re-run from the
same bars and scored with the identical metric functions, so every before and after number in the
results is a re-measurement and not a quotation of the R0 document.

- **M1 `repaint_violations`**, as section 7, plus the two new monotonicity audits and the prefix
  truncation test. Target 0.
- **M2 active level count** across snapshots: median, p95, max. Reported twice and the distinction is
  declared here, before any number exists:
  - `publishable` = `CONFIRMED` plus `WEAKENED`, the lines that exist for a reader. **The verdict
    uses this one.**
  - `book` = every non expired level including `PROVISIONAL`. Reported alongside so that a drop in
    the headline cannot be mistaken for a bookkeeping trick.
  R0 has no `PROVISIONAL` state, so for R0 the two numbers are identical and equal to its M2.
- **M3 duplicate density**, newly instrumented in code and applied to both runs:
  - for each hourly published snapshot, every unordered pair of published levels closer than
    `0.25 * ATR_5m` of that snapshot is a near duplicate pair
  - `dup_set_share` = share of hourly snapshots containing at least one such pair
  - `dup_pairs_total` = the sum of such pairs over snapshots
  - `cross_tf_pairs` = of those, the pairs whose immutable `tf` differs, which is R0's "1h stacked on
    5m" count
- **M4 major reversal coverage**, definition unchanged: a confirmed 1h swing in the replay window at
  least 2.0% from the previous confirmed opposite 1h swing, checked at the close of the 1h bar
  immediately preceding the pivot bar, hit when the **published** set holds a level within 0.25% of
  the pivot price. Reported with three diagnostics that do **not** feed the verdict:
  - coverage against 1h sourced published levels only
  - coverage against the whole alive book including `PROVISIONAL`, which separates "the level was
    never created" from "the level existed but was not published"
  - the hit and miss move size split
- **M5 retest liveness**: of the machines whose window elapsed before the replay end, the fraction in
  a terminal state. Target 1.0. Reported with `resolved_share`, the fraction of all machines ending
  in `RETESTED`, `FAILED` or `HELD` rather than `EXPIRED`, which is the direct before and after of
  R0's 68% `SUPERSEDED`, and with the maximum observed concurrency.
- **M6 confirmed breakouts per UTC day**: median and max, with the breakdown by `confirm_tf` and by
  source, plus the count attributable to merged `MULTI_TF` levels having moved to the 1h confirming
  frame, so that the effect declared in section 3 is visible separately.

Reported but not verdict bearing: `SUPERSEDED` to `EXPIRED/RELEVEL` comparison, lifecycle census
(how many 5m levels never left `PROVISIONAL`), M7 state distributions, and the publication cap check.

## 9. Verdict rule, fixed before results

Evaluated in order, first match wins. Every bound is R0's, except M3 which had no bound in R0 and is
set here before it is measured.

- **A. NOT_USABLE** if `M1 > 0`, or `M2 publishable median > 15`, or `M4 < 0.40`, or `M5 < 1.0`.
- **B. USABLE_FOR_STRUCTURE_LAYER** if `M1 == 0` and `M2 publishable median <= 10` and
  `M2 publishable p95 <= 16` and `M3 dup_set_share <= 0.10` and `M4 >= 0.60` and `M5 == 1.0` and the
  median daily confirmed breakout count is `<= 12`.
- **C. NEEDS_NEW_LEVEL_SOURCE** in every other case.

Because C carries an instruction, its reading is pinned here too. On C the result names every failing
axis. The task's follow up, freeze the R1 contract and study only the missing level source in R2,
**follows only when M4 is the sole failing axis**. If a non M4 axis also fails, that is a remaining
curation defect and is reported as one, not laundered into a missing source.

No threshold in this document may be changed once a result has been seen. If the outcome is A or C,
revisions are **proposed, not applied**.

## 10. Prohibitions carried from the task

No new S/R source. No liquidity map or order flow coupling. No RSI, MACD or any oscillator. No C1 or
C1x. No directional probability, and no forward return is computed at all. No threshold sweep after
results. No change to AUTO, PAPER or LIVE. No production change, no commit, no push. The preview is
an isolated static artifact that no trading screen imports.

## 11. What R1 does not attempt

- It adds no level source, so nothing here is expected to fix M4 on its own. R0 measured that 13 of
  15 misses sat inside already traded price, and the only R1 mechanism that could touch M4 is freeing
  publication slots.
- One symbol, one 33 day window, one regime. No claim about other symbols or periods.
- No edge claim of any kind. This layer describes; it does not predict and is not measured against
  returns.
