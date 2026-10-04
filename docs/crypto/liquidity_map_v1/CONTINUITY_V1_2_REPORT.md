# Liquidity Map V1.2: HARD / SOFT resync continuity - report

Date: 2026-10-04 (KST).
New frozen rule `lm-continuity.v1`, in `WALL_CONTINUITY_V1_2.md`
(sha256 `d68a26ce2a170d5549f5e8bec5db02b8fc91d7bfd489f31d97e39653a0148656`).
**Superseded the same day by `lm-continuity.v2`** (sha256 `596339b6…`), which narrowed the SOFT
window from 1000 ms to 300 ms and stated that a journal reconstruction carries nothing. Where
this report says 1000 ms below, it is describing v1 as it was measured; `GATE_V1_2_REPORT.md`
carries the change, its reason and the re-measurement.
Wall selection rule `lm-wall.v2` **unchanged**, sha256 still `deaa9db8...a9d87`.
Data contract `btc-ms.v0.1` **unchanged**, sha256 still `9eed3862...d52f`.

**Status: isolated preview only. Not deployed, not committed, not linked from navigation.**

---

## 0. What changed, and what did not

Every file this release touches is **untracked** in git. `git ls-files` over the two packages,
their tests, their docs and the three frontend files returns **0 rows**, so no tracked file in
the repository was modified and "no effect on the trading service" is a structural fact rather
than a claim. (The modified tracked files visible in `git status` belong to a concurrent session
working on `crypto/live`, `crypto/terminal` and `crypto/paper`; none was touched here.)

| Area | Change |
|---|---|
| `market_structure_v0/walls.py` | continuity ledger: `BookWindow`, `classify_refresh`, `SoftRefreshProof`, per-candidate carry, `continuity_view()` |
| `market_structure_v0/collector.py` | classifies every snapshot install, hands the verdict to the ledger, publishes `wall_continuity` telemetry and a `continuity` section in the state file |
| `market_structure_v0/book.py` | **not touched** |
| `liquidity_map/continuity.py` | **new** - the frozen `lm-continuity.v1` rule, its hash check, the bin-level identity decision |
| `liquidity_map/wallrule.py` | R4 takes the longer of two spans; thresholds unchanged; the continuity rule is injected, not imported |
| `liquidity_map/checkpoint.py`, `wallstate.py`, `view.py` | read and publish the ledger |
| frontend | `ContinuityPanel`, a carried marker per wall row, new reason and gate labels |

Nothing outside the two packages imports either of them. The collector still cannot import the
viewer (`test_liquidity_map_isolation.py` enforces it), so the proof is produced where the
evidence lives and consumed where the display rule lives.

---

## 1. The HARD / SOFT contract

### HARD: persistence always ends

A resync is HARD when the stream itself was interrupted, so the collector was blind for an
interval it cannot bound from the data. Exhaustively: any websocket gap (`GAP_FIRST_DELTA`,
`GAP_PU_MISMATCH`, `GAP_NON_INCREASING`, `GAP_MALFORMED_IDS`), a disconnect or reconnect, a stale
depth connection, a crossed book, a level or prefix-buffer overflow, a rejected or malformed
snapshot, any other sequence or continuity failure, and every session boundary.

**HARD is also the default.** A transition that is not positively proved SOFT is HARD. In the
code this is one line: `_close_all()` sets `pending_proof = None`, so every fault path that ends
candidates also destroys the right to carry across it, without each path having to remember to
say so.

### SOFT: persistence may be carried, if proved

Only the two V1.1 voluntary resnapshots can be SOFT - the coverage-edge refresh and the hourly
safety refresh. All five gates must hold at the moment the snapshot is installed:

| Gate | What it requires |
|---|---|
| **S1 VOLUNTARY** | requested by the policy, with the book `SYNCED` and `first_delta_applied` at request time |
| **S2 CONTINUITY** | no new gap, connect, reconnect, stale event, buffer overflow or snapshot rejection between request and install; book `SYNCED` afterwards with no invalidation; generation moved by exactly one |
| **S3 NEWER** | `snapshot.lastUpdateId > book.last_update_id` |
| **S4 BOUNDED WINDOW** | REST request-to-install at most one sample interval (1000 ms in v1; **300 ms in v2**) |
| **S5 OVERLAP** | the two known intervals overlap non-degenerately and the overlap contains both mids, with at least one comparable level |

The published reason is the **first** gate that failed, which is the one an operator can act on.

### Per-candidate verdicts at a SOFT refresh

| Condition | Verdict |
|---|---|
| price inside **both** known intervals, and the installed snapshot holds that exact side and price with qty > 0 | **eligible** |
| price inside both intervals, and the snapshot does not hold it | **ENDED** - observed to be gone |
| price outside the new known interval | **UNKNOWN** - unobservable, never ENDED |

