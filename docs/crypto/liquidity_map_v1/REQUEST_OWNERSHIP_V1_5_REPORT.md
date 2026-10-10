# Liquidity Map V1.5: request ownership and the coverage floor

2026-10-04 (KST). Local only. **Nothing committed, nothing pushed, nothing deployed.**

Two defects were recorded in the V1.4 report and in `CRYPTO_CURRENT_STATUS_20261004.md` rather
than fixed. This closes both, and nothing else: no Wall V2 threshold moved, no continuity gate
changed, C1 and C1x were not touched, no direction or score exists anywhere in this code, and no
order path was opened.

## A. Contract, frozen before the results

`lm-continuity.v5`, in `WALL_CONTINUITY_V1_2.md`, sha256
`c9d9659c8957501c97e570aaf55d8cf616e3c6733c6a238174c86365031fed44`, superseding v4
`0ed46edcc0a58957b99cc14bf5b8bba92a4111eb94026b7353311042e58327b3`. The document was written and
hashed **before** a single V1.5 test or live session was run, as every version since v1 has been.

What v5 changes, in its own changelog:

1. **Snapshot request ownership.** Every REST request carries an id, a purpose and a state, and a
   response may be installed only by the attempt that asked for it.
2. **The coverage-edge floor is 10 s, not 300 s.** The safety refresh keeps 300 s; fault-driven
   recovery stays immediate.
3. Editorial: the S4 sentence said "three cases" where the published field has four values
   (`NOT_MEASURED` was missing from the sentence, though not from the telemetry section).

What v5 explicitly does **not** change, quoted from the document: S1 through S5 including the
300 ms window value and the v4 exemption with all five of its conditions; HARD as the default and
the exhaustive HARD list; the per-candidate verdicts; the bin-level identity rule; the effect on
R4; every `lm-wall.v2` threshold and the 500,000 USDT display filter; the session boundary being
HARD; a journal reconstruction carrying nothing; a failed refresh consuming no floor and taking
the 10 s backoff; one staged refresh at a time and the 1,000 ms staged deadline; and the replay
divergence check remaining evidence rather than a gate.

The compact state file moved to `ms-v0-state.v1-3` and the viewer accepts v1-1, v1-2 and v1-3.
V1.2's bump was for an additive section; this one is for a **replaced** one, which is the case the
version field exists for: `coverage_cooldown_s` and `coverage_cooldown_remaining_s` are gone, so a
v1-2 reader handed a v1-3 file would have printed a cooldown that no longer exists.

## B. The late snapshot: what was wrong and what the fix is

V1.3 and V1.4 identified a response by nothing at all. `on_snapshot` asked "is a refresh in
flight?" and, if not, handed the payload to the recovery path. So when a staged attempt was
abandoned at its 1,000 ms deadline, its read stayed in flight; the response arrived to find no
refresh in progress; and the recovery path installed a snapshot that was by then **behind** the
live book onto a book that was perfectly healthy. `apply_snapshot` assigns `last_update_id` from
the payload and clears `first_delta_applied`, so the book moved backwards and then demanded a
first delta it had already had. Measured under a forced 1,400 ms delay: about 141,000 ids
backwards, followed by `GAP_FIRST_DELTA`.

The root cause is not the missing newer-check. It is that a response had no owner, so a snapshot
requested for one purpose could be used for another. A newer-check would have stopped this one
rollback and left the structure that produced it in place.

**The fix.** A request is an object: `request_id`, `purpose`, `refresh_attempt_id`, `state`. The
runner carries the id with the read and back with the response. On arrival, ownership is resolved
once, before anything looks at the payload, and there are three outcomes and no fourth: the owner
installs it, the staged swap installs it, or it is discarded.

The strongest consequence is a path that was deleted rather than guarded. A `SOFT_REFRESH`
response can now reach the live book **only** through `_swap_refresh`. V1.3 and V1.4 fell through
to the recovery path when the book had faulted during the read, and that fall-through is gone: the
response is discarded and the fault's own recovery request does its own read. That costs one extra
round trip of UNSYNCED time, and it removes the only code path that could install a snapshot for a
purpose it was not requested for.

## C. The ownership structure

| field | values |
|---|---|
| `purpose` | `SOFT_REFRESH`, `HARD_RECOVERY`, `INITIAL_SYNC` |
| `state` | `ACTIVE`, `APPLIED`, `ABORTED`, `EXPIRED` |
| `response_disposition` | `APPLIED`, `DISCARDED_ABORTED`, `DISCARDED_EXPIRED`, `DISCARDED_WRONG_OWNER` |

