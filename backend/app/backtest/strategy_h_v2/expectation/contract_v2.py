"""H-V2-D4 Contract V2: the EXECUTION layer repaired, the interpretation layer untouched.

D4.1 Tier A ran the frozen D4 Contract V1 live and returned MECHANICAL NOT READY. Nothing it found
was about what an expectation gap means. Every defect was in the layer that carries the contract to
the model and back:

1. `check_consensus_not_fabricated` matched the substring "consensus expect", which is a prefix of
   "consensus expectations" - the exact phrase the contract's own §18 remedy sentence contains. The
   rule fired on its prescribed cure and the error message demanded the sentence that triggered it,
   so no repair round could converge. Both live candidates ended on this one error and neither
   fabricated a consensus.
2. `ManagementSignalChangeV1.direction` enforced a seven-token allow-list in a field validator
   while being typed `str`, so the JSON schema the model was shown said `{"type": "string"}` with
   no members. Both initial responses failed on this and on nothing else.
3. The per-candidate worst case was booked at $2.00 while a candidate may make three model calls
   (initial + two repairs), each under its own $2.00 CLI cap. The two attempted candidates cost
   $2.43 and $2.11, so the budget was breached by construction, not by surprise.
4. D4 imported `valid_evidence_ids` from `research/validate.py` and `ConflictResolution` from
   `research/schema.py`, and both symbols exist only in an uncommitted working-tree edit. A clean
   checkout of the D4 package does not import.

This module owns what V2 changes. It changes only enum EXPOSURE, budget ARITHMETIC and dependency
ROUTING. It does not touch C1, C4, the gap enum, the priced-in or why-now meaning, a confidence
threshold or the UNKNOWN policy - and the allow-lists below are asserted equal to the V1 literals
they replace, so "V2 exposes the same values" is a test failure rather than a claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: V1, kept for attribution. Artifacts carrying these strings were produced under the contract
#: D4.1 ran and are read with V1's semantics.
D4_CONTRACT_VERSION_V1 = "h_v2_d4_contract_v1"
#: V2. Prompt, JSON schema, validator execution, budget accounting and dependency routing.
D4_CONTRACT_VERSION = "h_v2_d4_contract_v2"

#: The GAP contract - C1..C7, the six gap states, the confidence ceilings - is unchanged by V2 and
#: keeps its V1 version string. A reader who sees this value next to contract V2 is seeing the
#: intended fact: the meaning did not move.
GAP_CONTRACT_VERSION_UNCHANGED = "h_v2_d4_gap_contract_v1"


# ---------------------------------------------------------------------------------------------
# Defect 2 - the direction allow-list, exposed
# ---------------------------------------------------------------------------------------------

#: The seven tokens `ManagementSignalChangeV1._known_direction` enforced in V1, copied verbatim
#: from commit 8a54895. This literal exists so the enum below can be checked against the frozen
#: set rather than against itself; V2 adds no member and removes none.
V1_FROZEN_DIRECTIONS: frozenset[str] = frozenset({
    "STRENGTHENED", "WEAKENED", "INTRODUCED", "WITHDRAWN", "BROUGHT_FORWARD", "DELAYED",
    "UNCHANGED",
})


class ManagementSignalDirection(StrEnum):
    """How a management target or milestone moved between two official documents (D4 brief §14).

    This is the single definition. The Pydantic field, the JSON schema shown to the model, the
    validator and the tests all resolve to it, because D4.1 measured what happens when they do not:
    the model was shown `{"type": "string"}`, answered `"INCREASED"` and
    `"UP (small): 911,400 -> 915,400 -> 917,000 tonnes across three consecutive filings"`, and was
    rejected by a rule it had never been shown. A constraint the model cannot read is not a
    contract, it is a trap.
    """

    STRENGTHENED = "STRENGTHENED"
    """The same target is now stated with more conviction, a higher value, or a firmer commitment."""
    WEAKENED = "WEAKENED"
    """The same target is now hedged, lowered, or stated with less commitment."""
    INTRODUCED = "INTRODUCED"
    """A target or milestone that the earlier document did not state at all."""
    WITHDRAWN = "WITHDRAWN"
    """A target the earlier document stated and the later one removes."""
    BROUGHT_FORWARD = "BROUGHT_FORWARD"
    """The same target, now expected earlier."""
    DELAYED = "DELAYED"
    """The same target, now expected later."""
    UNCHANGED = "UNCHANGED"
    """Both documents state it the same way. Recorded, and counted as no expectation signal."""


assert {d.value for d in ManagementSignalDirection} == V1_FROZEN_DIRECTIONS, (
    "V2 must expose exactly the directions V1 enforced - a member added or dropped here would be a "
    "semantic change wearing an execution-layer repair"
)


# ---------------------------------------------------------------------------------------------
# Defect 2, same class - every other validator-enforced allow-list typed `str` (brief §8 audit)
# ---------------------------------------------------------------------------------------------

#: `ExpectationConflictV1._known_origin`'s V1 literal.
V1_FROZEN_CONFLICT_ORIGINS: frozenset[str] = frozenset({"D3_RESEARCH", "D4_EXPECTATION"})


class ConflictOrigin(StrEnum):
    """Where a recorded conflict came from. Same defect class as `direction`: V1 enforced these two
    tokens in a field validator over a `str` field, so the schema showed the model no members."""

    D3_RESEARCH = "D3_RESEARCH"
    """Carried forward from the immutable D3 output. Not a new D4 finding and never presented as
    one."""
    D4_EXPECTATION = "D4_EXPECTATION"
    """Found by D4 between two pieces of expectation evidence."""


assert {o.value for o in ConflictOrigin} == V1_FROZEN_CONFLICT_ORIGINS, (
    "V2 must expose exactly the origins V1 enforced"
)


# ---------------------------------------------------------------------------------------------
# Defect 3 - the budget contract
# ---------------------------------------------------------------------------------------------
#
# V1 wrote one number, `D4_WORST_CASE_CANDIDATE_USD = 2.00`, and used it for two different things:
# the CLI's `--max-budget-usd`, which caps ONE call, and the preflight reserve, which must cover a
# whole candidate. A candidate is one initial call plus up to `MAX_REPAIR_ATTEMPTS` repairs, so the
# true worst case is the per-call cap times the call count. D4.1's two candidates cost $2.43 and
# $2.11 against a $2.00 reserve - a 3-call topology billed against a 1-call ceiling.
#
# Nothing here is a new spending authorisation. It is the same money, counted against the calls
# that can actually be made.

#: Bounded repair rounds per candidate, unchanged from D4.1's runner. The call topology below is
#: derived from it, never restated beside it.
MAX_REPAIR_ATTEMPTS = 2
MAX_CALLS_PER_CANDIDATE = 1 + MAX_REPAIR_ATTEMPTS


@dataclass(frozen=True)
class CandidateBudgetContract:
    """What one candidate may cost, stated as the call topology rather than as a single number.

    `per_call_max_budget_usd` is what the CLI is handed and is a CAP PER CALL. Multiplying it by
    the call count is the only honest reserve: a preflight that reserves less than a candidate can
    spend does not stop an overrun, it just discovers one afterwards.
    """

    per_call_max_budget_usd: float
    max_calls_per_candidate: int = MAX_CALLS_PER_CANDIDATE
    #: Work that is paid for before the D4 call and is not subject to the per-call cap - Tier B's
    #: chained D3 leg. Zero for Tier A, whose D3 outputs were already bought by D3.3.
    preceding_stage_worst_case_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.per_call_max_budget_usd <= 0:
            raise ValueError("per_call_max_budget_usd must be positive")
        if self.max_calls_per_candidate < 1:
            raise ValueError("a candidate makes at least one call")
        if self.preceding_stage_worst_case_usd < 0:
            raise ValueError("preceding_stage_worst_case_usd cannot be negative")

    @property
    def candidate_worst_case_budget_usd(self) -> float:
        return (self.per_call_max_budget_usd * self.max_calls_per_candidate
                + self.preceding_stage_worst_case_usd)

    def admits_next_candidate(self, *, spent_so_far_usd: float, overall_hard_cap_usd: float) -> bool:
        """Brief §10, checked BEFORE the candidate starts: could this candidate finish inside the
        cap even if it spends everything it is allowed to?"""
        return (spent_so_far_usd + self.candidate_worst_case_budget_usd) <= overall_hard_cap_usd

    def to_dict(self) -> dict:
        return {
            "per_call_max_budget_usd": self.per_call_max_budget_usd,
            "max_calls_per_candidate": self.max_calls_per_candidate,
            "preceding_stage_worst_case_usd": self.preceding_stage_worst_case_usd,
            "candidate_worst_case_budget_usd": self.candidate_worst_case_budget_usd,
        }


@dataclass(frozen=True)
class CandidateSpend:
    """Every dollar a candidate spent, split the way brief §11 requires: the initial call, each
    repair call, and the total. A repair is charged to the same cap as everything else - there is
    no separate repair budget, because a budget with a hidden second budget is not a cap."""

    ticker: str
    initial_cost_usd: float
    repair_costs_usd: tuple[float, ...] = ()
    preceding_stage_cost_usd: float = 0.0

    @property
    def repair_cost_usd(self) -> float:
        return sum(self.repair_costs_usd)

    @property
    def candidate_total_cost_usd(self) -> float:
        return self.initial_cost_usd + self.repair_cost_usd + self.preceding_stage_cost_usd

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "initial_cost_usd": self.initial_cost_usd,
            "repair_costs_usd": list(self.repair_costs_usd),
            "repair_cost_usd": self.repair_cost_usd,
            "preceding_stage_cost_usd": self.preceding_stage_cost_usd,
            "candidate_total_cost_usd": self.candidate_total_cost_usd,
        }


def run_total_cost_usd(spends: list[CandidateSpend]) -> float:
    return sum(s.candidate_total_cost_usd for s in spends)