An eligible candidate becomes **CARRIED** only if it also re-qualifies under the V0 candidate
rule on the first sample of the new generation. Eligible but not re-qualifying is **ENDED**: it
is in the book and is no longer a candidate, which is an observation.

### What the journal does, which is nothing new

`btc-ms.v0.1` says a resync ends wall continuity as UNKNOWN, and it still does. At every
generation change, SOFT included, each held candidate is written `status=UNKNOWN` and each
re-qualifying candidate gets a fresh `OPENED` row with its own `first_seen_ms` and
`persistence_ms = 0`. A test asserts the `wall` payload's key set is exactly the contract's
list, so the ledger cannot leak into the dataset.

The carry travels in two other places, both additive:

* the compact state checkpoint (`ms-v0-state.v1-1` → **`v1-2`**), as a `continuity` section plus
  six `continuity_*` fields per wall row. `v1-1` is still read and simply carries no ledger.
* a `telemetry` record (an existing contract kind with an open event vocabulary), event
  `wall_continuity`, in two phases: `CLASSIFIED` at install (gates, overlap, window) and
  `APPLIED` at the first sample of the new generation (the counts and the carried identities,
  bounded at 2,000 with a `truncated` flag). **The carry is therefore auditable from the journal
  alone**, which the state file is not, since it is a cache and says so.

---

## 2. How continuity is proved

The collector is the only process that holds the websocket chain and the book, so it is the only
one that can prove anything about them. It produces the proof; the viewer's frozen rule decides
what a wall may do with it. Two layers:

**Member level (collector).** Identity is `(side, exact price)`. At install time the collector
takes a copy of the book before `apply_snapshot` and another after, and classifies. The
post-install copy is what "present in the refreshed snapshot" is tested against - at the install
instant, not a second later - so a level that vanished and returned within the same second cannot
pass by being back in time for the next sample.

**Wall level (viewer).** `lm-wall.v2` groups members into 5.0 USDT bins and a wall *is* the bin.
A wall carries **iff at least one of its members is a carried member**, and its origin is the
earliest among them. Matching on `(side, bin)` alone was considered and rejected in the frozen
document: a bin whose old member vanished and whose new member sits at a different price inside
the same 5 USDT still matches on side and bin, and carrying that would be an identity claim about
an order never seen resting. Requiring a carried member makes "same side, same frozen V2 bin"
true **by construction**, because a carried member has the same exact price.

**The honest limit, stated rather than discovered later.** The events between
`book.last_update_id` and `snapshot.lastUpdateId` are not individually enumerated; their *result*
is in the snapshot. A level cancelled and re-placed at the same price inside that window is
indistinguishable from one that rested. That is why S4 bounds the window at one sample interval:
V0 already discloses exactly this for its 1 s sampling, so a SOFT carry **adds no new class of
unprovable claim**. Measured live, the window was 87, 99, 102, 119 and 143 ms. `order_identity_proven`
stays `false` on every wall, carried or not.

The overlap check is published in full (bounds, overlap, both mids, levels compared, identical,
changed, only-before, only-after) but **only the interval gates**. Level agreement is evidence,
not a threshold: the snapshot is newer than the deltas already applied, so levels inside the
overlap are allowed to differ, and a cutoff on how many agreed would be a number nobody could
justify. The screen says this in so many words.

---

## 3. Persistence before and after

### Live, across a real refresh (session `8e87c03b`, forced coverage-edge and safety refreshes)

Polled once a second through the preview API. `rescued` counts walls on the ladder whose own span
is under R4's 10,000 ms and which are there only because of the carry - that is, **the walls V1.1
would have dropped**.

```
gen 3  soft 2   selected 31  shown 11   rescued 0    <- steady state
gen 3  soft 2   selected 32  shown 11   rescued 0
gen 4  soft 3   selected 33  shown 12   rescued 12   <- refresh: every wall on screen is carried
gen 4  soft 3   selected 33  shown 12   rescued 12
gen 4  soft 3   selected 32  shown 12   rescued 12
   ... 10 s ...
gen 4  soft 3   selected 33  shown 10   rescued 0    <- own spans cross 10 s, carry no longer needed
gen 5  soft 4   selected 30  shown 10   rescued 10   <- next refresh, same shape
gen 5  soft 4   selected 25  shown  5   rescued 5
```

**Without V1.2 the ladder is empty for the 10 seconds after every SOFT refresh.** With it,
`walls_selected` stays flat across the transition (31 → 33 → 33) instead of collapsing.

### The same thing in one candidate (unit test)

