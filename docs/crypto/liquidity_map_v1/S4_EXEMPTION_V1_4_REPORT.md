# Liquidity Map V1.4: the S4 window exemption for a replayed chain

Date: 2026-10-04 (KST). Status: **verified, not deployed.** No production deploy, no push.
Rule: **`lm-continuity.v4`**, frozen before any V1.4 refresh was observed.
Document `docs/crypto/liquidity_map_v1/WALL_CONTINUITY_V1_2.md`,
sha256 `0ed46edcc0a58957b99cc14bf5b8bba92a4111eb94026b7353311042e58327b3`.

## What changed, in one paragraph

V1.3 proved that a staged refresh contains no unenumerated event: every frame between the
snapshot and the swap is replayed individually and the swap happens only at an identical
`last_update_id`. v3 said so in its own text and then kept the 300 ms S4 ceiling anyway, on the
ground that a slow REST read says something about the connection. It was then measured deleting
276 proven wall histories on a 485 ms read whose own proof read `chain_preserved: true`. v4
withdraws that reasoning: in the staged case the window bounds nothing, so the ceiling does not
apply. The ceiling itself is unchanged, every other gate is unchanged, and the connection signal
is kept where it belongs - published on the transition as a property of the read.

## The exemption

S4 is satisfied without the 300 ms ceiling when **all five** of these hold at the swap:

| # | Condition | Where it is checked |
|---|---|---|
| 1 | the install was staged and the chain was preserved | `chain_preserved`, basis `REPLAYED_CHAIN_SAME_UPDATE_ID` |
| 2 | S1 VOLUNTARY | the resnapshot policy asked, on a `SYNCED` book with `first_delta_applied` |
| 3 | S2 CONTINUITY (= no HARD fault between request and install) | fault counters unchanged, `SYNCED`, no invalidation, generation +1 |
| 4 | S3 CHAIN | `staging.last_update_id == live.last_update_id` |
| 5 | the window was measured | `elapsed_ms` present and non-negative |

Fail any one and the 300 ms ceiling applies exactly as v3 had it. The exemption is written in
code as a conjunction of the gates it depends on rather than as trust in `chain_preserved`
alone, so it cannot outlive them.

Every transition publishes `window_verdict`: `WITHIN_MAX`, `EXEMPT_REPLAYED_CHAIN`,
`EXCEEDED_MAX` or `NOT_MEASURED`, plus `window_ms` and `window_max_ms`. The ledger publishes
`soft_window_exempt` beside `soft_refreshes`. A carry that used the exemption cannot look, on a
screen or in the journal, like one that was inside the ceiling.

## What was deliberately left alone

Per the V1.4 decision, and verified by test:

* the 300 ms value itself, and S1, S2, S3, S5
* **divergence stays a diagnostic.** No gate, no threshold. ~64,000 comparisons at zero
  differences is exactly when somebody is tempted to turn it into a refusal, and the frozen
  document now says in writing why that is refused
* **cooldowns**: 300 s on an applied refresh, 10 s backoff on a failed one, `cooldown_consumed:
  false` on failure
* Wall V2 and its five thresholds, the 500,000 USDT display filter, session boundary HARD,
  journal-recovery carry forbidden
* `btc-ms.v0.1`: no `wall` record changed. The dataset is untouched.

### One wording slip in the frozen document, left as frozen

Line 173 says "which of the three cases its window was in" and then lists three of the four
verdict values; the fourth, `NOT_MEASURED`, is listed correctly in the Telemetry section and is
implemented and tested. The document is **not** being corrected, because it was hashed before
any V1.4 refresh was observed and that property is worth more than the word. Recorded here
instead, which is where a post-freeze observation belongs.

## Tests

Backend `tests/crypto` 2,115 passed. In the liquidity map's own files -
`test_ms_v0_continuity`, `test_ms_v0_staged_refresh`, `test_ms_v0_walls`,
`test_ms_v0_isolation`, `test_liquidity_map_continuity` - **147 passed, 0 failed**. The 34
failures and 14 errors elsewhere in `tests/crypto` are pre-existing and in files that import
neither `market_structure_v0` nor `liquidity_map` (`btc_p1`, `btc_p1_forward`, `btc_p2`,
`btc_vol_a`, `c1_parity`, `c1x_parity`, `d6_golden`, `expert_execution_e1`,
`binance_live_leverage_capability`) or that fail on missing measured-response fixtures
(`crypto_multisymbol`). Frontend **897 passed**, `tsc --noEmit` clean.

New tests, one per way the exemption can be denied:

