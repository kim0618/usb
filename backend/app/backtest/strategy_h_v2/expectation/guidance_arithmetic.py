"""D4 code-owned guidance arithmetic (brief §6/§7/§25).

The split this module enforces:

- The AI READS a guidance sentence and reports the two ranges it found, each with the evidence_id
  it came from, verbatim as numbers the source states.
- CODE decides what moved. `classify_range_movement` compares the two bound pairs; the AI's own
  `GuidanceState` for that metric must agree with it (`validate.py` rejects the output otherwise).
  So "guidance was raised" is never the model's arithmetic or the model's adjective - it is a
  comparison this module performed on two numbers the model only transcribed.

Why the classifier compares BOUNDS rather than midpoints: a range moving from 1.00-1.20 to
1.05-1.15 has an unchanged midpoint and is not "maintained" in any sense a reader cares about - the
company narrowed it, which is a statement about confidence, not level. Reducing that to a midpoint
delta of 0.0% would erase the only thing that happened. `MIXED_BOUNDS` is therefore a real outcome,
and `midpoint_change` / `width_change` are reported alongside it rather than instead of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.backtest.strategy_h_v2.expectation.gap_contract import GuidanceState


class GuidanceUnit(StrEnum):
    USD = "USD"
    USD_PER_SHARE = "USD_PER_SHARE"
    PERCENT = "PERCENT"
    COUNT = "COUNT"
    OTHER = "OTHER"


class RangeMovement(StrEnum):
    """What CODE can determine from two numeric ranges alone."""

    RAISED = "RAISED"
    LOWERED = "LOWERED"
    MAINTAINED = "MAINTAINED"
    MIXED_BOUNDS = "MIXED_BOUNDS"
    """One bound up and the other down or unchanged - a narrowing, a widening, or a one-sided
    revision. Reported as its own outcome rather than resolved to a direction by midpoint."""
    NOT_COMPARABLE = "NOT_COMPARABLE"
    """Different units, or one side absent. Never silently coerced."""


@dataclass(frozen=True)
class GuidanceRange:
    """A guidance range exactly as a source states it. A point guidance ("about $1.10") is a range
    whose bounds are equal - a separate point type would double every code path below for no
    additional expressiveness."""

    low: float
    high: float
    unit: GuidanceUnit

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"guidance range high {self.high} is below low {self.low}")

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2

    @property
    def width(self) -> float:
        return self.high - self.low

    def to_dict(self) -> dict:
        return {"low": self.low, "high": self.high, "unit": self.unit.value,
                "midpoint": self.midpoint}


@dataclass(frozen=True)
class GuidanceComparison:
    movement: RangeMovement
    midpoint_change_abs: float | None
    midpoint_change_pct: float | None
    """Relative change of midpoints. `None` when the prior midpoint is 0 or crosses sign - a
    percentage change through zero is not a percentage change, and reporting one would be the
    `cap_vs_actual`-style arithmetic artifact this whole layer exists to keep out."""
    width_change_abs: float | None
    low_change_abs: float | None
    high_change_abs: float | None

    def to_dict(self) -> dict:
        return {
            "movement": self.movement.value, "midpoint_change_abs": self.midpoint_change_abs,
            "midpoint_change_pct": self.midpoint_change_pct,
            "width_change_abs": self.width_change_abs, "low_change_abs": self.low_change_abs,
            "high_change_abs": self.high_change_abs,
        }


def classify_range_movement(
    previous: GuidanceRange | None, current: GuidanceRange | None,
) -> GuidanceComparison:
    """The whole of code's guidance judgement. Pure arithmetic - no source text is read here."""
    if previous is None or current is None or previous.unit != current.unit:
        return GuidanceComparison(RangeMovement.NOT_COMPARABLE, None, None, None, None, None)
    low_delta = current.low - previous.low
    high_delta = current.high - previous.high
    if low_delta > 0 and high_delta > 0:
        movement = RangeMovement.RAISED
    elif low_delta < 0 and high_delta < 0:
        movement = RangeMovement.LOWERED
    elif low_delta == 0 and high_delta == 0:
        movement = RangeMovement.MAINTAINED
    else:
        movement = RangeMovement.MIXED_BOUNDS
    midpoint_abs = current.midpoint - previous.midpoint
    midpoint_pct = (
        None if previous.midpoint == 0 or (previous.midpoint < 0) != (current.midpoint < 0)
        else midpoint_abs / abs(previous.midpoint)
    )
    return GuidanceComparison(
        movement=movement, midpoint_change_abs=midpoint_abs, midpoint_change_pct=midpoint_pct,
        width_change_abs=current.width - previous.width,
        low_change_abs=low_delta, high_change_abs=high_delta,
    )