Identical payload, differing only in the carry proof: own span 2,000 ms, carried span 300,000 ms.
Without the ledger it is refused with `BELOW_MIN_PERSISTENCE`; with it, it is selected and
publishes `observed 300,000 / own 2,000 / carried 300,000 / source CARRIED`.

### Repeated refreshes no longer cap how long a wall can look

Five refreshes 20 s apart in a driven collector:

| after refresh | candidate's own `persistence_ms` | proven `continuity_persistence_ms` |
|---|---|---|
| 1 | 0 | 2,000 |
| 2 | 0 | 22,000 |
| 3 | 0 | 42,000 |
| 4 | 0 | 62,000 |
| 5 | 0 | 82,000 |

The own span is reset by every refresh, exactly as the contract says. The proven span is not.

### Live transition counts

| Session | Trigger | Window | Before | Carried | Ended | Unknown | Overlap |
|---|---|---|---|---|---|---|---|
| `aef0cb88` | coverage_edge | 87 ms | 266 | 231 | 27 | 8 | 1,920 compared, 1,885 identical |
| `aef0cb88` | safety_refresh | 119 ms | 268 | 256 | 8 | 4 | 1,968 compared, 1,966 identical |
| `42af08e5` | safety_refresh | 102 ms | 286 | 252 | 8 | 26 | 1,920 compared, 1,918 identical |
| `8e87c03b` | coverage_edge | 87 ms | 279 | 274 | 3 | 2 | 2,000 compared, 1,992 identical |

Every row sums: carried + ended + unknown = candidates before. Carry rates of 86% to 98% on a
quiet book; the UNKNOWN column is the price of the interval moving, and it is never reported as
an ending.

**A live refusal, unforced.** In session `42af08e5` the coverage-edge refresh at t+39.8 s was
refused on arrival with `NOT_NEWER_THAN_LIVE_BOOK` - V1.1's protection firing against a real
Binance REST read that was behind the applied deltas. The healthy book was kept, no generation
moved, no wall history was lost, and no transition was recorded at all.

---

## 4. Refresh telemetry

Every generation transition publishes, in the journal and in the state checkpoint:

* `refresh_type`: `HARD` | `SOFT`
* `continuity_reason`: the gate that decided it, or `SOFT_ALL_GATES_PASSED`
* `cause`: on a HARD interruption, the book's own invalidation word (`RECONNECT`, `STALE`, a gap
  reason, `shutdown`) rather than a second vocabulary invented for it
* `overlap_check`: both intervals, the overlap, both mids, and five level counts
* `wall_carried`, `wall_ended`, `wall_unknown`, which sum to `candidates_before`
* `carried_entering` and `carried_lost`
* `gates` (all five, each with its verdict), `window_ms`, `refresh_trigger`
* the carried identities, bounded at 2,000 with `carried_truncated`

Running totals live in the state file: `soft_refreshes`, `hard_transitions`, `wall_carried_total`,
`wall_ended_total`, `wall_unknown_total`, `proofs_superseded`, `active_carried`.

**`carried_lost` was wrong in the first implementation and the live run caught it.** It counted
every candidate that arrived already carried, so a healthy SOFT refresh that renewed 113 carries
reported 113 as lost, and the screen's warning fired on every refresh. It now counts only carried
candidates that did **not** carry through: all of them on HARD, and zero on a healthy SOFT. A
warning that fires every time is a warning an operator learns to ignore.

### The screen

A `관측 연속성 (HARD / SOFT)` panel, rendered and measured at 390 px and 1440 px with
**0 px horizontal overflow** at both:

* the last transition's three counts **drawn together** with the number they must add up to, so
  a reader cannot see the carries without seeing what was not carried;
* the five gates as gates, pass or fail, **in the frozen rule's order** - taken from the
  published `gates_required`, because the state file is canonical JSON with sorted keys and
  iterating the object drew them alphabetically;
* the overlap check with the sentence saying the level counts are not a gate;
* `carried_lost` and `proofs_superseded` as warnings only when non-zero;
* a red banner if the frozen document's hash stops agreeing;
* `이어받음` on each carried wall row, whose tooltip carries both spans and the line that this is
  not proof of order identity;
* the dash and the reason when the collector publishes no ledger (a `v1-1` checkpoint or a
  journal reconstruction) - which is also exactly how V1.1 behaved.

---

## 5. Tests

**Backend 422 passed** (`test_ms_v0_*.py` + `test_liquidity_map_*.py`), of which 54 are in the two new continuity files.
**Frontend 91 passed**, of which 13 are new.