| Test | Asserts |
|---|---|
| `a_slow_rest_read_on_a_replayed_chain_is_carried_rather_than_discarded` | 485 ms, staged → SOFT, `EXEMPT_REPLAYED_CHAIN`, walls carried |
| `an_exempt_carry_always_says_on_screen_that_it_used_the_exemption` | verdict, window, ceiling, basis and the exempt count all published |
| `a_window_inside_the_ceiling_never_increments_the_exemption_count` | 100 ms → `WITHIN_MAX`, exempt count 0 |
| `an_unstaged_slow_install_is_still_hard_exactly_as_it_was` | chain not preserved → `EXCEEDED_MAX`, HARD |
| `the_exemption_does_not_survive_a_stream_that_broke` | one new fault → HARD, `EXCEEDED_MAX`, never exempt |
| `the_exemption_does_not_survive_an_invalidated_book` | invalidation after install → HARD |
| `the_exemption_does_not_survive_a_chain_that_moved` | S3 fails → HARD |
| `the_exemption_does_not_survive_an_involuntary_install` | S1 fails → HARD |
| `the_exemption_does_not_survive_a_book_that_never_applied_a_delta` | S1 fails → HARD |
| `a_window_the_collector_could_not_time_is_never_exempt` (None, -1) | `NOT_MEASURED`, HARD |
| `the_exemption_leaves_every_other_gate_exactly_where_it_was` | five gates, 300 ms, S5 still refuses |
| `what_bounds_an_exempt_window_in_practice_is_the_staged_deadline` | over 1,000 ms → abandoned, book untouched, nothing classified |

Frontend: a panel that names `면제 (체인 재생)`, prints the sentence saying the ceiling did not
apply and why, and never shows that note on a window inside the ceiling.

## Live: the S4 exemption (forced, 5 min)

Real Binance USDⓈ-M, read-only. Real REST reads of **79-175 ms**, with a delay injected through
the Runner's existing `snapshot_fetcher` hook so the request-to-install window lands on the same
figure the v3 loss was measured at. Nothing in the repo was modified to produce this.

* **9 staged refreshes, round trip 480-576 ms, 9/9 SOFT by `EXEMPT_REPLAYED_CHAIN`.** Under v3
  all nine would have been HARD.
* carry **1,988 of 2,414 (82.4%)**; per transition 55.0% to 96.2%. `carried + ended + unknown =
  candidates before` in **9/9**.
* **divergence 0** across ~17,258 level comparisons (1,799-1,993 per swap), `only_live 0`,
  `only_staged 0`.
* attachment 9/9 STRADDLE; 4 frames replayed per refresh; swap 506-610 ms after the request.
* **rollback 0**: 10 checkpoints, `last_update_id` decreases **0**, generation 1→10.
* gaps 0, abandoned 0, storms 0.
* **The control is in the same run**: the session's own startup install took 555 ms and was
  classified **HARD / `EXCEEDED_MAX`**, because it was the recovery path and not voluntary. The
  exemption does not leak out of the staged case.

## Live: the practical bound (forced, 3 min)

A 1,400 ms delay, so every attempt exceeds `REFRESH_DEADLINE_MS`:

* 6 attempts, **6 abandoned** at `REFRESH_DEADLINE_EXCEEDED` (elapsed 1,499-1,575 ms),
  `cooldown_consumed: false`, `refresh_storm` at 5 consecutive failures, **0 applied, 0 exempt
  carries**. The frozen document's claim that no exempt carry can come from an attempt older
  than the deadline is confirmed on live data.

### A defect this exposed, which is V1.3's and is **not** fixed here

When a staged attempt is abandoned at the deadline, its REST read is still in flight. The late
snapshot arrives, finds no refresh in flight, and is installed by the **recovery** path onto a
book that is perfectly healthy - with no check that it is newer. Measured: the book went from
`11731449267188` back to `11731449126257` (about 141,000 ids), and the next frame was
`GAP_FIRST_DELTA`. Two gaps in three minutes, each one following an abandonment.

It is reachable only when the REST read itself exceeds 1,000 ms. Observed production reads are
75-175 ms and the worst ever recorded is 485 ms, so this has never happened in a real session -
but it is a path by which a *voluntary* refresh can break a healthy book, which is the one thing
the staged design exists to make impossible. It is out of V1.4's scope (the decision froze
everything but S4, and this is neither a gate nor a threshold), so it is reported rather than
patched. **User decision required.**

## Live: natural triggers (unforced, 30 min)

A separate collector, nothing forced, same market window.

