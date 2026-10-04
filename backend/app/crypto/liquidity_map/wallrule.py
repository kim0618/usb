"""Wall selection V2: which V0 candidates the screen is willing to call a wall.

The rule is frozen in `docs/crypto/liquidity_map_v1/WALL_RULE_V2.md` (`lm-wall.v2`), hashed
beside it, and was frozen before the sets it selects were looked at. This module implements that
document and nothing else; `rule_identity()` reports whether the document on disk still hashes to
what was recorded, so a rule that drifted away from its own specification is visible rather than
silent.

Why a selection rule at all, stated once here because it is the whole design:

The V0 contract's candidate rule - at least 3 occupied neighbours each side, at least 3x their
mean, within +-1% of mid, once a second - is **loose on purpose**. It feeds a dataset, and a
superset can be narrowed later while a subset cannot be widened without re-collecting. Measured
on the live book it qualified 284 of 1,997 retained levels, whose median notional was 165,607
USDT: that is ordinary book, not a wall. Worse, the top of the book is finely sliced, so the
local mean collapses there and a trivially small level clears "3x its neighbours" by 77x, in one
sample 1,473x. The published "nearest major wall" was then the touch level itself.

So the multiple is not wrong, it is simply unable to answer the question alone. V2 adds the three
things it cannot see - absolute money, distance from mid, and how long the level has actually been
observed - and then groups adjacent survivors, because nine qualifying levels inside five dollars
are one structure and drawing them as nine walls misreads the same data.

Two orderings are part of the rule and both are easy to get backwards:

* **Filter members, then group.** Grouping first and summing into the thresholds would let nine
  ordinary levels add up to a wall that no level in the group is.
* **The bin is anchored in absolute price, not in bps of mid.** A bps-defined bin moves its own
  edges as mid moves, so a wall would change identity without changing.

Nothing here is tunable from the UI. The operator's display filter is a separate, later narrowing
applied on top of this, and is allowed to move precisely because this is not.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from ..market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN
from ..market_structure_v0.envelope import decimal_out

#: Version of the selection rule. Independent of `btc-ms.v0.1`, which this does not change.
RULE_VERSION = "lm-wall.v2"
RULE_RELATIVE_PATH = "docs/crypto/liquidity_map_v1/WALL_RULE_V2.md"

# --------------------------------------------------------------------------- the thresholds
#
# Every figure below is justified in the frozen document against the measured distribution, and
# the document is the specification. They are repeated here because code has to contain them;
# `test_liquidity_map_wallrule.py` asserts each one appears in the frozen text, so the two cannot
# drift apart quietly.

#: R1. A wall is first of all big, in money, independently of its neighbourhood.
MIN_NOTIONAL_USDT = Decimal("250000")
#: R2. And it stands out locally. Modest on purpose: R1 carries the absolute question, and a high
#: multiple preferentially deletes large walls that happen to sit in a thick neighbourhood.
MIN_MULTIPLE = Decimal("5")
#: R3. And it is not the touch. 19 of 284 candidates sat inside 1 bp with a quarter of the median
#: notional; this is where the slicing that inflates multiples lives.
MIN_DISTANCE_BPS = Decimal("1.0")
#: R4. And it has been seen for more than one sample. Low on purpose: observation cannot cross a
#: session boundary, so a high floor blanks the screen for minutes after every restart.
#: **Unchanged by V1.2.** What V1.2 changed is the span this floor is applied to, for a candidate
#: the collector proved continuous across a SOFT refresh: see `continuity.py` and the frozen
#: `lm-continuity.v1`. Because a carried span is never shorter than the candidate's own, that
#: rule can only keep a wall this floor would have dropped, never drop one it would have kept.
MIN_PERSISTENCE_MS = 10_000
#: R5. BTCUSDT futures price increment, as the venue publishes it.
TICK_SIZE = Decimal("0.1")
#: R5. Bin width, stated in ticks so that bin edges are stable in absolute price.
BIN_WIDTH_TICKS = 50
BIN_WIDTH_USDT = TICK_SIZE * BIN_WIDTH_TICKS

#: Why a candidate was not shown. Counted and published, so "no walls" is never ambiguous
#: between "none qualify" and "none were looked for".
REJECT_NOTIONAL = "BELOW_MIN_NOTIONAL"
REJECT_MULTIPLE = "BELOW_MIN_MULTIPLE"
REJECT_DISTANCE = "INSIDE_MIN_DISTANCE"
REJECT_PERSISTENCE = "BELOW_MIN_PERSISTENCE"
REJECT_UNUSABLE = "FIELD_MISSING_OR_UNPARSEABLE"
REJECT_NO_MID = "NO_USABLE_MID"
REJECT_REASONS = (REJECT_NOTIONAL, REJECT_MULTIPLE, REJECT_DISTANCE, REJECT_PERSISTENCE,
                  REJECT_UNUSABLE, REJECT_NO_MID)

#: Which moment a wall's figures describe. The checkpoint carries the collector's current sample;
#: a journal reconstruction carries each candidate's OPENED row, which can be far older.
VALUES_CHECKPOINT = "CHECKPOINT_CURRENT"
VALUES_JOURNAL_OPEN_ROW = "JOURNAL_OPEN_ROW"

#: Which span R4 was measured against. `continuity.py` restates these two so that a reader of
#: either module sees the vocabulary; a test asserts the two agree.
SOURCE_OWN = "OWN"
SOURCE_CARRIED = "CARRIED"

SIDE_ASK = "ASK"
SIDE_BID = "BID"


def repo_root() -> Path:
    """`backend/app/crypto/liquidity_map/wallrule.py` -> repository root."""
    return Path(__file__).resolve().parents[4]


def rule_path() -> Path:
    return repo_root() / RULE_RELATIVE_PATH


def rule_identity(path: Path | None = None) -> dict[str, Any]:
    """The rule's version, its hash as computed now, and whether the sidecar still agrees.

    A missing document is reported rather than raised, for the same reason the V0 contract does
    it: the viewer must be runnable from a checkout that ships code without docs, and it then
    says so out loud instead of implying a frozen rule it cannot see.
    """
    target = path or rule_path()
    recorded_path = target.with_suffix(".sha256")
    identity: dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "rule_relative_path": RULE_RELATIVE_PATH,
        "rule_sha256": None,
        "recorded_sha256": None,
        "sha256_agrees": None,
        "status": "MISSING",
    }
    if not target.exists():
        return identity
    identity["rule_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    identity["status"] = "PRESENT"
    if recorded_path.exists():
        recorded = recorded_path.read_text(encoding="utf-8").split()
        identity["recorded_sha256"] = recorded[0] if recorded else None
        identity["sha256_agrees"] = identity["recorded_sha256"] == identity["rule_sha256"]
    return identity


def rule_view() -> dict[str, Any]:
    """The rule as the screen states it. Frozen figures, not settings."""
    return {
        "rule_version": RULE_VERSION,
        "identity": rule_identity(),
        "is_frozen_not_tunable": True,
        "applies_to": "V0_CANDIDATE_SUPERSET",
        "changes_data_contract": False,
        "min_notional_usdt": str(MIN_NOTIONAL_USDT),
        "min_multiple": str(MIN_MULTIPLE),
        "min_distance_bps": str(MIN_DISTANCE_BPS),
        "min_persistence_ms": MIN_PERSISTENCE_MS,
        "bin_width_usdt": decimal_out(BIN_WIDTH_USDT),
        "bin_width_ticks": BIN_WIDTH_TICKS,
        "tick_size": str(TICK_SIZE),
        "bin_rule": "FLOOR_ABSOLUTE_PRICE_PER_SIDE",
        "bin_representative": "LARGEST_NOTIONAL_MEMBER",
        "evaluation_order": "FILTER_MEMBERS_THEN_GROUP",
        "v0_rule": {"min_multiple": "3", "min_neighbours": 3, "neighbours_per_side": 5,
                    "band_pct": "1", "bin_rule": "EXACT_PRICE"},
    }


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def distance_bps(price: Decimal, mid: Decimal, side: str) -> Decimal | None:
    """Distance from mid towards the far side, in basis points. Negative means through mid."""
    if mid <= 0:
        return None
    gap = (price - mid) if side == SIDE_ASK else (mid - price)
    return gap / mid * Decimal(10_000)


def price_bin(price: Decimal) -> Decimal:
    """The bin's lower edge, anchored in absolute price so a bin keeps its identity."""
    return (price / BIN_WIDTH_USDT).to_integral_value(rounding="ROUND_FLOOR") * BIN_WIDTH_USDT