| Scenario asked for | Test |
|---|---|
| wall persistence survives a soft refresh | `test_a_wall_that_was_in_the_refreshed_snapshot_keeps_its_first_seen_and_its_span`, and end to end on a real journal |
| a wall gone after a soft refresh ends | `test_a_wall_that_is_gone_from_the_refreshed_snapshot_ends_rather_than_carrying`, `test_a_level_still_present_but_no_longer_a_candidate_ends_rather_than_carrying` |
| a hard gap never keeps persistence | `test_a_gap_ends_every_wall_and_the_next_snapshot_carries_nothing`, `test_a_gap_between_a_clean_refresh_and_the_next_sample_voids_the_proof` |
| reconnect never keeps persistence | `test_a_reconnect_carries_nothing_even_if_the_book_comes_back_identical` |
| snapshot mismatch fails closed | `test_a_snapshot_whose_interval_has_left_the_market_fails_closed`, `test_a_rest_read_slower_than_one_sample_interval_is_hard`, `test_a_refresh_of_a_book_that_never_applied_a_delta_is_hard`, `test_a_recovery_snapshot_is_hard_however_clean_it_looks` |
| restart recovery | `test_a_restart_cannot_inherit_a_carry_from_the_process_that_died` |
| repeated coverage-edge refreshes | `test_repeated_coverage_edge_refreshes_no_longer_cap_how_long_a_wall_can_look`, `test_a_carry_can_be_repeated_across_several_soft_refreshes` |
| 390 px and existing preview regression | `390px layout` suite with the new panel added; live render at 390/1440 px with 0 px overflow |

Each of the five gates is also broken **on its own**, with the other four asserted to still hold,
because a gate only ever tested while the others pass is a gate nobody has tested. Beyond the
asked-for list: the journal payload's key set is pinned; a status the reader does not recognize
is not a carry (`"carried"`, `"CARRIED_SOFT"`, `True`, `1`, `None`); a carry cannot rescue a
candidate failing R1, R2 or R3; a carried span can only extend R4's input, never shorten it; two
installs between two samples supersede each other and carry nothing; a stale book at the sample
carries nothing even after a clean refresh; `lm-wall.v2`'s own hash is re-asserted unchanged.

---

## 6. Production readiness

**Not yet.** The V1.1 blockers that remain, plus what V1.2 adds.

1. **Everything here was measured on a quiet book.** Carry rates of 86-98% come from four
   sessions of 4 to 5 minutes each in a calm market. A funding print, a liquidation cascade or a
   fast trend will move the interval far more between refreshes, which raises the UNKNOWN count
   and lowers the carry rate. The direction is known; the magnitude is not.
2. **The coverage-edge trigger was forced, not awaited.** A live coverage-edge refresh fired on
   its own in V1.1 (margin 0.62 bp, 130 ms). Here the refreshes were requested by a measurement
   harness outside the repo that sets `refresh_wanted` the way the policy does; everything
   downstream - the REST read, the refusal check, `apply_snapshot`, the five gates - is the
   shipped path unmodified. The **safety refresh has still never fired on its own**, because no
   session has run an uninterrupted hour.
3. **No live HARD resync was observed.** V0 has never recorded a real gap in any run, so the HARD
   paths remain proved by test and by the shutdown interruption only. This is the same
   outstanding item V0 carried into its 24 h trial.
4. **The 300 s cooldown is now a smaller cost, not a non-issue.** A wall history survives a SOFT
   refresh, so the cooldown no longer caps how long a wall can be seen to rest. It still caps it
   on a HARD resync, and a book that faults every few minutes still shows nothing older than the
   last fault. **User decision B from V1.1 is unchanged in kind and smaller in size.**
5. **The preview still renders inside the app shell**, which a concurrent session is editing, so
   `app-shell.tsx` was not touched.
6. **One unexplained artifact, not from these code paths.** The first harness run left a
   `storage_stats` file belonging to a second session id in its output root and failed to seal
   one file at exit. It did not recur in three later runs, the shipped CLI (`--duration 25`)
   exits 0 with clean seals, and the failure is in `store.py`, which this release does not touch.
   It is recorded here because a second writer in one journal root would be a data-integrity
   problem if it is real, and it deserves its own look rather than a guess.

### User decisions

* **A.** ~~Whether the rule should apply to the journal reconstruction fallback.~~ **Decided:
  it must not.** A reconstructed set carries nothing, and rebuilding the carry from the
  `wall_continuity` telemetry stream is refused. Written into `lm-continuity.v2`.
* **B.** ~~Whether the SOFT window ceiling of 1000 ms is right.~~ **Decided: 300 ms.** Written
  into `lm-continuity.v2`.
* **C.** Whether to re-measure the V2 distribution and the carry rate in a volatile session
  before anything is promoted (this is V1.1's decision D, now with a second quantity to measure).
* **D.** V1.1's decisions A (operational UI placement) and C (R3's 1 bp blind spot) are
  unaffected and still open.