* The purpose is decided when the request goes out and never on arrival. `INITIAL_SYNC` is
  "no snapshot has ever been installed in this session" - deliberately not
  `depth.snapshot_update_id is None`, which `invalidate` clears, so a faulted book would
  otherwise keep looking like a cold start and every recovery would be filed as one.
* A request still outstanding when a newer one is issued becomes `EXPIRED` immediately, so two
  responses can never both be installable.
* A `SOFT_REFRESH` response older than the 1,000 ms staged deadline is `EXPIRED` even if nothing
  else ended its attempt. In the forced sessions this was the path that fired most often, because
  the sampler's deadline check runs once a second and a response delayed by 1,400 ms usually
  arrives before the next sample: 16 of 17 responses at a 1,400 ms delay were `EXPIRED` and 1 was
  `ABORTED`, while at 2,500 ms all 7 were `ABORTED`. Both words mean the same thing to the book.
* A recovery or initial response has **no** age limit. Whether it can still be used is decided by
  `apply_snapshot`'s chain rule and by what the prefix buffer can bridge, which are measurements
  rather than a clock, and the book it would replace is already unusable so there is nothing it
  can roll back. A TTL here would have thrown away usable recoveries.
* A discard changes nothing: not the levels, bounds, `last_update_id`, generation or invalidation
  state, and **not `snapshot_wanted`**. If a recovery is due, the fault that invalidated the book
  already asked for it. Only the newest request's answer clears `snapshot_in_flight`, so a
  response from an older request cannot make the collector think the read it is still waiting for
  came back.
* The raw `snapshot` record is still written for a refused response, because the raw stream is the
  replay authority and a read that happened is a fact. It carries `response_disposition` and, when
  refused, an explicit note that a replay must honour it - a replay that installed every snapshot
  record it found would reproduce the exact defect this closes.

## D. The coverage floor

The 300 s floor was measured breaking the promise it was supposed to protect. In 30 natural
minutes on 2026-10-04 the coverage margin was negative 4.0% of the time, worst -1.85 bp, 72
consecutive seconds, and the whole stretch sat inside one cooldown: installed at t=406 s, out of
the trigger at t=544 s, recovered the instant the cooldown expired at t=707 s.

| trigger | floor between installed refreshes | changed? |
|---|---|---|
| `coverage_edge` | 10 s | yes, from 300 s |
| `safety_refresh` | 300 s (hourly trigger anyway) | no |
| fault recovery | none, immediate | no |

The floors are per trigger in one table (`REFRESH_MIN_INTERVAL_S`) with a per-trigger "last
installed" clock, so one trigger cannot spend another's floor. The cost side of the old trade also
shrank: a coverage refresh is staged now, and a staged refresh that passes the five gates carries
its candidates instead of ending them (92.5% across 33 measured transitions). The floor is not
zero because a transition still costs the ledger something.

Two things are published rather than acted on. A refresh that installs and still leaves the margin
under the trigger emits `refresh_ineffective`: the collector keeps refreshing on the 10 s floor,
because the band's COMPLETE claim is the thing being protected and backing off would hide the fact
rather than fix it. And the contract states in its own words that a coverage refresh is a data
quality guarantee, not strategy logic: nothing reads it to decide anything.

API weight is not the binding cost and never was: a `limit=1000` read is weight 20 against
2,400 a minute, so even one every 10 s is 5% of the budget.

## E. Tests

`backend/tests/crypto/test_ms_v0_request_ownership.py` is new (15 tests). Every late-arrival test
compares the **whole** observable book before and after the arrival rather than the three fields
somebody thought of.

Late response: deadline abort then late arrival; a fault mid-read then arrival (the deleted
promotion); a previous SOFT response arriving while a HARD recovery is in flight; a response past
the staged deadline; an orphaned attempt; an unknown request id; a second delivery of an answered
request; a newer request expiring an outstanding one; out-of-order responses; and a five-round
sequence asserting `last_update_id` is monotonic across all of it. Published: the raw record's
ownership and refusal note, and the policy view's counters.

Floors, in `test_ms_v0_resnapshot.py` and `test_ms_v0_staged_refresh.py`: an installed coverage
refresh blocks at 2, 5 and 10 s and is free at 12; a success does **not** create a 300 s lock,
asserted at both 11 s and 299 s; a failed attempt consumes no floor and takes the 10 s backoff;
the safety floor is still 300 s, measured directly and through the policy; one trigger's install
does not start another's floor; and `COVERAGE_REFRESH_COOLDOWN_S` is asserted **absent** rather
than renamed, because a constant that still existed would be read as the policy.

