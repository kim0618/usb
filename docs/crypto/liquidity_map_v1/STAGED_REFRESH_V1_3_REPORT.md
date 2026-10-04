# Liquidity Map V1.3: staged coverage refresh (buffer, replay, atomic swap)

Date: 2026-10-04 (KST).
Continuity rule **`lm-continuity.v3`**, sha256 `9637ef1be41e9eb679eb50634990801b28677fc5122bcca35ba956bd4d17231d`,
superseding v2 (`596339b6…`) and v1 (`d68a26ce…`).
`lm-wall.v2` unchanged (`deaa9db8…`). `btc-ms.v0.1` unchanged (`9eed3862…`).

**Status: isolated preview only. Not deployed, not committed, not linked from navigation.**
No direction, no rating, no order path, no AUTO change.

---

## 1. The defect, restated from measurement

V1.2 installed a voluntary refresh by handing the REST snapshot to the live book, refusing
whenever the snapshot was not newer than the deltas already applied. On a moving book that
refused **every** attempt: 2 requests in 23 minutes, 2 refusals, 0 installs, while the ±0.1% band
the preview may call COMPLETE left the known interval for 50 seconds.

The snapshot was never the problem. An independent probe found it **newer** than the newest
stream frame in **10 reads out of 10**, by 2,176 to 21,682 ids, with a book age of 32 to 178 ms.
The comparison simply happened at the wrong moment: during the ~105 ms round trip the stream
advanced past the snapshot, by 1,695 and 1,789 ids in the two measured refusals, so by arrival
the live book was ahead of it.

## 2. The design

The snapshot is no longer a replacement. It is the **base of a second book**:

1. from the moment the policy wants a refresh, every frame the live book **applies** is also kept
   in a bounded buffer (applied frames only, so the buffer *is* the live chain: duplicates and
   out-of-order frames the live book refused never enter it);
2. the live book keeps serving, untouched;
3. the REST snapshot builds a staging book;
4. buffered frames replay onto it through the same `apply_delta` the live book used, so frames
   older than the snapshot are discarded by the contract's own rule;
5. the gates are evaluated;
6. frames that keep arriving are applied to both books;
7. the staging book catches up to the live chain;
8. and only then, the swap.

```
INVARIANT AT SWAP:  staging.last_update_id == live.last_update_id
```

The swap therefore changes **levels and bounds only**. The chain position is not assigned at all,
so the next frame chains on as an ordinary `pu` link, there is no first-delta rule to re-satisfy,
no gap to risk, and - the part that matters for continuity - **no unenumerated window**: every
event between the snapshot and the swap was applied individually rather than absorbed.

`_swap_refresh` is the only code anywhere that writes to the live book on a refresh's behalf.
Every other outcome discards the attempt, which is why "the book kept serving" is a property of
the control flow rather than a promise.

### One thing the design needed that the contract's rules do not cover

The futures first-delta rule is `U <= lastUpdateId <= u`. When the snapshot's id lands exactly on
a **frame boundary** the next frame starts at `U = lastUpdateId + 1` and cannot attach under it,
even though nothing is missing. A staged replay meets this case the moment a snapshot id is a
boundary, so the attachment is decided explicitly and both ways are exact:

| attachment | condition | meaning |
|---|---|---|
| `FIRST_FRAME_STRADDLES_SNAPSHOT_ID` | `U <= lastUpdateId <= u` | the contract's own rule |
| `FIRST_FRAME_IS_IMMEDIATE_SUCCESSOR` | `pu == lastUpdateId` | the preceding frame ended at the snapshot's id, so this is the very next event |

Anything else is a hole and the refresh is abandoned. `apply_delta` still applies the frame
either way; the only choice made is which of its two rules the staged book is asked to use.
`book.py` is untouched.

### Cooldown and backoff

| outcome | cost |
|---|---|
| **applied** | the existing 300 s coverage cooldown, because a swap is what costs a generation |
| **refused / failed** | **no cooldown**, 10 s backoff |

V1.1 started the cooldown clock when a refresh was *requested*, so an attempt that never
installed still cost five minutes of unguarded band - measured, twice, while the margin went
negative. The clock now starts at the swap. Consecutive failures are counted and a
`refresh_storm` record is published every 5, so a refresh that can never succeed is visible as a
pattern rather than a trickle.

### What was forbidden and is unchanged

