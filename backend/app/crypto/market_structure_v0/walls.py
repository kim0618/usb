"""Wall *candidates*: large resting levels, tracked and never interpreted.

V0 records that a price level is unusually large for its neighbourhood and how long it stayed
that way. It does not decide whether that is spoofing, absorption, an iceberg or an honest limit
order, and it carries no vocabulary for those. The reason is that the data cannot support the
distinction: this collector sees aggregated depth, not orders. A level that shrinks may have been
cancelled or filled, and the depth stream alone cannot say which. So the output is a candidate
with a persistence measurement, and the labels stay out.

What a candidate means, precisely:

* The comparison set is up to 5 adjacent **occupied** levels on each side of the level in price
  order, excluding the level itself. Empty prices are not neighbours; on a book with gaps, the
  five nearest resting levels are the local neighbourhood, not the five nearest ticks.
* Fewer than 3 neighbours and there is no candidate at all. A level with one neighbour is not
  three times a local average, it is half the sample.
* The threshold is `qty >= 3 x mean(neighbours)`.
* Candidates are looked for within +-1% of mid, on both sides, from the once-per-second sample.

`persistence_ms` is the span between the first and last sample in which the level qualified. It
is a **sampled observation span and not proof of continuous order identity**: a level that
disappears between two samples and returns with the same price and size is indistinguishable
from one that never left, and an order that was cancelled and replaced at the same price looks
identical to one that rested. Anything a resync or a loss of freshness interrupts ends as
`UNKNOWN` rather than `ENDED`, because continuity was not observed, and a candidate carries the
book `generation` so it can never appear to have survived a resnapshot.

**The continuity ledger (V1.2) does not change one word of the paragraph above.** Every journal
`wall` record keeps the fields and the behaviour the frozen contract fixes: at a generation
change each held candidate is still written `UNKNOWN`, and a candidate that re-qualifies is
still a new candidate with its own `first_seen_ms`. What is added is a second, separate
bookkeeping line that answers a different question: *was this exact level provably resting
across that transition?* The collector is the only process that can answer it, because it is the
one holding the websocket chain and the book, and it answers it only for a **voluntary refresh**
whose five gates all held (`SoftRefreshProof`). The answer travels in `state_row()` and in a
`telemetry` record, never in a `wall` payload, and the viewer's frozen `lm-continuity.v1` rule
decides what to do with it. A HARD resync carries nothing, and a transition that cannot be
proved is HARD.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .contract import (COMPLETE, DEPTH_STALE_MS, PARTIAL, UNKNOWN, WALL_BAND,
                       WALL_MIN_NEIGHBOURS, WALL_MULTIPLE, WALL_NEIGHBOURS_PER_SIDE)
from .envelope import decimal_out
from . import book as B

ACTIVE = "ACTIVE"
ENDED = "ENDED"
#: Continuity was interrupted, so the candidate's end is not an observation of its end.
INTERRUPTED = "UNKNOWN"

OPENED = "OPENED"
UPDATED = "UPDATED"

# ----------------------------------------------------------------- continuity across a resync
#
# Added for Liquidity Map V1.2. The two words below are the whole of it.
#
# A **HARD** resync is one where the stream broke: a gap of any kind, a disconnect or reconnect,
# a stale connection, a crossed book, an overflow, a rejected snapshot, a session boundary. The
# collector was blind for an interval it cannot bound from the data, so nothing is carried, ever.
# HARD is also the default: a transition that is not positively proved SOFT is HARD.
#
# A **SOFT** refresh is one of the two voluntary resnapshots V1.1 added - the coverage-edge
# refresh and the hourly safety refresh - installed onto a book that stayed synchronized from the
# request to the install. Five gates decide it, all of them required, and `SoftRefreshProof`
# records which ones held so a transition can be audited rather than believed.

HARD = "HARD"
SOFT = "SOFT"

#: The five gates, in the order the frozen rule states them.
GATE_VOLUNTARY = "VOLUNTARY"
GATE_CONTINUITY = "CONTINUITY"
GATE_NEWER = "NEWER"
GATE_BOUNDED_WINDOW = "BOUNDED_WINDOW"
GATE_OVERLAP = "OVERLAP"
SOFT_GATES = (GATE_VOLUNTARY, GATE_CONTINUITY, GATE_NEWER, GATE_BOUNDED_WINDOW, GATE_OVERLAP)

#: Why a transition was classified as it was. One code, the first gate that failed.
REASON_SOFT_PROVEN = "SOFT_ALL_GATES_PASSED"
REASON_NOT_VOLUNTARY = "HARD_NOT_A_VOLUNTARY_REFRESH"
REASON_STREAM_BROKE = "HARD_STREAM_DISCONTINUITY"
REASON_NOT_NEWER = "HARD_SNAPSHOT_NOT_NEWER"
REASON_WINDOW_EXCEEDED = "HARD_REST_WINDOW_EXCEEDS_SAMPLE_INTERVAL"
REASON_NO_OVERLAP = "HARD_SNAPSHOT_OVERLAP_INCONSISTENT"
REASON_SUPERSEDED = "HARD_PROOF_SUPERSEDED"
REASON_PROOF_EXPIRED = "HARD_PROOF_DID_NOT_MATCH_GENERATION"
REASON_NOT_FRESH = "HARD_BOOK_NOT_FRESH_AT_SAMPLE"
REASON_INTERRUPTED = "HARD_CONTINUITY_INTERRUPTED"
#: A gate failure maps to exactly one reason, so a HARD verdict always names a cause.
GATE_REASONS = {
    GATE_VOLUNTARY: REASON_NOT_VOLUNTARY,
    GATE_CONTINUITY: REASON_STREAM_BROKE,
    GATE_NEWER: REASON_NOT_NEWER,
    GATE_BOUNDED_WINDOW: REASON_WINDOW_EXCEEDED,
    GATE_OVERLAP: REASON_NO_OVERLAP,
}

#: A candidate's continuity status. `NEW` is the honest default: no earlier life that this
#: collector can prove. There is deliberately no third value for "had one, lost it at a HARD
#: resync": tracking that would mean holding identity across exactly the boundary this rule
#: refuses to cross. What a HARD resync destroyed is published as a count on the transition
#: (`carried_lost`) instead, where it costs nothing and claims nothing.
CARRY_NEW = "NEW"
CARRY_CARRIED = "CARRIED"

#: Per-candidate verdicts at a SOFT refresh, decided against the newly installed snapshot.
VERDICT_ELIGIBLE = "ELIGIBLE"
VERDICT_ENDED = "ENDED"
VERDICT_UNKNOWN = "UNKNOWN"

#: How the new generation's book was obtained. A staged refresh replays every frame between the
#: snapshot and the swap, so the chain is unbroken and nothing in the window was absorbed; an
#: unstaged install jumps the chain to the snapshot's id and the window is unenumerated.
REPLAYED_CHAIN = "REPLAYED_CHAIN_SAME_UPDATE_ID"
SNAPSHOT_INSTALL = "SNAPSHOT_INSTALLED_ON_LIVE_BOOK"

#: What the collector claims when it marks a candidate carried. Deliberately stated in the
#: collector's own terms: it proved a thing about an exact price across a bounded window, and the
#: viewer's own frozen rule decides what a wall may do with that.
CARRY_PROOF = "SOFT_REFRESH_EXACT_PRICE_PRESENT_IN_INSTALLED_SNAPSHOT"

#: Longest REST request-to-install window a SOFT refresh may have, in milliseconds, **unless the
#: install was a replayed chain**. The events inside the window of an *unstaged* install are not
#: individually enumerated, so keeping it under the 1 s sampling granularity keeps a carry inside
#: the disclaimer the contract already makes instead of adding a new unprovable claim on top of
#: it. `lm-continuity.v1` set it at the sampling interval itself; **v2 narrowed it to 300 ms**
#: because the measured round trips are 87-143 ms, so the ceiling was an order of magnitude of
#: slack, and slack in a gate is only an opportunity to carry something that should not have been
#: carried. The value is unchanged in v3 and v4.
SOFT_WINDOW_MAX_MS = 300

#: Which of the three cases decided S4. Published on every transition, because a carry that used
#: the exemption must never be indistinguishable on a screen from one that was inside the
#: ceiling. `NOT_MEASURED` is a window the collector could not time, and is never exempt.
WINDOW_WITHIN_MAX = "WITHIN_MAX"
WINDOW_EXEMPT = "EXEMPT_REPLAYED_CHAIN"
WINDOW_EXCEEDED = "EXCEEDED_MAX"
WINDOW_NOT_MEASURED = "NOT_MEASURED"

#: Most carried identities published per transition. The same discipline as every other buffer
#: here; the count stays exact and `truncated` says when the list was cut.
TRANSITION_MAX_CARRIED = 2_000


@dataclass(frozen=True)
class BookWindow:
    """What the book looked like at one instant, in as much detail as a proof needs.

    Two of these bracket a snapshot install: one taken before `apply_snapshot` and one after. The
    collector builds them; nothing here reaches into the book, so the proof can be constructed in
    a test from plain values and the gates can be checked without a socket.
    """

    generation: int
    synced: bool
    first_delta_applied: bool
    last_update_id: int | None
    known_low: Decimal | None
    known_high: Decimal | None
    mid: Decimal | None
    #: `side -> price -> quantity`, copied, because deltas keep mutating the live maps.
    levels: dict[str, dict[Decimal, Decimal]] = field(default_factory=dict)
    #: The fault counters that must not have moved: gaps, connects, stale events, buffer
    #: overflows, rejected snapshots. Compared as a whole rather than one by one, so a counter
    #: added later is covered by this gate without anybody remembering to add it here.
    faults: tuple[tuple[str, int], ...] = ()
    invalidation: str | None = None

    def level(self, side: str, price: Decimal) -> Decimal | None:
        return (self.levels.get(side) or {}).get(price)


#: Stands in for a book that was not there. Every bound is None, so every gate that reads it
#: fails, which is the behaviour a missing observation must have.
_NO_BOOK = BookWindow(generation=-1, synced=False, first_delta_applied=False, last_update_id=None,
                      known_low=None, known_high=None, mid=None)


def _empty_overlap() -> dict[str, Any]:
    """The overlap check of a transition where there was nothing to compare."""
    return _overlap_check(_NO_BOOK, _NO_BOOK)


def _overlap_check(before: BookWindow, after: BookWindow) -> dict[str, Any]:
    """Whether the two known intervals describe the same piece of market, and by how much.

    The quantities are published for an operator, but only `passed` gates anything. The gate is
    deliberately about the *interval*, not about agreement between the levels: the snapshot is
    newer than the deltas already applied, so levels inside the overlap are allowed to differ,
    and a threshold on how many of them agreed would be a number nobody could justify. What is
    not allowed is an overlap that is empty, degenerate, or no longer around the market.
    """
    bounds = (before.known_low, before.known_high, after.known_low, after.known_high)
    check: dict[str, Any] = {
        "before_known_low": decimal_out(before.known_low),
        "before_known_high": decimal_out(before.known_high),
        "after_known_low": decimal_out(after.known_low),
        "after_known_high": decimal_out(after.known_high),
        "before_mid": decimal_out(before.mid),
        "after_mid": decimal_out(after.mid),
        "overlap_low": None, "overlap_high": None, "overlap_width": None,
        "before_mid_inside": None, "after_mid_inside": None,
        "levels_compared": 0, "levels_identical": 0, "levels_changed": 0,
        "levels_only_before": 0, "levels_only_after": 0,
        "passed": False,
    }
    if any(bound is None for bound in bounds):
        return check
    low = max(before.known_low, after.known_low)      # type: ignore[arg-type]
    high = min(before.known_high, after.known_high)   # type: ignore[arg-type]
    check["overlap_low"], check["overlap_high"] = decimal_out(low), decimal_out(high)
    check["overlap_width"] = decimal_out(high - low)
    if high <= low:
        return check
    inside = [before.mid is not None and low <= before.mid <= high,
              after.mid is not None and low <= after.mid <= high]
    check["before_mid_inside"], check["after_mid_inside"] = inside[0], inside[1]

    for side in ("BID", "ASK"):
        was = {price: qty for price, qty in (before.levels.get(side) or {}).items()
               if low <= price <= high}
        now = {price: qty for price, qty in (after.levels.get(side) or {}).items()
               if low <= price <= high}
        for price, qty in was.items():
            if price not in now:
                check["levels_only_before"] += 1
                continue
            check["levels_compared"] += 1
            if now[price] == qty:
                check["levels_identical"] += 1
            else:
                check["levels_changed"] += 1
        check["levels_only_after"] += sum(1 for price in now if price not in was)

    check["passed"] = bool(inside[0] and inside[1] and check["levels_compared"] > 0)
    return check


@dataclass(frozen=True)
class SoftRefreshProof:
    """One generation transition, classified, with the evidence that classified it.

    Built by `classify_refresh` at the moment a snapshot is installed. A SOFT proof is handed to
    the tracker, which consumes it once, on the first sample of the new generation. A HARD proof
    is still built and still published, because "why was this HARD" is the question an operator
    asks when a wall's history disappears.
    """

    refresh_type: str
    continuity_reason: str
    generation: int
    reason: str | None
    gates: tuple[tuple[str, bool], ...]
    overlap: dict[str, Any]
    elapsed_ms: int | None
    snapshot_update_id: int | None
    previous_last_update_id: int | None
    after: BookWindow | None = None
    before_known_low: Decimal | None = None
    before_known_high: Decimal | None = None
    #: V1.3: how many frames were replayed onto the staged book before the swap, and whether the
    #: update-id chain was preserved across it. When it was, the events between the snapshot and
    #: the swap were applied individually rather than absorbed, so the window S4 bounds contains
    #: no unenumerated event at all.
    replayed_frames: int | None = None
    chain_preserved: bool = False
    #: V1.4: which of the three cases satisfied (or failed) S4. `EXEMPT_REPLAYED_CHAIN` means the
    #: 300 ms ceiling did not apply because the window bounded nothing: every frame in it was
    #: replayed onto the staged book individually and the swap happened at an identical update
    #: id. Carried on the proof so the exemption can never be used invisibly.
    window_verdict: str = WINDOW_NOT_MEASURED

    @property
    def is_soft(self) -> bool:
        return self.refresh_type == SOFT

    def verdict(self, side: str, price: Decimal) -> str:
        """What this transition says about one candidate, by exact price.

        Three outcomes and the difference between the last two is the whole point: a level we
        could see and that is gone has been **observed** to end, while a level that is now
        outside the known interval has not been observed at all and must never be reported as
        ended. Fail closed: no post-refresh window means UNKNOWN for everything.
        """
        after = self.after
        if after is None or after.known_low is None or after.known_high is None:
            return VERDICT_UNKNOWN
        if self.before_known_low is None or self.before_known_high is None:
            return VERDICT_UNKNOWN
        inside_both = (after.known_low <= price <= after.known_high
                       and self.before_known_low <= price <= self.before_known_high)
        if not inside_both:
            return VERDICT_UNKNOWN
        quantity = after.level(side, price)
        if quantity is None or quantity <= 0:
            return VERDICT_ENDED
        return VERDICT_ELIGIBLE

    def view(self) -> dict[str, Any]:
        return {
            "refresh_type": self.refresh_type,
            "continuity_reason": self.continuity_reason,
            "generation": self.generation,
            "reason": self.reason,
            "gates": dict(self.gates),
            "gates_required": list(SOFT_GATES),
            "overlap_check": self.overlap,
            "window_ms": self.elapsed_ms,
            "window_max_ms": SOFT_WINDOW_MAX_MS,
            "snapshot_update_id": self.snapshot_update_id,
            "previous_last_update_id": self.previous_last_update_id,
            "replayed_frames": self.replayed_frames,
            "chain_preserved": self.chain_preserved,
            "basis": REPLAYED_CHAIN if self.chain_preserved else SNAPSHOT_INSTALL,
            "window_verdict": self.window_verdict,
            "window_exempt": self.window_verdict == WINDOW_EXEMPT,
        }


def classify_refresh(before: BookWindow, after: BookWindow, *, voluntary: bool,
                     reason: str | None, elapsed_ms: int | None,
                     snapshot_update_id: int | None, replayed_frames: int | None = None,
                     chain_preserved: bool = False) -> SoftRefreshProof:
    """Decide HARD or SOFT for one snapshot install, and record why.

    The gates are evaluated in the frozen rule's order and the published reason is the **first**
    one that failed, which is the one an operator can act on. Every gate is a positive statement
    that has to be true; nothing here infers SOFT from the absence of a recorded fault.
    """
    overlap = _overlap_check(before, after)
    # S3 reads differently once the install is staged. V1.2 asked whether the snapshot was newer
    # than the live book, because it was about to replace it; a snapshot that was not newer would
    # have moved the chain backwards. V1.3 never replaces the book - it swaps in a staged one that
    # already stands at the same update id - so what has to be true is that the chain did **not**
    # move, which is a stronger statement and the one checked here. The older form is kept for
    # the unstaged path, where it is still exactly the right question.
    if chain_preserved:
        newer = bool(before.last_update_id is not None
                     and after.last_update_id == before.last_update_id)
    else:
        newer = bool(snapshot_update_id is not None and before.last_update_id is not None
                     and snapshot_update_id > before.last_update_id)
    voluntary_held = bool(voluntary and before.synced and before.first_delta_applied)
    continuity_held = bool(
        after.synced and after.invalidation is None
        and before.faults == after.faults
        and after.generation == before.generation + 1)

    # S4 in v4. The ceiling is unchanged and so is the measurement; what changed is the one case
    # in which the ceiling has nothing to bound. A staged refresh replays every frame between the
    # snapshot and the swap individually and swaps only at an identical update id, so there is no
    # absorbed event inside the window for the ceiling to protect against - and v3 was measured
    # deleting a proven carry on a 485 ms read whose own proof said `chain_preserved: true`.
    #
    # The exemption is written as a conjunction of the gates it depends on rather than as a trust
    # in `chain_preserved` alone, so that it cannot outlive them: a staged install whose stream
    # broke, whose chain moved, or that was not voluntary gets the 300 ms ceiling back, and an
    # unmeasured window is never exempt. Fail closed in every direction.
    window_measured = elapsed_ms is not None and elapsed_ms >= 0
    window_within = bool(window_measured and elapsed_ms <= SOFT_WINDOW_MAX_MS)
    window_exempt = bool(window_measured and not window_within and chain_preserved
                         and voluntary_held and continuity_held and newer)
    if window_within:
        window_verdict = WINDOW_WITHIN_MAX
    elif window_exempt:
        window_verdict = WINDOW_EXEMPT
    elif window_measured:
        window_verdict = WINDOW_EXCEEDED
    else:
        window_verdict = WINDOW_NOT_MEASURED

    gates: dict[str, bool] = {
        GATE_VOLUNTARY: voluntary_held,
        GATE_CONTINUITY: continuity_held,
        GATE_NEWER: newer,
        GATE_BOUNDED_WINDOW: window_within or window_exempt,
        GATE_OVERLAP: bool(overlap.get("passed")),
    }
    failed = [gate for gate in SOFT_GATES if not gates[gate]]
    refresh_type = SOFT if not failed else HARD
    return SoftRefreshProof(
        refresh_type=refresh_type,
        continuity_reason=REASON_SOFT_PROVEN if not failed else GATE_REASONS[failed[0]],
        generation=after.generation, reason=reason, gates=tuple(gates.items()), overlap=overlap,
        elapsed_ms=elapsed_ms, snapshot_update_id=snapshot_update_id,
        previous_last_update_id=before.last_update_id,
        after=after if refresh_type == SOFT else None,
        before_known_low=before.known_low, before_known_high=before.known_high,
        replayed_frames=replayed_frames, chain_preserved=chain_preserved,
        window_verdict=window_verdict)


@dataclass
class Candidate:
    """A level that has qualified at least once, and what has been seen of it since."""

    side: str
    price: Decimal
    generation: int
    first_seen_ms: int
    first_seen_ns: int
    last_seen_ms: int
    last_seen_ns: int
    current_size: Decimal
    local_average: Decimal
    multiple: Decimal
    neighbours: int
    samples: int = 1
    coverage: str = COMPLETE
    #: Trajectory summary, so an ended candidate describes its whole life in one record instead
    #: of one record per sample. See `WallTracker.emit_updates`.
    max_size: Decimal | None = None
    min_size: Decimal | None = None
    max_multiple: Decimal | None = None
    # --- continuity ledger (V1.2). None of this reaches `payload()`; see `state_row`.
    #: Where the observation this candidate continues actually began. Equal to `first_seen_*`
    #: unless a SOFT refresh carried an earlier life into it.
    origin_first_seen_ms: int = 0
    origin_first_seen_ns: int = 0
    origin_generation: int = 0
    carry_status: str = CARRY_NEW
    #: How many SOFT refreshes this observation has been carried across.
    carried_refreshes: int = 0
    #: Samples observed before the first carry, so the total is not lost with the old candidate.
    carried_samples: int = 0

    def __post_init__(self) -> None:
        self.max_size = self.current_size
        self.min_size = self.current_size
        self.max_multiple = self.multiple
        self.origin_first_seen_ms = self.first_seen_ms
        self.origin_first_seen_ns = self.first_seen_ns
        self.origin_generation = self.generation

    def carry_from(self, previous: "Candidate") -> None:
        """Continue `previous`: its origin, its sample count and its carry count move here.

        The candidate's own `first_seen_*` and `generation` are left alone. They describe this
        generation's candidate and they are what the journal row says, so the two accounts stay
        comparable instead of one quietly overwriting the other.
        """
        self.origin_first_seen_ms = previous.origin_first_seen_ms
        self.origin_first_seen_ns = previous.origin_first_seen_ns
        self.origin_generation = previous.origin_generation
        self.carried_samples = previous.carried_samples + previous.samples
        self.carried_refreshes = previous.carried_refreshes + 1
        self.carry_status = CARRY_CARRIED

    @property
    def continuity_persistence_ms(self) -> int:
        """The span since the origin: the same measurement, over the proven-continuous life."""
        return max(0, (self.last_seen_ns - self.origin_first_seen_ns) // 1_000_000)

    @property
    def continuity_samples(self) -> int:
        return self.carried_samples + self.samples

    def observe(self, size: Decimal, multiple: Decimal) -> None:
        self.max_size = size if self.max_size is None else max(self.max_size, size)
        self.min_size = size if self.min_size is None else min(self.min_size, size)
        self.max_multiple = (multiple if self.max_multiple is None
                             else max(self.max_multiple, multiple))

    @property
    def persistence_ms(self) -> int:
        return max(0, (self.last_seen_ns - self.first_seen_ns) // 1_000_000)

    def payload(self, *, status: str, event: str) -> dict[str, Any]:
        return {
            "side": self.side,
            "price": decimal_out(self.price),
            "bin": decimal_out(self.price),
            "bin_rule": "EXACT_PRICE",
            "qty": decimal_out(self.current_size),
            "current_size": decimal_out(self.current_size),
            "notional": decimal_out(self.price * self.current_size),
            "local_average": decimal_out(self.local_average),
            "multiple": decimal_out(self.multiple),
            "max_size": decimal_out(self.max_size),
            "min_size": decimal_out(self.min_size),
            "max_multiple": decimal_out(self.max_multiple),
            "neighbours": self.neighbours,
            "first_seen_ms": self.first_seen_ms,
            "last_seen_ms": self.last_seen_ms,
            "persistence_ms": self.persistence_ms,
            "samples": self.samples,
            "status": status,
            "event": event,
            "coverage": self.coverage,
            "generation": self.generation,
            "persistence_is_sampled_span": True,
            "order_identity_proven": False,
        }


    def state_row(self) -> dict[str, Any]:
        """The compact form for the state file: what a reader needs, and nothing it can rebuild.

        `payload()` is the journal record and keeps everything, including the trajectory summary
        and the two standing disclaimers. This file is rewritten every second for the life of the
        collector, so it carries only the fields a reader cannot derive: the identity, the current
        size and its evidence, the observation span, and the coverage and generation that qualify
        them. The journal remains the place to read a candidate's whole history.
        """
        return {
            "side": self.side,
            "price": decimal_out(self.price),
            "qty": decimal_out(self.current_size),
            "notional": decimal_out(self.price * self.current_size),
            "local_average": decimal_out(self.local_average),
            "multiple": decimal_out(self.multiple),
            "neighbours": self.neighbours,
            "first_seen_ms": self.first_seen_ms,
            "last_seen_ms": self.last_seen_ms,
            "persistence_ms": self.persistence_ms,
            "samples": self.samples,
            "status": ACTIVE,
            "event": UPDATED,
            "coverage": self.coverage,
            "generation": self.generation,
            # The continuity ledger. Additive, and only here: a `wall` journal payload keeps the
            # fields the frozen contract fixes, so these cannot change a stored dataset.
            "continuity_status": self.carry_status,
            "continuity_first_seen_ms": self.origin_first_seen_ms,
            "continuity_persistence_ms": self.continuity_persistence_ms,
            "continuity_samples": self.continuity_samples,
            "continuity_refreshes": self.carried_refreshes,
            "continuity_origin_generation": self.origin_generation,
            "continuity_proof": CARRY_PROOF if self.carry_status == CARRY_CARRIED else None,
        }


def _neighbour_mean(prices: list[Decimal], levels: dict[Decimal, Decimal],
                    index: int) -> tuple[Decimal | None, int]:
    """Mean of up to 5 occupied levels on each side of `index`, excluding it."""
    low = max(0, index - WALL_NEIGHBOURS_PER_SIDE)
    high = min(len(prices), index + WALL_NEIGHBOURS_PER_SIDE + 1)
    sizes = [levels[prices[i]] for i in range(low, high) if i != index]
    if len(sizes) < WALL_MIN_NEIGHBOURS:
        return None, len(sizes)
    return sum(sizes, Decimal(0)) / Decimal(len(sizes)), len(sizes)


def _qualifying(levels: dict[Decimal, Decimal], low: Decimal, high: Decimal
                ) -> dict[Decimal, tuple[Decimal, Decimal, Decimal, int]]:
    """Levels inside `[low, high]` that clear the multiple, with their comparison evidence."""
    prices = sorted(levels)
    found: dict[Decimal, tuple[Decimal, Decimal, Decimal, int]] = {}
    for index, price in enumerate(prices):
        if not (low <= price <= high):
            continue
        mean, count = _neighbour_mean(prices, levels, index)
        if mean is None or mean <= 0:
            continue
        size = levels[price]
        if size < WALL_MULTIPLE * mean:
            continue
        found[price] = (size, mean, size / mean, count)
    return found


@dataclass
class WallTracker:
    """Per-second candidate lifecycle. Returns the records to persist for each sample.

    Detection runs every second. **Persistence is by transition**: a candidate is written when it
    opens and when it ends, and the ending record carries `samples`, `persistence_ms` and the
    trajectory summary, so an active candidate is on disk from the moment it opens and its whole
    life is described when it finishes.

    That is a measurement, not a preference. A 600 s live run on 2026-10-04 produced 278
    qualifying candidates **per sample** out of about 1,950 retained levels, because the
    contract's rule (3x the mean of up to 5 occupied neighbours on each side) matches roughly 15%
    of levels on a real book, where the size distribution is strongly skewed; the median multiple
    observed was 7.3. Writing one row per candidate per second made the wall stream **88.5% of
    all bytes** at about 722 bytes a row, which would be the dominant cost of the 24 h trial and
    would still be a stream of levels that are not walls in any useful sense. Transitions were
    12.5% of those rows. Raw depth stays the authority for a level's full size history, so
    nothing is lost that cannot be rebuilt offline.

    `emit_updates=True` restores a row per candidate per sample for a short study that genuinely
    needs the sampled trajectory inline. It is off by default because of the figures above.
    """

    emit_updates: bool = False
    candidates: dict[tuple[str, Decimal], Candidate] = field(default_factory=dict)
    last_generation: int | None = None
    opened: int = 0
    ended: int = 0
    interrupted: int = 0
    # --- continuity ledger (V1.2)
    #: The classification of the most recent snapshot install, waiting for the sample that will
    #: see the generation change. Consumed exactly once, and void the moment anything else ends
    #: candidate continuity.
    pending_proof: SoftRefreshProof | None = None
    #: The most recent generation transition, as telemetry and the state file publish it.
    last_transition: dict[str, Any] | None = None
    soft_refreshes: int = 0
    hard_transitions: int = 0
    carried: int = 0
    continuity_ended: int = 0
    continuity_unknown: int = 0
    proofs_superseded: int = 0
    #: V1.4: how many of the SOFT refreshes passed S4 by the replayed-chain exemption rather than
    #: by being inside the 300 ms ceiling. Published beside `soft_refreshes` so the share of
    #: carries that rest on the exemption is a number on the screen and not an inference.
    soft_window_exempt: int = 0

    # ------------------------------------------------------------------ continuity

    def note_refresh(self, proof: SoftRefreshProof) -> None:
        """Record how a snapshot install was classified, for the next sample to act on.

        A second install before any sample has seen the first one voids both: the generation
        transition in between was never observed, so candidates held now belong to a book two
        generations back and the newer proof's interval says nothing about them. Superseding
        therefore fails closed rather than using the proof that happens to be newest.
        """
        if self.pending_proof is not None:
            self.proofs_superseded += 1
            self.pending_proof = SoftRefreshProof(
                refresh_type=HARD, continuity_reason=REASON_SUPERSEDED,
                generation=proof.generation, reason=proof.reason, gates=proof.gates,
                overlap=proof.overlap, elapsed_ms=proof.elapsed_ms,
                snapshot_update_id=proof.snapshot_update_id,
                previous_last_update_id=proof.previous_last_update_id,
                replayed_frames=proof.replayed_frames,
                chain_preserved=proof.chain_preserved,
                window_verdict=proof.window_verdict)
            return
        self.pending_proof = proof

    def _hard_proof(self, reason: str, generation: int) -> SoftRefreshProof:
        return SoftRefreshProof(
            refresh_type=HARD, continuity_reason=reason, generation=generation, reason=None,
            gates=tuple((gate, False) for gate in SOFT_GATES), overlap=_empty_overlap(),
            elapsed_ms=None, snapshot_update_id=None, previous_last_update_id=None)

    def _transition(self, generation: int, *, receive_ms: int
                    ) -> tuple[dict[tuple[str, Decimal], Candidate], dict[str, Any]]:
        """Judge every held candidate against the classification of this generation change.

        Returns the candidates that may be continued, and the transition summary so far. The
        summary is finished by `sample` once it knows which of them re-qualified, because
        "eligible" is a statement about the snapshot and "carried" is a statement about the
        candidate rule, and the two are different claims.
        """
        proof = self.pending_proof
        self.pending_proof = None
        held = list(self.candidates.values())
        if proof is None:
            proof = self._hard_proof(REASON_INTERRUPTED, generation)
        elif proof.is_soft and proof.generation != generation:
            # The book moved on past the generation this proof was taken for.
            proof = self._hard_proof(REASON_PROOF_EXPIRED, generation)
        summary: dict[str, Any] = {
            "refresh_type": proof.refresh_type,
            "continuity_reason": proof.continuity_reason,
            "cause": None,
            "generation_from": self.last_generation,
            "generation_to": generation,
            "receive_ms": receive_ms,
            "candidates_before": len(held),
            # How many candidates arrived already carrying a proven history.
            "carried_entering": sum(1 for candidate in held
                                    if candidate.carry_status == CARRY_CARRIED),
            # And how much of that history this transition destroyed. On a HARD resync it is all
            # of it. On a SOFT refresh it is only the carried candidates that did **not** carry
            # through, which is the number worth warning about: counting every carried candidate
            # as lost would fire a warning on every healthy refresh and teach an operator to
            # ignore the one that matters.
            "carried_lost": 0,
            "eligible": 0,
            "wall_carried": 0,
            "wall_ended": 0,
            "wall_unknown": 0,
            "carried_identities": [],
            "carried_truncated": False,
            "proof": proof.view(),
        }
        if not proof.is_soft:
            # A HARD resync observed nothing ending: every candidate is UNKNOWN, which is both
            # the contract's answer and this ledger's.
            self.hard_transitions += 1
            summary["wall_unknown"] = len(held)
            summary["carried_lost"] = summary["carried_entering"]
            self.continuity_unknown += len(held)
            self.last_transition = summary
            return {}, summary

        self.soft_refreshes += 1
        if proof.window_verdict == WINDOW_EXEMPT:
            self.soft_window_exempt += 1
        eligible: dict[tuple[str, Decimal], Candidate] = {}
        for candidate in held:
            verdict = proof.verdict(candidate.side, candidate.price)
            if verdict == VERDICT_ELIGIBLE:
                eligible[(candidate.side, candidate.price)] = candidate
            elif verdict == VERDICT_ENDED:
                summary["wall_ended"] += 1
                self.continuity_ended += 1
            else:
                summary["wall_unknown"] += 1
                self.continuity_unknown += 1
            if (verdict != VERDICT_ELIGIBLE
                    and candidate.carry_status == CARRY_CARRIED):
                summary["carried_lost"] += 1
        summary["eligible"] = len(eligible)
        self.last_transition = summary
        return eligible, summary

    def _finish_transition(self, summary: dict[str, Any],
                           unclaimed: dict[tuple[str, Decimal], Candidate]) -> None:
        """An eligible candidate nobody claimed was in the book and is no longer a candidate.

        That is an observation, not an interruption, so it counts as ended rather than unknown.
        """
        summary["wall_ended"] += len(unclaimed)
        self.continuity_ended += len(unclaimed)
        summary["carried_lost"] += sum(1 for candidate in unclaimed.values()
                                       if candidate.carry_status == CARRY_CARRIED)
        identities = summary["carried_identities"]
        summary["carried_truncated"] = len(identities) > TRANSITION_MAX_CARRIED
        del identities[TRANSITION_MAX_CARRIED:]

    def _close_all(self, status: str) -> list[dict[str, Any]]:
        records = [candidate.payload(status=status, event=status)
                   for candidate in self.candidates.values()]
        if status == INTERRUPTED:
            self.interrupted += len(records)
        else:
            self.ended += len(records)
        self.candidates.clear()
        # Whatever ends candidate continuity also ends the right to carry anything across it.
        # This is the one line that makes every fault path HARD without each of them having to
        # remember to say so.
        self.pending_proof = None
        return records

    def sample(self, depth: B.DepthBook, *, at_ns: int, receive_ms: int,
               stale_ms: int = DEPTH_STALE_MS) -> list[dict[str, Any]]:
        """Observe the book once. Candidate continuity ends on any loss of a usable book."""
        age_ms = depth.age_ms(at_ns)
        mid = depth.mid()
        fresh = depth.state == B.SYNCED and age_ms is not None and age_ms <= stale_ms
        if not fresh or mid is None:
            # Not observed, so not an ending: the candidates end as UNKNOWN. A book that is not
            # usable at the sample cannot have carried anything across a refresh either, even if
            # the refresh itself was clean, so the pending proof dies with the candidates.
            was = self.last_generation
            self.last_generation = None
            carried_now = sum(1 for candidate in self.candidates.values()
                              if candidate.carry_status == CARRY_CARRIED)
            if self.candidates or self.pending_proof is not None:
                self.last_transition = {
                    "refresh_type": HARD, "continuity_reason": REASON_NOT_FRESH,
                    "cause": depth.last_invalidation,
                    "generation_from": was, "generation_to": depth.generation,
                    "receive_ms": receive_ms, "candidates_before": len(self.candidates),
                    "carried_entering": carried_now, "carried_lost": carried_now,
                    "eligible": 0, "wall_carried": 0, "wall_ended": 0,
                    "wall_unknown": len(self.candidates), "carried_identities": [],
                    "carried_truncated": False,
                    "proof": self._hard_proof(REASON_NOT_FRESH, depth.generation).view()}
            return self._close_all(INTERRUPTED)

        records: list[dict[str, Any]] = []
        carry: dict[tuple[str, Decimal], Candidate] = {}
        summary: dict[str, Any] | None = None
        if self.last_generation is not None and depth.generation != self.last_generation:
            carry, summary = self._transition(depth.generation, receive_ms=receive_ms)
            records.extend(self._close_all(INTERRUPTED))
        elif (self.pending_proof is not None
                and self.pending_proof.generation <= depth.generation):
            # A proof for the generation this sample is already in. No transition was detected,
            # so it can never be consumed by a future one, and the commonest case is the very
            # first sample of a session: the startup snapshot's install left a proof behind that
            # no transition will ever claim. Dropping it here is what keeps the next voluntary
            # refresh from being superseded by it.
            self.pending_proof = None
        self.last_generation = depth.generation

        assert depth.known_low is not None and depth.known_high is not None
        bid_low = max(mid * (Decimal(1) - WALL_BAND), depth.known_low)
        ask_high = min(mid * (Decimal(1) + WALL_BAND), depth.known_high)
        # A band reaching past the snapshot bound means the neighbourhood is only partly known,
        # which is recorded on the candidate rather than silently ignored.
        coverage = COMPLETE
        if (mid * (Decimal(1) - WALL_BAND) < depth.known_low
                or mid * (Decimal(1) + WALL_BAND) > depth.known_high):
            coverage = PARTIAL

        qualifying: dict[tuple[str, Decimal], tuple[Decimal, Decimal, Decimal, int]] = {}
        for side, levels, low, high in (("BID", depth.bids, bid_low, mid),
                                        ("ASK", depth.asks, mid, ask_high)):
            for price, evidence in _qualifying(levels, low, high).items():
                qualifying[(side, price)] = evidence

        for key, candidate in list(self.candidates.items()):
            if key not in qualifying:
                records.append(candidate.payload(status=ENDED, event=ENDED))
                self.ended += 1
                del self.candidates[key]

        for (side, price), (size, mean, multiple, neighbours) in qualifying.items():
            existing = self.candidates.get((side, price))
            if existing is None:
                candidate = Candidate(
                    side=side, price=price, generation=depth.generation,
                    first_seen_ms=receive_ms, first_seen_ns=at_ns, last_seen_ms=receive_ms,
                    last_seen_ns=at_ns, current_size=size, local_average=mean,
                    multiple=multiple, neighbours=neighbours, coverage=coverage)
                previous = carry.pop((side, price), None)
                if previous is not None and summary is not None:
                    # Proven present in the installed snapshot, and qualifying again now. This is
                    # the only place a candidate's observation is allowed to continue past a
                    # generation, and it continues the ledger, never the journal row.
                    candidate.carry_from(previous)
                    self.carried += 1
                    summary["wall_carried"] += 1
                    summary["carried_identities"].append({
                        "side": side, "price": decimal_out(price),
                        "continuity_first_seen_ms": candidate.origin_first_seen_ms,
                        "continuity_persistence_ms": candidate.continuity_persistence_ms,
                        "continuity_samples": candidate.continuity_samples,
                        "continuity_refreshes": candidate.carried_refreshes,
                        "origin_generation": candidate.origin_generation})
                self.candidates[(side, price)] = candidate
                self.opened += 1
                records.append(candidate.payload(status=ACTIVE, event=OPENED))
                continue
            existing.last_seen_ms = receive_ms
            existing.last_seen_ns = at_ns
            existing.current_size = size
            existing.local_average = mean
            existing.multiple = multiple
            existing.neighbours = neighbours
            existing.coverage = coverage if existing.coverage == COMPLETE else existing.coverage
            existing.samples += 1
            existing.observe(size, multiple)
            if self.emit_updates:
                records.append(existing.payload(status=ACTIVE, event=UPDATED))
        if summary is not None:
            self._finish_transition(summary, carry)
        return records

    def shutdown(self, cause: str | None = None) -> list[dict[str, Any]]:
        """A clean stop did not observe these candidates ending either.

        `cause` is the book's own invalidation vocabulary - the gap reason, `RECONNECT`, `STALE`,
        or a shutdown - reused rather than mirrored into a second set of codes. It is recorded
        because an operator watching a long-lived wall disappear asks what ended it, and the
        answer has to exist before the question.
        """
        held = len(self.candidates)
        carried_lost = sum(1 for candidate in self.candidates.values()
                           if candidate.carry_status == CARRY_CARRIED)
        records = self._close_all(INTERRUPTED)
        if held:
            self.hard_transitions += 1
            self.continuity_unknown += held
            self.last_transition = {
                "refresh_type": HARD, "continuity_reason": REASON_INTERRUPTED,
                "cause": cause, "generation_from": self.last_generation,
                "generation_to": self.last_generation, "receive_ms": None,
                "candidates_before": held, "carried_entering": carried_lost,
                "carried_lost": carried_lost, "eligible": 0,
                "wall_carried": 0, "wall_ended": 0, "wall_unknown": held,
                "carried_identities": [], "carried_truncated": False,
                "proof": self._hard_proof(REASON_INTERRUPTED,
                                          self.last_generation or 0).view()}
        return records

    def counters(self) -> dict[str, int]:
        return {"active": len(self.candidates), "opened": self.opened, "ended": self.ended,
                "interrupted": self.interrupted, "carried": self.carried,
                "soft_refreshes": self.soft_refreshes,
                "hard_transitions": self.hard_transitions,
                "continuity_ended": self.continuity_ended,
                "continuity_unknown": self.continuity_unknown,
                "proofs_superseded": self.proofs_superseded,
                "soft_window_exempt": self.soft_window_exempt}

    def continuity_view(self) -> dict[str, Any]:
        """The ledger as the state file and the screen publish it.

        `carried` is the only number here that can keep a wall on screen, and it is published
        beside the two that cannot, because a reader who sees only the carries cannot tell a
        quiet book from a rule that is carrying everything.
        """
        return {
            "proof": CARRY_PROOF,
            "window_max_ms": SOFT_WINDOW_MAX_MS,
            "gates_required": list(SOFT_GATES),
            "active_carried": sum(1 for candidate in self.candidates.values()
                                  if candidate.carry_status == CARRY_CARRIED),
            "soft_refreshes": self.soft_refreshes,
            "soft_window_exempt": self.soft_window_exempt,
            "hard_transitions": self.hard_transitions,
            "wall_carried_total": self.carried,
            "wall_ended_total": self.continuity_ended,
            "wall_unknown_total": self.continuity_unknown,
            "proofs_superseded": self.proofs_superseded,
            "pending_proof": None if self.pending_proof is None else self.pending_proof.view(),
            "last_transition": self.last_transition,
        }

    def state_payloads(self, *, limit: int) -> tuple[list[dict[str, Any]], bool]:
        """Every candidate resting right now, largest first, and whether the list was cut.

        The journal says when a candidate opened and when it ended; it never contains a line
        saying "these are resting now", because that set only exists here. A reader that has to
        rebuild it walks the transition stream backwards, which costs more the longer the session
        has run and can only be *proven* complete against a separate count. This method is that
        set, read straight out of the dictionary that is the authority for it, with each
        candidate's **current** size rather than the size it had when its OPENED row was written.

        Sorted by current notional descending so that a `limit` that bites removes the smallest
        candidates. Truncation that could hide the largest wall would make the cap worse than no
        cap at all, and the caller is told when it happened rather than left to assume.
        """
        ordered = sorted(self.candidates.values(),
                         key=lambda candidate: candidate.price * candidate.current_size,
                         reverse=True)
        truncated = len(ordered) > limit
        return ([candidate.state_row() for candidate in ordered[:limit]], truncated)


__all__ = ["WallTracker", "Candidate", "ACTIVE", "ENDED", "INTERRUPTED", "OPENED", "UPDATED",
           "BookWindow", "SoftRefreshProof", "classify_refresh", "HARD", "SOFT", "SOFT_GATES",
           "GATE_VOLUNTARY", "GATE_CONTINUITY", "GATE_NEWER", "GATE_BOUNDED_WINDOW",
           "GATE_OVERLAP", "GATE_REASONS", "REASON_SOFT_PROVEN", "REASON_NOT_VOLUNTARY",
           "REASON_STREAM_BROKE", "REASON_NOT_NEWER", "REASON_WINDOW_EXCEEDED",
           "REASON_NO_OVERLAP", "REASON_SUPERSEDED", "REASON_PROOF_EXPIRED", "REASON_NOT_FRESH",
           "REASON_INTERRUPTED", "CARRY_NEW", "CARRY_CARRIED", "CARRY_PROOF",
           "VERDICT_ELIGIBLE", "VERDICT_ENDED", "VERDICT_UNKNOWN", "SOFT_WINDOW_MAX_MS",
           "REPLAYED_CHAIN", "SNAPSHOT_INSTALL", "WINDOW_WITHIN_MAX", "WINDOW_EXEMPT",
           "WINDOW_EXCEEDED", "WINDOW_NOT_MEASURED",
           "TRANSITION_MAX_CARRIED"]