| suite | result |
|---|---|
| this work's 21 backend files | **508 passed** |
| `backend/tests/crypto` whole directory | 2,140 passed, 34 failed, 14 errors |
| frontend `vitest` | **906 passed** (55 files) |
| frontend `tsc --noEmit` | clean |

The 34 failures and 14 errors are in `test_btc_p1_forward`, `test_btc_p2`, `test_btc_p1`,
`test_btc_vol_a`, `test_crypto_multisymbol`, `test_expert_execution_e1`,
`test_binance_live_leverage_capability`, `test_c1_parity`, `test_c1x_parity` and `test_d6_golden`.
None of them imports anything changed here; their causes are missing gitignored freeze artifacts
and missing contract documents (`FileNotFoundError`, `ForwardContractHashMismatch`,
`ArtifactMismatch`), which is the known pre-broken cluster.

## F. Live gate

Isolated: read-only public Binance, own output roots under the session scratchpad, own processes,
no production root touched, nothing deployed. Delay injection used the runner's existing
`snapshot_fetcher` hook, so the collector was not modified for the measurement.

### F1. Forced, 1,400 ms delay, 6 minutes, a refresh forced every 20 s

| | |
|---|---|
| forced refresh attempts | 17 |
| installed | **0** |
| late responses discarded | **17** (16 `DISCARDED_EXPIRED`, 1 `DISCARDED_ABORTED`) |
| rollback that would have happened | 61,800 to 101,145 ids per response |
| `last_update_id` decreases | **0** |
| installs behind the chain (journal check) | **0** |
| gaps / dropped records | **0 / 0** |
| generation at end | 1 (startup only) |
| checkpoints | 1 |
| REST read median / max | 89.7 / 152.4 ms (total with injection up to 1,553.9 ms) |
| ±0.1% COMPLETE | 716/718 side-samples, 100% of synchronised samples |
| coverage margin | min 1.07, median 2.88 bp, negative 0.00% |
| queue backlog max / RSS max | 288 of 8,192 / 45.5 MiB |

The two non-COMPLETE readings are sample 1 of the session, before the first snapshot installed,
where the band's coverage is `UNKNOWN` rather than `PARTIAL`. A book that does not exist yet is
not a broken promise, and it is counted separately rather than rounded away.

### F2. Forced, 2,500 ms delay, 2.5 minutes - the abort path specifically

A 2,500 ms delay puts a sampler tick between the deadline and the arrival, so this run exercises
the incident's exact sequence: abort first, response afterwards.

| | |
|---|---|
| forced attempts / installed | 7 / **0** |
| discarded | **7, all `DISCARDED_ABORTED`** with `closed_reason REFRESH_DEADLINE_EXCEEDED` |
| rollback avoided | 101,207 to 153,754 ids per response, bracketing the 141,000 incident |
| `last_update_id` decreases / gaps / dropped | **0 / 0 / 0** |
| installs behind the chain | **0** |
| coverage margin negative | 0.00% |

### F3. Natural, 31 minutes, the policy as it ships

No injection, no forcing, the shipped policy. 1,859 samples, 31 minutes.