No rollback of the live book onto a stale snapshot (structurally impossible: its chain position
is never written). No carry without proof. No HARD fault treated as SOFT. `lm-wall.v2` thresholds
untouched and hash-verified; display filter 500,000 USDT; HARD default, 300 ms SOFT window,
session boundary HARD, journal-recovery carry forbidden - all as `lm-continuity.v2` left them.

---

## 3. Verification

**Backend 479 passed, frontend 881 passed.** `test_ms_v0_staged_refresh.py` is new, 28 cases.

| the gate asked for | result |
|---|---|
| busy stream, snapshot + replay succeeds | frames arriving during the round trip are replayed and the swap lands at the live id |
| gap during replay → HARD | the attempt is abandoned, the book takes the gap on its own terms, and the recovery snapshot that follows is HARD |
| disconnect during replay → HARD | abandoned, book untouched |
| duplicate / out-of-order | never enter the buffer: it holds applied frames only |
| snapshot newer at request but stale at install | the case the whole release is for, now installs |
| active book serving, interruption 0 | every observable (`state`, `mid`, bounds, levels) sampled at every step of an attempt, all identical |
| ±0.1% coverage recovery | measured live, below |
| carry/ended/unknown invariant | the three sum to `candidates_before` on every transition, asserted in tests and checked on all 36 live transitions |
| failed refresh consumes no cooldown | asserted directly, and that the backoff is shorter than the cooldown |
| retry backoff | held inside 10 s, released after |
| concurrent refresh prevention | a second request is refused while one is staged |
| RSS / queue / API latency | below |

Each failure path is tested by comparing the **entire observable book** before and after, so
"untouched" is a measurement rather than an assertion about the fields somebody remembered.

---

## 4. Live measurement

Two sessions in the same market window. The market was moving at **0.95-0.97 bps/min of path**
against a quiet-book baseline of 0.085, so roughly 11x; it was not violent (no funding print, no
cascade).

### A. The policy's own trigger, nothing forced (29.0 min)

| | V1.2 (23 min, previous gate) | **V1.3 (29 min)** |
|---|---|---|
| coverage-edge triggers fired | 2 | **3** |
| installed | **0** | **3** |
| refused | 2 | **0** |
| margin below the 1.0 bp trigger | 12.0% of samples | **2.0%** |
| margin **negative** (promise broken) | **3.7%**, worst -0.72 bp | **0%**, min 0.19 bp |

Coverage recovery at each trigger, in bps of margin over the ±0.1% promise:

```
0.38 -> 5.27   (+4.89)
0.19 -> 4.93   (+4.74)
0.80 -> 4.74   (+3.94)
```

**The band was never left unprotected.** The thing V1.2 could not do, three times in half an hour.

### B. Forced triggers, to measure the staged path itself (24.2 min, 33 refreshes)

| | |
|---|---|
| requested / applied / refused / storms | **33 / 33 / 0 / 0** |
| REST round trip | 75 - 145 ms, median ~96 (gate 300 ms) |
| **request to swap** | **100 - 302 ms** (deadline 1,000 ms) |
| frames buffered during the round trip | 1 - 2 |
| **frames replayed** | 1 every time |
| frames discarded as older than the snapshot | 1 - 2 |
| frames arriving after the snapshot | 0 - 1 |
| attachment | `STRADDLE` 33/33 |
| **divergence between the staged and live books** | **0**, every time |

The divergence figure is the strongest evidence here. At the swap the two books stand at the same
update id, built independently - one from an old snapshot plus months of deltas, the other from a
fresh snapshot plus the replay - and inside the interval they both claim to know they are
compared level by level. Across 33 swaps that is **1,705 to 2,000 levels each, about 64,000
comparisons, with 0 differing, 0 present only in the live book and 0 only in the staged one.**

It is published as evidence and deliberately **not** gated on: a disagreement would be
information about the exchange's snapshot or about this collector, and turning a number nobody
has characterised into a refusal would be inventing exactly the kind of threshold the rest of
this work refuses to invent. Now that it has been characterised at 0, it could become a gate -
that is a decision, below.

### Walls across the 33 SOFT transitions

| | |
|---|---|
| candidates before, total | 9,178 |
| **carried** | **8,489 (92.5%)** |
| ended | 498 |
| unknown | 191 |
| per-transition carry rate | min 61.5%, median **95.6%**, max 97.8% |
| `carried + ended + unknown == candidates_before` | **true on all 33** |

### The invariant, checked against the journal rather than the code