@dataclass(frozen=True)
class Member:
    """One V0 candidate measured against the rule, kept with everything the screen needs."""

    payload: dict[str, Any]
    side: str
    price: Decimal
    qty: Decimal
    notional: Decimal
    multiple: Decimal
    distance_bps: Decimal
    persistence_ms: int
    bin_low: Decimal
    coverage: str
    #: The candidate's own span, as this rule has always measured it.
    own_span_ms: int = 0
    #: The span over the continuity-proven life, when `lm-continuity.v1` certified one. Kept
    #: beside the own span rather than replacing it, so both reach the screen and the one R4
    #: used is a named choice instead of an overwritten field.
    carried_span_ms: int | None = None
    persistence_source: str = "OWN"

    @property
    def bin_high(self) -> Decimal:
        return self.bin_low + BIN_WIDTH_USDT


@dataclass
class Selection:
    """What the rule produced, and the full accounting of what it refused."""

    walls: list[dict[str, Any]]
    considered: int = 0
    passed: int = 0
    rejected: dict[str, int] | None = None
    bins: int = 0
    grouped_away: int = 0
    values_as_of: str = VALUES_JOURNAL_OPEN_ROW

    def view(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "passed": self.passed,
            "rejected": dict(self.rejected or {}),
            "bins": self.bins,
            "grouped_away": self.grouped_away,
            "values_as_of": self.values_as_of,
        }