| | |
|---|---|
| coverage triggers fired | 5, all natural |
| installed / refused / abandoned | **5 / 0 / 0** |
| SOFT / HARD transitions | 5 / 1 (the session's own start) |
| continuity verdict on all five | `SOFT_ALL_GATES_PASSED`, `WITHIN_MAX`, `chain_preserved: true` |
| REST round trips on the five | 87, 117, 83, 82, 79 ms; request to swap 143 to 214 ms |
| carry | 1,057 of 1,397 = **75.7%** (per transition 69.4 to 81.7%) |
| three counts sum to `candidates_before` | **5 of 5** |
| replay divergence | **0** across 8,682 level comparisons |
| `last_update_id` decreases / installs behind the chain | **0 / 0** |
| responses discarded | **0** (nothing arrived late, which is the normal case) |
| coverage margin | min **0.15 bp**, p05 1.83, median 4.17, max 5.88 |
| margin under the 1.0 bp trigger | **0.27%** of samples |
| margin negative | **0.00%**, longest run 0 s |
| ±0.1% band COMPLETE | **3,718 / 3,718 = 100.00%** |
| gaps / dropped records | **0 / 0** |
| queue backlog max / RSS max | 568 of 8,192 / 43.3 MiB, no drift |
| storage projection | 5.01 GB/day at this transition rate |

**The floor change is what produced the last four rows, and the journal says so directly.** The
intervals between the five installed refreshes were 426.0, 62.9, 118.0 and 345.0 seconds. Two of
the four are shorter than the 300 s cooldown v1 to v4 applied, so under v4 the third and fourth
refreshes would have been **refused**, and the margin would have stayed under the trigger until
the cooldown expired - 237 s in one case. That is the same mechanism V1.4 measured as 72
consecutive seconds of negative margin. Here the comparable numbers are 0.27% under the trigger
and 0.00% negative, against V1.4's 10.5% and 4.0%.

The 10 s floor itself never bound: the shortest natural interval was 62.9 s, six times the floor.
So what V1.5 removed was not a limit the market was pressing against; it was a limit that
happened to fall in the middle of the natural refresh rate.

### F4. The screen, on the live root

The isolated preview was pointed at the natural run's root while it ran. It served
`lm-continuity.v5` with `sha256_agrees: true`, the per-trigger floors
(`coverage_min_interval_s: 10.0`, `safety_refresh_min_interval_s: 300.0`), the ownership record
for `snapshot-1` (`purpose: INITIAL_SYNC`, `request_state: APPLIED`,
`response_disposition: APPLIED`, `response_age_ms: 214`), `late_response_can_install: false`, and
**no** legacy cooldown keys. Walls were read from the `ms-v0-state.v1-3` checkpoint
(`verified_by: COLLECTOR_STATE_CHECKPOINT`), which is the live proof that the reader's version
list moved with the writer's.

### F5. Where the evidence is

Three isolated roots under this session's scratchpad, with their journals intact:
`v15/forced` (F1), `v15/aborted` (F2), `v15/natural` (F3), plus the harness `v15/harness.py`
and the three `*.log` files carrying the START and END records. The scratchpad is
session-scoped and will be cleaned, so the numbers above are reproduced here rather than
referenced; the analyzer that produced them is `trial_analyze.py`, which reads the journal.

The production collector root was not touched by any of this, no `.env` was read, and nothing
was deployed.

## G. Remaining limitations

1. **No live HARD resync that nobody arranged.** Still true, and unchanged by V1.5. Every HARD
   transition observed so far was a session start or an injected condition.
2. **The hourly safety refresh has never fired on its own.** It needs 3,600 s on one snapshot and
   coverage refreshes keep resetting that clock. Its 300 s floor is therefore also unexercised
   live, and is measured only by test.
3. **No 24-hour session.** The runbook is ready and was not started.
4. **The natural carry rate was 75.7% here against 92.5% in V1.3's forced measurement.** Both
   are real; they are different markets and different refresh causes, and nothing in V1.5 touched
   the carry rule. A session long enough to characterise the carry rate across market regimes has
   not been run, so neither number should be quoted as *the* carry rate.
5. **The 10 s floor's upper bound on transition rate is theoretical, not measured.** The
   shortest natural interval observed was 62.9 s, so the floor never bound. The trigger only
   fires when the margin drops under 1 bp and a fresh snapshot restores it to about 4 to 6 bp, so
   re-triggering inside 10 s needs mid to move several bp in 10 s. A genuinely violent market
   could in principle drive six transitions a minute, and the journal cost of that is `wall`
   records, the line item that dominates storage. Not observed, not bounded by measurement.
6. **`DISCARDED_WRONG_OWNER` has not occurred live.** It is reachable only by a caller that builds
   a request another way, or by a duplicate delivery, neither of which the runner can produce. It
   is covered by test only.
7. **The app shell is still untouched**, and the preview is still reachable only by its own URL.

## H. 24H trial readiness

**Ready, not started.** The runbook (`TRIAL_24H_RUNBOOK_V1_4.md`, content V1.5) now records four
further acceptance criteria, written before any run: every response's disposition accounted for
with no `resync` beside a refused one; installs behind the chain 0; at least 10 s between two
installed coverage refreshes; and the ±0.1% COMPLETE ratio with pre-synchronisation samples
counted separately. The analyzer `trial_analyze.py` prints all of them from the journal rather
than from the collector.

## I. Production readiness

**No.** Items 1 to 3 of section G are the same three that blocked V1.4, and V1.5 did not address
them: they need a day of wall-clock, not code. What changed is that the two recorded defects are
closed and the band's COMPLETE promise is now kept by the policy rather than broken by its floor.
