"""H-V2 D1 E3 - Research Priority.

E3 orders candidates for AI research; it is never a buy rank
(`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md` §E3). `future stock return` must never appear as an
input here - see `test_research_priority.py::test_no_return_field_accepted` for the enforced
invariant (the input dataclass has no return/price-change/performance field at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.backtest.strategy_h_v2.change_detection import ChangeEvidence, Confidence, MATERIAL_STATES

SCHEMA_VERSION = "h_v2_research_priority_v1"


class PriorityState(StrEnum):
    P1_HIGH = "P1_HIGH"
    P2_MEDIUM = "P2_MEDIUM"
    P3_LOW = "P3_LOW"
    HOLD = "HOLD"


@dataclass(frozen=True)
class PriorityInputs:
    """Everything E3 is allowed to look at. Deliberately has no field for a future return, a price
    target, or an expected performance number of any kind."""

    change_evidence: tuple[ChangeEvidence, ...]
    days_since_latest_filing: int | None
    """Recency of the most recent accepted SEC filing for this issuer, from the submissions store.
    `None` means unknown, not "no recent filing"."""
    resolved_field_count: int
    total_field_count: int


def _material_change_score(evidence: tuple[ChangeEvidence, ...]) -> tuple[int, int]:
    material = [e for e in evidence if e.state in MATERIAL_STATES]
    high_confidence_material = [e for e in material if e.confidence == Confidence.HIGH]
    return len(material), len(high_confidence_material)


def compute_priority(inputs: PriorityInputs) -> PriorityState:
    """Deterministic, forward-return-free priority. Ordering is "most useful/urgent for research
    to read first", not "most likely to go up" - a candidate with weak change evidence can still
    reach APPROVE later at the AI Research step; a P1_HIGH candidate can still be REJECTed there.
    """
    material_count, high_confidence_count = _material_change_score(inputs.change_evidence)
    completeness = (
        inputs.resolved_field_count / inputs.total_field_count if inputs.total_field_count else 0.0
    )
    filing_is_recent = inputs.days_since_latest_filing is not None and inputs.days_since_latest_filing <= 14

    if completeness < 0.34:
        # Too little evidence to prioritize responsibly; research would be guessing, not reading.
        return PriorityState.HOLD
    if high_confidence_count >= 2 or (high_confidence_count >= 1 and filing_is_recent):
        return PriorityState.P1_HIGH
    if material_count >= 1:
        return PriorityState.P2_MEDIUM
    return PriorityState.P3_LOW