def observed_persistence_ms(payload: dict[str, Any], latest_sample_ms: int | None) -> int | None:
    """The span a candidate has been observed for, measured the way the contract defines it.

    A resting candidate's journal row is its OPENED row, whose `persistence_ms` is 0 and whose
    `samples` is 1 forever, so the row's own field cannot be used. The span is therefore the
    newest sample this viewer can see minus `first_seen_ms`. The collector's own `persistence_ms`
    is preferred when it is larger, which is the case in the compact checkpoint, where the figure
    is live.
    """
    first_seen = payload.get("first_seen_ms")
    row = payload.get("persistence_ms")
    spans = [value for value in (
        (max(0, latest_sample_ms - first_seen)
         if isinstance(first_seen, int) and isinstance(latest_sample_ms, int) else None),
        row if isinstance(row, int) else None,
    ) if value is not None]
    return max(spans) if spans else None


def evaluate(payload: dict[str, Any], *, mid: Decimal | None, latest_sample_ms: int | None,
             carried_span: Callable[[dict[str, Any], int | None], int | None] | None = None
             ) -> tuple[Member | None, str | None]:
    """One candidate against R1-R4. Returns the member, or the reason it was refused.

    `carried_span` is the continuity rule, passed in rather than imported, so that this module
    stays the implementation of `lm-wall.v2` alone and the two frozen rules cannot grow a
    circular dependency. Omitted, the behaviour is exactly V1.1's.
    """
    if mid is None or mid <= 0:
        return None, REJECT_NO_MID
    side = str(payload.get("side") or "")
    price = _decimal(payload.get("price"))
    qty = _decimal(payload.get("qty"))
    notional = _decimal(payload.get("notional"))
    multiple = _decimal(payload.get("multiple"))
    if side not in (SIDE_ASK, SIDE_BID) or price is None or notional is None or multiple is None:
        return None, REJECT_UNUSABLE
    distance = distance_bps(price, mid, side)
    persistence = observed_persistence_ms(payload, latest_sample_ms)
    if distance is None or persistence is None:
        return None, REJECT_UNUSABLE
    carried = None if carried_span is None else carried_span(payload, latest_sample_ms)
    span = persistence if carried is None else max(persistence, carried)
    source = SOURCE_CARRIED if carried is not None and carried > persistence else SOURCE_OWN
    # Checked in rule order so the published reason is the first thing the candidate failed,
    # which is the one an operator can act on.
    if notional < MIN_NOTIONAL_USDT:
        return None, REJECT_NOTIONAL
    if multiple < MIN_MULTIPLE:
        return None, REJECT_MULTIPLE
    if distance < MIN_DISTANCE_BPS:
        return None, REJECT_DISTANCE
    if span < MIN_PERSISTENCE_MS:
        return None, REJECT_PERSISTENCE
    return Member(payload=payload, side=side, price=price, qty=qty or Decimal(0),
                  notional=notional, multiple=multiple, distance_bps=distance,
                  persistence_ms=span, bin_low=price_bin(price),
                  coverage=str(payload.get("coverage") or UNKNOWN),
                  own_span_ms=persistence, carried_span_ms=carried,
                  persistence_source=source), None


def _bin_coverage(members: list[Member], known_low: Decimal | None,
                  known_high: Decimal | None) -> str:
    """A bin is COMPLETE only if its whole price range was inside the observed interval.

    The members' own coverage comes first: a candidate the collector already marked PARTIAL
    cannot be promoted by this view. Then the bin's own edges are tested, because a bin that
    straddles the boundary is partly in a region the book never reached, and a wall reported as
    complete there would be a claim about unobserved space.
    """
    if any(member.coverage == UNKNOWN for member in members):
        return UNKNOWN
    if any(member.coverage == PARTIAL for member in members):
        return PARTIAL
    if known_low is None or known_high is None:
        return UNKNOWN
    low = min(member.bin_low for member in members)
    high = max(member.bin_high for member in members)
    return COMPLETE if low >= known_low and high <= known_high else PARTIAL


