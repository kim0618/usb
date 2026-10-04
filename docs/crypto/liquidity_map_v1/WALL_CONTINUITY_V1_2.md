# Liquidity Map wall continuity rule V1.2: HARD and SOFT resync, frozen

Status: FROZEN, 2026-10-04 (KST). Rule version: **`lm-continuity.v4`**.
Supersedes `lm-continuity.v3` (sha256 `9637ef1be41e9eb679eb50634990801b28677fc5122bcca35ba956bd4d17231d`),
`lm-continuity.v2` (sha256 `596339b66cc170b4bd1550b93e44f63e35f81c13e28f833dc9a446e67b691576`)
and `lm-continuity.v1` (sha256 `d68a26ce2a170d5549f5e8bec5db02b8fc91d7bfd489f31d97e39653a0148656`),
the first of which was frozen **before** the resulting carry rates were examined. Every version
since has been frozen the same way, this one included: v4 was written and hashed before a single
V1.4 refresh was observed.
Changes require a new version, never a silent edit, and never a re-tuning against an outcome
somebody preferred. What changed in each version, and why, is recorded at the end of this
document.

## What this rule is, and what it is not

Two documents are already frozen and **neither is changed by this one**:

* `btc-ms.v0.1` (`docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md`) is the journal's
  authority. Its wall clause says that a loss of freshness or a resync "ends continuity as
  UNKNOWN". **Every V0 `wall` record keeps exactly that behaviour and exactly its current
  fields.** At a generation change the collector still writes `status=UNKNOWN` for every
  candidate it was holding, and the candidates that re-qualify still get fresh `OPENED` rows
  whose `first_seen_ms` is the moment they re-qualified. Nothing in this document moves a byte
  of the dataset.
* `lm-wall.v2` (`WALL_RULE_V2.md`) decides what the viewer is willing to call a wall. **R1 to
  R5 and all five thresholds are unchanged**: notional 250,000 USDT, multiple 5, distance
  1.0 bp, persistence 10,000 ms, bin 5.0 USDT. The display filter stays 500,000 USDT and stays
  the operator's.

This document defines one thing only: **which `first_seen` a wall's observation span is measured
from**. It can only ever *extend* a span across a transition it has proved, and it is never
allowed to shorten one, to create a wall, to remove a wall, or to change a threshold.

## The defect being fixed

V1.1 added two voluntary resnapshot triggers: a coverage-edge refresh when the protected
±0.1% band is about to leave the snapshot's known interval (cooldown 300 s), and a safety
refresh after 3600 s on one snapshot. Both are necessary and both were measured: the live
coverage-edge refresh on 2026-10-04 restored the margin from 0.62 bp to 4.56 bp in 130 ms and
the ±0.1% band stayed COMPLETE across the transition.

But a resnapshot increments the book `generation`, and a new generation ends **every** wall
candidate as UNKNOWN. So a wall that never moved has its observed span reset to zero, and
`lm-wall.v2` R4 then refuses it for the next 10 s. With a 300 s cooldown, a trending market
can pay that cost every 5 minutes, and no wall can ever show a span longer than the interval
between two refreshes. The screen reports a book with no long-lived structure in it, which is a
statement about the collector's bookkeeping and not about the market.

The fix is not to refresh less often. It is to tell the two cases apart.

## HARD resync

A HARD resync is one where the stream itself was interrupted, so the collector was blind for an
interval it cannot bound from the data. **Wall persistence always ends at a HARD resync. There
is no evidence that may override this and no setting that relaxes it.**

Exhaustively, a resync is HARD when it follows any of:

* a websocket gap of any kind: `GAP_FIRST_DELTA`, `GAP_PU_MISMATCH`, `GAP_NON_INCREASING`,
  `GAP_MALFORMED_IDS`
* a disconnect or a reconnect (`RECONNECT`)
* a stale depth connection (`STALE`)
* a crossed book (`CROSSED_BOOK`)
* a level overflow (`LEVEL_OVERFLOW`) or a prefix-buffer overflow (`QUEUE_OVERFLOW`)
* a rejected or malformed snapshot (`SNAPSHOT_REJECTED`)
* any sequence or continuity failure not in the list above
* a session boundary: a new collector process, a new session id, or a restart