* **3 natural `coverage_edge` triggers, 3/3 installed, 0 refused.** Round trips **84, 104 and
  292 ms**: all three `WITHIN_MAX`, **0 exempt**. This is the result to want. V1.4 changes
  nothing in the ordinary case, and a session with no slow read looks exactly as it did under
  v3.
* The 292 ms read came **8 ms** from the ceiling. Under v3 an 8 ms slower connection on that one
  read would have ended 272 wall histories; under v4 it would have carried them.
* carry **614 of 799 (76.8%)**, per transition 70.7% / 85.4% / 74.3%, three counts summing to
  candidates-before in **3/3**.
* divergence 0 across ~5,238 comparisons. gaps 0, rejected 0, storms 0, rollback 0.
* health: peak RSS 40.9 MiB with no drift, queue backlog max 557 of 8,192, dropped records 0,
  104,509 rows, projected **4.76 GB/day**.

### Coverage, and the one cost V1.4 was told not to touch

| | this session | V1.3 session |
|---|---|---|
| margin median | 3.29 bp | n/a |
| below the 1.0 bp trigger | **10.5%** | 2.0% |
| negative (band promise broken) | **4.0%**, 72 s | 0% |

The negative stretches are 14 s and 58 s, worst -1.85 bp, and they sit **entirely inside one
cooldown window**: the refresh at t=406 s started the 300 s clock, the margin crossed the
trigger at t=544 s, and nothing could be done about it until the cooldown expired at t=707 s,
at which point the refresh installed immediately and the margin recovered.

So this session was worse than V1.3's on coverage, and the cause is not the staged refresh or
the exemption: it is the 300 s cooldown meeting a market that drifted inside it. The V1.4
decision was explicit that the cooldown stays at 300 s, so this is reported as a measurement
rather than acted on. It does say that V1.3's "negative 0%" was a property of that half hour and
not of the design.

## Live: the exemption on the screen, not only in a test

A third short forced session, with the preview API polled against the live root while the
collector was writing to it. The screen read: `SOFT`, window **517 ms**,
`EXEMPT_REPLAYED_CHAIN`, `window_exempt: true`, carried **275** of 287 (ended 11, unknown 1,
sum correct), ledger `soft_refreshes 1 / soft_window_exempt 1`, `active_carried 257`, wall
summary `candidate_count 287 / walls_selected 31 / carried 217`. The rule panel served
`lm-continuity.v4` with `sha256_agrees: true`.

That session's four swaps were 505-517 ms, 4/4 exempt, carry 95.8% to 97.9%, divergence 0 across
~8,000 comparisons.

## 24-hour trial readiness

**Ready to start.** The runbook, the acceptance criteria written before the run, the measured
storage projection and the post-run analyzer are in
`TRIAL_24H_RUNBOOK_V1_4.md` and `trial_analyze.py` beside this report. Nothing is missing: the
collector CLI already takes `--duration 86400`, 6.14 GB/day worst case against 863 GB free, peak
RSS 42 MiB with no drift, dropped records 0 across every session run today.

The trial is the only way to reach the four things still unmeasured: a real live HARD, a safety
refresh firing on its own, the natural rate of the exemption, and coverage across a full
session-handover cycle.

## Production readiness

**Not ready, and V1.4 does not claim to make it ready.** What V1.4 closes: the V1.3 cost of a
slow read destroying a carry the replay had proved. What remains open is unchanged from V1.3:
① live HARD (a real gap) still unobserved outside forced conditions ② the safety refresh has
still never fired of its own accord ③ no 24-hour session ④ app shell untouched. Added by this
gate: ⑤ the abandoned-refresh recovery install above, and ⑥ coverage measured at 4.0% negative
in a half hour where V1.3 measured 0%, entirely inside one 300 s cooldown.

## What needs a decision

1. **The abandoned-refresh recovery install.** A late snapshot from a timed-out staged attempt
   is installed over a healthy book with no newer-check, and was measured rolling the chain back
   141,000 ids and causing a gap. Out of V1.4's scope, reachable only above a 1,000 ms REST
   read. Fix it before the 24 h trial, or run the trial and watch for it?
2. **The 300 s cooldown.** Held at 300 s per the V1.4 decision, and measured costing 72 seconds
   of broken band promise in 30 minutes. Now that a failed refresh costs no cooldown and a
   successful one is ~7.5% of the carry history rather than all of it, the original reason for
   the floor is much smaller than it was.
3. **The 24 h trial.** Ready to start on the word.

## Deploy state

Local only. **No commit, no push, no deploy.** Tracked files modified: **0**.