Every `checkpoint` record carries `generation` and `last_update_id`. Across both sessions,
**33 checkpoints spanning 29 generation changes, zero rollbacks**: the live book's chain position
never decreased, at a swap or anywhere else.

### Health

| | natural (29 min) | forced (24 min, 33 swaps) |
|---|---|---|
| gaps / reconnects / crossed / overflows | 0 | 0 |
| dropped records | 0 | 0 |
| queue backlog max | 559 / 8,192 | 600 / 8,192 |
| RSS | 27 - 42 MiB | 27 - 42 MiB, no drift |
| depth state | `SYNCED` 1715/1715 | `SYNCED` 1445/1445 |
| preview API | median 9.5 ms, p90 15.7 ms, 0 errors | - |
| active candidates | 258 - 308 | 258 - 305 |
| snapshots rejected by the book | 0 | 0 |

Thirty-three extra REST reads and thirty-three staging books in 24 minutes cost nothing
measurable in memory, queue depth or latency.

---

## 5. The one case where the book was refreshed and the carry was not

Worth its own section, because it is the design working and it has a cost.

The **first natural trigger** fired at a margin of 0.38 bp. Its REST round trip took **485 ms**,
over the 300 ms S4 ceiling. What happened:

* the staged refresh **installed** - margin recovered 0.38 → 5.27 bp, the band protected;
* the continuity proof was **HARD**, reason `HARD_REST_WINDOW_EXCEEDS_SAMPLE_INTERVAL`, with
  every other gate passing;
* so **276 candidates → 0 carried, 0 ended, 276 unknown**.

That separation is correct and intended: refreshing the book and carrying wall history are
different claims with different evidence, and the slow read invalidates only the second.

But note what the proof also recorded for that same refresh: `chain_preserved: true`,
`basis: REPLAYED_CHAIN_SAME_UPDATE_ID`, `replayed_frames: 1`. **Every intervening event was
applied individually.** The S4 window exists to bound events that were absorbed rather than
applied, and in a replayed swap there are none - the v3 document says so in as many words. So on
a staged refresh, S4 is now stricter than its own justification requires, and the 485 ms read
cost 276 wall observations it arguably did not need to.

S4 was kept at 300 ms because this gate's instructions said to keep it. The cost is now measured:
**1 of 3 natural refreshes**, and the one that fired at the lowest margin.

---

## 6. Production readiness

**No**, but the reason that has blocked every previous gate is gone.

What is now resolved:

* the coverage-edge refresh installs on a moving book - **36 of 36 attempts across both
  sessions**, against 0 of 2 in V1.2;
* the ±0.1% COMPLETE promise held for the whole 29-minute natural session;
* wall history survives a refresh at a 92.5% carry rate on a moving book, where V1.2 had no
  volatile-market number at all because no refresh ever landed;
* the live book is never rolled back, and a failed attempt costs nothing.

What is still open:

1. **No live HARD resync has ever been observed.** `gaps 0` again, in both sessions, as in every
   V0 run since the beginning. The HARD paths remain proved by test and by shutdown only.
2. **The safety refresh has still never fired on its own** - no uninterrupted hour.
3. **Still not a violent market.** 0.95 bps/min is 11x the quiet baseline, but there was no
   funding print and no liquidation cascade. The 61.5% worst-case carry rate came from the most
   active stretch of this window, which suggests the rate does fall with volatility, but three
   data points below 90% is not a characterisation.
4. **A 24 h session has never been run.** Everything here is 24 to 29 minutes.
5. The preview still renders inside the app shell, which a concurrent session is editing.

### Decisions for the user

* **A.** Whether S4 should be waived, or widened, when `chain_preserved` is true. Measured cost
  of keeping it: one natural refresh in three lost its entire wall history to a 485 ms read that
  had replayed every intervening frame. Changing it is a `lm-continuity.v4`.
* **B.** Whether the replay divergence should become a gate now that it has been characterised at
  exactly 0 over ~64,000 comparisons. Gating would turn a silent disagreement into a refusal; it
  would also make the refresh fail on a benign exchange quirk nobody has seen yet.
* **C.** Whether the 300 s cooldown is still the right floor. It was set when a refresh cost the
  entire wall history; a SOFT refresh now costs about 7.5% of it, so the same protection could be
  bought with a shorter floor and the band would be re-centred more often.
* **D.** A 24 h trial, which is the only way items 1 to 4 close.