and also when a voluntary refresh fails any gate in the next section. **HARD is the default.**
A transition that cannot be positively proved SOFT is HARD.

## SOFT refresh

A SOFT refresh is a voluntary resnapshot that the collector asked for while holding a
synchronized book, and that was installed onto that same book without the stream breaking.
Only the two V1.1 voluntary triggers can ever be SOFT: `coverage_edge` and `safety_refresh`.

All five gates must hold at the moment the snapshot is installed. Any one of them failing makes
the transition HARD; this is a fail-closed rule.

**S1 VOLUNTARY.** The snapshot was requested by the resnapshot policy, not by the recovery path,
and the book was `SYNCED` when the request went out. A read that began as a refresh of a healthy
book but arrived after that book was invalidated is the recovery path, and is HARD.

**S2 CONTINUITY.** Between the request and the install, measured on the collector's own
counters: no new gap, no new connect or reconnect, no new stale event, no new prefix-buffer
overflow, no new snapshot rejection, and no other generation change. The pre-refresh book had
`first_delta_applied` true, so it was a fully synchronized book and not one still warming up.
After the install the book is `SYNCED` with no invalidation recorded.

**S3 CHAIN.** The new generation's book stands at **exactly the same `last_update_id`** as the
one it replaced.

This is the gate that changed in v3, and it changed because the one it replaces did not work. v1
and v2 asked whether the REST snapshot was *newer* than the live book, because the snapshot was
about to become the live book and a snapshot behind it would have moved the chain backwards.
Measured on a moving book, that refused every attempt: two requests in 23 minutes, two refusals,
zero installs, while the ±0.1% band left the known interval for 50 seconds. The snapshot was not
stale - an independent probe found it newer than the newest stream frame in 10 reads out of 10,
by 2,176 to 21,682 ids - but the comparison happened at *install* time, and during the round trip
the stream advanced past it, by 1,695 and 1,789 ids in the two measured cases.

So a voluntary refresh no longer replaces the live book at all. The snapshot becomes the base of
a **second** book; the frames that arrived during the round trip are replayed onto it; and the
swap happens only once that staged book stands where the live one does. The chain does not move,
so what S3 checks is that it did not.

Two ways the first frame after the snapshot may attach to the staged book, both exact:

* it **straddles** the snapshot id (`U <= lastUpdateId <= u`), the futures rule `btc-ms.v0.1`
  fixes; or
* it is the **immediate successor** (`pu == lastUpdateId`), which happens when the snapshot id
  lands on a frame boundary. The preceding frame ended at the snapshot's id, so nothing is
  missing between them.

Anything else is a hole, and the refresh is abandoned with the live book untouched.

**S4 BOUNDED WINDOW.** The elapsed time from REST request to install is at most **300 ms**,
**unless the install was a replayed chain, in which case the ceiling does not apply.** The
exemption is stated exactly, below. The 300 ms value is unchanged from v2 and so is every other
threshold in this document.

Why the ceiling exists at all. It keeps the carry inside a disclaimer the contract already
makes, and it is the honest limit of an *unstaged* install, so it is stated plainly: the events
between `book.last_update_id` and `snapshot.lastUpdateId` are not individually enumerated. Their
*result* is in the snapshot, but a level that was cancelled and re-placed at the same price
inside that window is indistinguishable from one that rested. V0 already discloses exactly this
for its 1 s sampling, so a window shorter than one sample interval adds **no new class of
unprovable claim** beyond the one already on the record. 300 ms is well inside that bound: the
measured REST round trips were 87, 99, 102, 119 and 143 ms. A slower read is refused rather than
stretched over, at a cost of one lost wall history.

