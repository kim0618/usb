"""Strategy B hard eligibility: who may become a candidate at all.

Declared rules: ``docs/backtest/strategy_b/B_F0_FSM_RULES_V1.md`` section 6.

Eligibility is separate from the scanner gate on purpose. The gate asks "is this symbol
moving?"; eligibility asks "may B trade this symbol today, and is the tape good enough to
judge it?". The second question is re-asked on every tick while a candidate is watched,
because a halt pattern or a collapsing tape appears mid-session.

Everything here fails closed: an ``UNKNOWN`` tape density is a rejection, not a pass. The
single deliberate exception is ``HaltStatus.UNKNOWN``, which the research layer defines as
"cannot tell" and not as "halted" (see the package README).
"""

from dataclasses import dataclass
from enum import StrEnum

from app.strategy_b.models import CorporateActionFlag, FeatureSnapshot, HaltStatus, TapeDensity
from app.strategy_b.scope import ScopeDecision, ScopeExclusion


class IneligibleReason(StrEnum):
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    """The D-1 research scope excluded the symbol; the scope exclusions are kept next to it."""
    CORPORATE_ACTION = "CORPORATE_ACTION"
    HALT_INFERRED = "HALT_INFERRED"
    TAPE_TOO_SPARSE = "TAPE_TOO_SPARSE"
    TAPE_DENSITY_UNKNOWN = "TAPE_DENSITY_UNKNOWN"


REJECTING_CORPORATE_ACTIONS = frozenset({
    CorporateActionFlag.SPLIT_ON_DAY,
    CorporateActionFlag.IPO_WARMUP,
    CorporateActionFlag.DELISTING_WINDOW,
    CorporateActionFlag.SYMBOL_CHANGE,
    CorporateActionFlag.CA_SUSPECT,
})
"""``RECENT_SPLIT`` is absent on purpose: the PIT split factors already handle it."""


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    symbol: str
    eligible: bool
    reasons: tuple[IneligibleReason, ...]
    corporate_actions: tuple[CorporateActionFlag, ...]
    scope_exclusions: tuple[ScopeExclusion, ...]

    def __post_init__(self) -> None:
        if self.eligible != (not self.reasons):
            raise ValueError("eligible is exactly the absence of a reason")


def evaluate_eligibility(snapshot: FeatureSnapshot,
                         scope: ScopeDecision | None) -> EligibilityDecision:
    """Hard eligibility for one symbol at one as-of time.

    ``scope`` is the D-1 decision from ``scope.evaluate_research_scope``. ``None`` means the
    caller has no scope decision for this symbol, which is itself a rejection: a symbol that
    was never judged in scope does not enter the pool by default.
    """
    reasons: list[IneligibleReason] = []
    scope_exclusions: tuple[ScopeExclusion, ...] = () if scope is None else scope.exclusion_reasons
    if scope is None or not scope.included:
        reasons.append(IneligibleReason.OUT_OF_SCOPE)

    blocking = sorted(snapshot.corporate_action_flags & REJECTING_CORPORATE_ACTIONS)
    if blocking:
        reasons.append(IneligibleReason.CORPORATE_ACTION)

    if snapshot.halt_inferred is HaltStatus.HALT_INFERRED:
        reasons.append(IneligibleReason.HALT_INFERRED)

    if snapshot.sparse_status is TapeDensity.VERY_SPARSE:
        reasons.append(IneligibleReason.TAPE_TOO_SPARSE)
    elif snapshot.sparse_status is TapeDensity.UNKNOWN:
        reasons.append(IneligibleReason.TAPE_DENSITY_UNKNOWN)

    return EligibilityDecision(
        symbol=snapshot.symbol,
        eligible=not reasons,
        reasons=tuple(reasons),
        corporate_actions=tuple(blocking),
        scope_exclusions=scope_exclusions,
    )