#: How a code-determined `RangeMovement` maps onto the frozen `GuidanceState` enum the AI must
#: report for that same metric. Only these four are code-determinable: `INITIATED`, `WITHDRAWN`,
#: `NOT_PROVIDED` and `UNKNOWN` are statements about the ABSENCE of a range, which arithmetic over
#: two ranges cannot reach, and `MIXED` at the metric level is `MIXED_BOUNDS`.
MOVEMENT_TO_STATE: dict[RangeMovement, GuidanceState] = {
    RangeMovement.RAISED: GuidanceState.RAISED,
    RangeMovement.LOWERED: GuidanceState.LOWERED,
    RangeMovement.MAINTAINED: GuidanceState.MAINTAINED,
    RangeMovement.MIXED_BOUNDS: GuidanceState.MIXED,
}


def state_disagrees_with_arithmetic(
    reported: GuidanceState, comparison: GuidanceComparison,
) -> bool:
    """True when the AI's per-metric state contradicts what the two numbers actually did.

    `NOT_COMPARABLE` never disagrees: if code could not compare, code has no standing to reject.
    """
    expected = MOVEMENT_TO_STATE.get(comparison.movement)
    return expected is not None and reported != expected


def result_vs_range(actual: float, guided: GuidanceRange) -> str:
    """Where a reported actual falls relative to the company's own prior guidance range.

    Returns a `ResultVsCompanyGuidance` value as a string (imported as a value, not the enum, to
    keep this arithmetic module free of any dependency on the gap contract's decision vocabulary).
    Tolerance is exact: a result at a bound is WITHIN, because the company's own range included it.
    """
    if actual > guided.high:
        return "ABOVE_COMPANY_GUIDANCE"
    if actual < guided.low:
        return "BELOW_COMPANY_GUIDANCE"
    return "WITHIN_COMPANY_GUIDANCE"


#: Deterministic, code-owned location of guidance-bearing language. This finds WHERE guidance might
#: be stated; it never decides WHAT the guidance is. Kept narrow on purpose - a broad "expects"
#: pattern matches every forward-looking-statements boilerplate paragraph in every filing, which
#: would bury the real guidance sentence in a hundred legal disclaimers and make the excerpt budget
#: worthless.
GUIDANCE_TERMS: tuple[str, ...] = (
    r"\bguidance\b", r"\boutlook\b",
    #: "we NOW expect" is how a revision is almost always worded, and an earlier version of this
    #: list required the bare "we expect" - so it matched boilerplate and missed the revision. Found
    #: by a test written against that exact sentence, not by reading the pattern.
    r"\bwe (?:now )?(?:expect|anticipate|project|forecast)s?\b",
    r"\bthe [Cc]ompany (?:now )?(?:expects|anticipates|projects|forecasts)\b",
    r"\b(?:full[- ]year|fiscal year|FY\s?20\d{2})\s+(?:guidance|outlook|revenue|earnings)\b",
    r"\b(?:reaffirm|reiterat|rais|lower|updat|withdraw|suspend)\w*\s+(?:its |our |the )?"
    r"(?:full[- ]year |fiscal |prior |previous )?(?:guidance|outlook)\b",
    r"\bnow (?:expect|anticipate|project)s?\b",
)

#: Management expectation SIGNALS (brief §14) - a target, milestone or commitment changing across
#: successive official documents. Located, never interpreted: "on track" appearing in a filing is a
#: fact about the document; whether it strengthened or weakened relative to last quarter is the
#: AI's source-linked comparison, not a keyword's verdict.
MANAGEMENT_SIGNAL_TERMS: tuple[str, ...] = (
    r"\bon track\b", r"\bahead of schedule\b", r"\bdelayed?\b", r"\bpostpon\w+\b",
    r"\bmilestone\b", r"\btarget(?:s|ing|ed)?\b", r"\bcommitt?\w*\s+to\b",
    r"\bno longer (?:expects?|anticipates?|plans?)\b", r"\bpushed? (?:out|back)\b",
    r"\bacceler\w+\b", r"\bahead of (?:our|its|the) (?:prior |previous )?(?:plan|target|schedule)\b",
)