**In a staged refresh that window contains nothing at all.** v3 said so and then kept the
ceiling anyway, on the ground that a slow REST read is a sign about the connection whether or
not the replay can repair it. That reasoning is withdrawn here, because it charges the wrong
account. A staged refresh replays every frame between the snapshot and the swap, individually,
through the same rules the live book used; there is no event inside the window whose effect was
absorbed rather than applied; and the swap happens only at an identical `last_update_id`. The
window therefore bounds an interval over which nothing is being claimed. Refusing the carry
because the read was slow does not make any statement on the screen more true - it deletes a
wall history that the replay *proved* - and it was measured doing exactly that: one natural
refresh in three, on 2026-10-04, had a 485 ms round trip, updated the book correctly, and had
its 276 candidates all turned to UNKNOWN by this gate while its own proof read
`chain_preserved: true`. A gate that is stricter than its own evidence is not caution, it is
noise. The connection signal is kept, and kept where it belongs: a slow read is published on the
transition and in telemetry, as a fact about the read, not as a verdict about the market.

**The exemption, exactly.** S4 is satisfied without the 300 ms ceiling when **all** of the
following hold at the moment of the swap:

1. the install was staged and the update-id chain was preserved across it
   (`chain_preserved` true, basis `REPLAYED_CHAIN_SAME_UPDATE_ID`);
2. **S1 VOLUNTARY** holds;
3. **S2 CONTINUITY** holds, which is the buffered-and-replayed chain's own continuity check: no
   new gap, connect, reconnect, stale event, buffer overflow or snapshot rejection between the
   request and the install, the book `SYNCED` with no invalidation after it, and the generation
   advanced by exactly one. This is also the clause that means no HARD fault occurred;
4. **S3 CHAIN** holds, that is `staging.last_update_id == live.last_update_id` at the swap;
5. the window was **measured** - a round trip the collector could not time is not exempt.

If any one of those does not hold, the 300 ms ceiling applies exactly as it did in v3, and a
window over it is HARD. The exemption is therefore not a relaxation of S4 for the general case:
it is the statement that in the one case where the window bounds nothing, it bounds nothing.

The exemption is not unbounded in practice, and that is worth saying because it is the only
thing standing between it and an arbitrarily old snapshot: a staged attempt that has not swapped
within `REFRESH_DEADLINE_MS` (1,000 ms) is abandoned by the collector and the live book is left
untouched, so no exempt carry can come from an attempt older than that. That deadline is an
existing V1.3 operational limit and is not changed here, nor is it a continuity gate - if it
were ever raised, the exemption's practical ceiling would rise with it, and that is a
consequence a future version has to accept deliberately rather than inherit.

Every transition publishes which of the three cases its window was in - `WITHIN_MAX`,
`EXEMPT_REPLAYED_CHAIN`, or `EXCEEDED_MAX` - alongside the measured window and the ceiling, so a
screen can never show a carry that used the exemption without showing that it used it.

**S5 OVERLAP.** The pre-refresh and post-refresh known intervals overlap, the overlap is
non-degenerate, and it contains both the pre-refresh mid and the post-refresh mid. At least one
level is comparable inside it. An overlap that is empty, degenerate, or that has moved off the
market fails closed.

## Per-candidate verdicts at a SOFT refresh

The gates above decide whether the *transition* may carry anything. Each candidate is then
judged on its own, against the newly installed snapshot, by exact price:

| Condition | Verdict |
|---|---|
| price inside **both** known intervals, and the installed snapshot holds that exact side and price with quantity > 0 | **eligible**, pending re-qualification |
| price inside both known intervals, and the installed snapshot does not hold it | **ENDED** (it was observed to be gone, in a region we can see) |
| price outside the new known interval | **UNKNOWN** (unobservable, never ENDED) |

An eligible candidate is **CARRIED** when it also re-qualifies under the V0 candidate rule on
the first sample of the new generation. An eligible candidate that does not re-qualify is
**ENDED**: it is still in the book but it is no longer a candidate, which is an observation.

A carried candidate keeps: its identity `(side, exact price)`, its origin `first_seen_ms` and
`first_seen_ns`, the number of samples it has been observed in, and a count of the SOFT
refreshes it has crossed. It does **not** keep its old `generation`, which belongs to the book.

## Wall identity at the V2 bin

`lm-wall.v2` groups members into 5.0 USDT bins and a wall *is* the bin. So wall-level continuity
needs a bin-level answer, and the bin-level answer is deliberately stricter than "the bin still
has a wall in it":