def select(payloads: list[dict[str, Any]], *, side: str, mid: Decimal | None,
           latest_sample_ms: int | None, known_low: Decimal | None = None,
           known_high: Decimal | None = None,
           values_as_of: str = VALUES_JOURNAL_OPEN_ROW,
           carried_span: Callable[[dict[str, Any], int | None], int | None] | None = None,
           wall_continuity: Callable[[list[Member], int | None], dict[str, Any]] | None = None
           ) -> Selection:
    """Apply the frozen rule to one side's candidates. Nearest to mid first.

    The two callables are `lm-continuity.v1`, injected. Without them this is V1.1 exactly: no
    candidate's span is extended and no wall carries an identity.
    """
    rejected: dict[str, int] = {reason: 0 for reason in REJECT_REASONS}
    members: list[Member] = []
    considered = 0
    for payload in payloads:
        if str(payload.get("side") or "") != side:
            continue
        considered += 1
        member, reason = evaluate(payload, mid=mid, latest_sample_ms=latest_sample_ms,
                                  carried_span=carried_span)
        if member is None:
            rejected[reason or REJECT_UNUSABLE] += 1
            continue
        members.append(member)

    groups: dict[Decimal, list[Member]] = {}
    for member in members:
        groups.setdefault(member.bin_low, []).append(member)

    walls: list[dict[str, Any]] = []
    for bin_low, group in groups.items():
        group.sort(key=lambda item: item.notional, reverse=True)
        lead = group[0]
        coverage = _bin_coverage(group, known_low, known_high)
        walls.append({
            "side": lead.side,
            "price": lead.payload.get("price"),
            "qty_btc": lead.payload.get("qty"),
            "notional_usdt": lead.payload.get("notional"),
            "multiple": lead.payload.get("multiple"),
            "local_average": lead.payload.get("local_average"),
            "neighbours": lead.payload.get("neighbours"),
            "distance_bps": str(lead.distance_bps.quantize(Decimal("0.01"))),
            # Three spans rather than one. `observed_persistence_ms` is the figure R4 was applied
            # to; the other two say what it was made of, so a carried span can never appear on a
            # screen without the screen being able to say that it is carried.
            "observed_persistence_ms": lead.persistence_ms,
            "own_persistence_ms": lead.own_span_ms,
            "persistence_source": lead.persistence_source,
            "first_seen_ms": lead.payload.get("first_seen_ms"),
            "generation": lead.payload.get("generation"),
            "coverage": coverage,
            "values_as_of": values_as_of,
            "bin_low": decimal_out(bin_low),
            "bin_high": decimal_out(bin_low + BIN_WIDTH_USDT),
            "bin_members": len(group),
            # A sum over qualifying candidates only. Levels in this bin that did not qualify are
            # not counted, so this is a lower bound on the bin and is never its liquidity.
            "bin_candidate_notional_usdt": decimal_out(sum((item.notional for item in group),
                                                           Decimal(0))),
            "bin_candidate_notional_is_lower_bound": True,
            "persistence_is_sampled_span": True,
            "order_identity_proven": False,
            "_price": lead.price,
        })
        if wall_continuity is not None:
            walls[-1].update(wall_continuity(group, latest_sample_ms))

    # Nearest to mid first: the lowest ask, the highest bid.
    walls.sort(key=lambda wall: wall["_price"], reverse=(side == SIDE_BID))
    for wall in walls:
        wall.pop("_price", None)
    return Selection(walls=walls, considered=considered, passed=len(members), rejected=rejected,
                     bins=len(groups), grouped_away=len(members) - len(groups),
                     values_as_of=values_as_of)


__all__ = ["RULE_VERSION", "RULE_RELATIVE_PATH", "MIN_NOTIONAL_USDT", "MIN_MULTIPLE",
           "MIN_DISTANCE_BPS", "MIN_PERSISTENCE_MS", "TICK_SIZE", "BIN_WIDTH_TICKS",
           "BIN_WIDTH_USDT", "REJECT_NOTIONAL", "REJECT_MULTIPLE", "REJECT_DISTANCE",
           "REJECT_PERSISTENCE", "REJECT_UNUSABLE", "REJECT_NO_MID", "REJECT_REASONS",
           "VALUES_CHECKPOINT", "VALUES_JOURNAL_OPEN_ROW", "SOURCE_OWN", "SOURCE_CARRIED",
           "SIDE_ASK", "SIDE_BID", "Member",
           "Selection", "repo_root", "rule_path", "rule_identity", "rule_view", "distance_bps",
           "price_bin", "observed_persistence_ms", "evaluate", "select"]