> A V2 wall carries its identity and its first_seen across a SOFT refresh **if and only if at
> least one of its members is a CARRIED member**. Its carried `first_seen_ms` is the earliest
> origin among its carried members.

Bin-only matching is rejected here, before anybody can prefer the number it produces. A bin
whose old member vanished and whose new member is a different order at a different price would
match on side and bin, and carrying that would be an identity claim about an order that was
never seen resting. Requiring a carried member makes the bin condition automatic rather than
assumed: a carried member has the same exact price, so it is in the same bin on both sides of
the transition, and "same side, same frozen V2 bin" holds by construction.

A wall with no carried member is a **new** wall, even if its bin held a wall a second ago.

## What a carry does to R4, and nothing else

For a wall with a carried member, R4 is evaluated against the longer of the candidate's own span
and its carried span:

```
own span      = latest sample ms - first_seen_ms              (lm-wall.v2, unchanged)
carried span  = latest sample ms - continuity first_seen_ms    (this rule, CARRIED only)
R4 input      = max(own span, carried span)
R4 threshold  = 10,000 ms                                      (unchanged)
```

Because a carried span is never shorter than the own span, this rule can only keep a wall that
R4 would otherwise have dropped. It can never drop a wall R4 would have kept. Both spans are
published on every wall, with the status that decided which one was used, so a screen can never
show a carried span without showing that it is carried.

R1, R2, R3 and R5 take no input from this rule.

## Telemetry

Every generation transition publishes, in the journal as a `telemetry` record (an existing
contract kind with an open event vocabulary) and in the collector's compact state checkpoint:

* `refresh_type`: `HARD` or `SOFT`
* `continuity_reason`: which gate decided it, in the vocabulary above
* `overlap_check`: the two known intervals, their overlap, whether both mids are inside it, and
  how many levels were comparable
* `window_ms`, `window_max_ms` and `window_verdict`: the measured REST round trip, the 300 ms
  ceiling, and which of `WITHIN_MAX`, `EXEMPT_REPLAYED_CHAIN`, `EXCEEDED_MAX` or
  `NOT_MEASURED` decided S4. A carry that used the exemption always says so
* `wall_carried`, `wall_ended`, `wall_unknown`: the three verdict counts, which sum to the
  number of candidates held before the transition
* the carried identities themselves, bounded and with a `truncated` flag, so the carry is
  auditable from the journal alone

## Where a carry may be read from

The ledger travels in the collector's compact state checkpoint and in the `wall_continuity`
telemetry record, and **never in a `wall` record**. That has a consequence which is a decision
rather than an accident, so it is written here:

> **A set reconstructed from the journal's `wall` transitions carries nothing.** The viewer's
> fallback path - used for an older session, a collector that predates the checkpoint, or a
> checkpoint that cannot be trusted - reports every wall as `NEW`, with the reason
> `COLLECTOR_PUBLISHES_NO_LEDGER`, and behaves exactly as V1.1 did.

Rebuilding the carry by replaying the `wall_continuity` telemetry stream alongside the
transitions is possible and is **deliberately not done**. The reconstruction path already carries
a weaker claim than the checkpoint (`JOURNAL_OPEN_ROW` values rather than current ones) and is
reached precisely when something about the collector's own account is missing or untrusted;
layering an inferred identity on top of that is the kind of stitching this rule exists to refuse.
A carry is shown only where the collector published it directly.

## The replay divergence check is evidence, not a gate

A staged swap compares the two books level by level inside the interval they both claim to
know, and publishes where they disagree. Across 33 forced and 3 natural swaps on 2026-10-04,
roughly 64,000 level comparisons produced zero differences. That number is now characterised,
which is exactly when somebody is tempted to turn it into a refusal.

It is deliberately not one, and v4 does not make it one. A divergence is evidence about the
exchange's snapshot or about this collector, and the right response to the first non-zero
reading is to look at it, not to have already decided in advance that some unmeasured count of
differing levels should silently end a few hundred wall histories. The check keeps its current
job: it is computed on every swap, published on the transition, and gates nothing. Adding a
threshold to it would be inventing a number, which is the thing this work refuses to do.

## What this rule still does not do

No direction. No rating or composite number of any kind. No spoofing, absorption or iceberg
verdict. `order_identity_proven` stays **false** on every wall, carried or not: a carry proves
that the same side and the same exact price held a qualifying quantity continuously across a
bounded window, which is not proof that one order rested there. The claim a carry makes is
exactly as strong as the claim V0's sampled span already makes, and no stronger.

No order path, no automatic action, no change to the display filter, and no new tunable. There
is nothing in this rule an operator can move from a screen.

## What a failed refresh costs

Nothing. A staged refresh that cannot reach S3 is abandoned: the live book keeps its levels, its
bounds, its ids and its generation, no wall observation ends, and no `wall` record is written. So
a failure is not allowed to consume the 300 s cooldown either - that floor exists to bound how
often a **successful** refresh destroys wall observation, and an attempt that changed nothing
destroyed nothing. A failed attempt takes a short backoff instead.

This is not a licence to retry without limit: consecutive failures are counted and published, so
a refresh that can never succeed is visible as a pattern rather than as a trickle of single
failures nobody adds up.

## Changelog

**v4 (2026-10-04, this document).** One change, and it is to when S4 applies rather than to what
it says:

1. **S4 BOUNDED WINDOW gains one exemption.** A staged refresh whose chain was preserved, and
   that satisfies S1, S2 and S3 with a measured round trip, is not subject to the 300 ms
   ceiling. The five conditions are listed under S4 and all five are required; failing any one
   of them puts the 300 ms ceiling back exactly as v3 had it. The window's verdict
   (`WITHIN_MAX`, `EXEMPT_REPLAYED_CHAIN`, `EXCEEDED_MAX`) is published on every transition.
2. Recorded, not a new rule: a slow REST read remains a fact worth publishing and is still
   published, as a property of the read rather than as a verdict about wall continuity; and the
   V1.3 operational deadline of 1,000 ms per staged attempt is what bounds an exempt window in
   practice.

Unchanged in v4, explicitly: the 300 ms value itself; HARD as the default and the exhaustive
HARD list; S1, S2, S3 and S5; the per-candidate verdicts; the bin-level identity rule; the
effect on R4; every `lm-wall.v2` threshold and the 500,000 USDT display filter; the session
boundary being HARD; a journal reconstruction carrying nothing; the 300 s cooldown on a
successful refresh and the 10 s backoff on a failed one; and the replay divergence check
remaining evidence rather than a gate.

**v3 (2026-10-04).** One change, and it is to how a refresh is installed rather
than to what may be carried:

1. **S3 NEWER became S3 CHAIN.** A voluntary refresh is staged on a second book and swapped in
   only at an identical `last_update_id`, so the live book is never rolled back and the gate
   checks that the chain did not move instead of that the snapshot was ahead of it. The two
   attachment rules for the first frame after the snapshot are stated above.
2. Consequences recorded, not new rules: the S4 window now contains no unenumerated event in the
   staged case, and a failed refresh costs nothing and therefore consumes no cooldown.

Unchanged in v3: HARD remains the default and the exhaustive HARD list is untouched; SOFT still
requires all five gates; the SOFT window is still 300 ms; a session boundary is still HARD; a
journal reconstruction still carries nothing; the per-candidate verdicts, the bin-level identity
rule, the effect on R4 and every `lm-wall.v2` threshold are as they were.

**v2 (2026-10-04).** Two changes, both narrowing:

1. **S4 BOUNDED WINDOW: 1000 ms to 300 ms.** v1 set the ceiling at one sample interval, which is
   the bound that makes the argument work. The measured round trips sit at 87-143 ms, so the
   ceiling had an order of magnitude of slack, and slack in a gate is only ever an opportunity to
   carry something that should not have been carried. 300 ms keeps a normal read passing and
   refuses one that has gone slow.
2. **"Where a carry may be read from" added**, stating that a journal reconstruction carries
   nothing and that rebuilding the carry from the telemetry stream is refused.

Unchanged in v2: the HARD and SOFT definitions, the other four gates, the per-candidate verdicts,
the bin-level identity rule, the effect on R4, and every `lm-wall.v2` threshold.
