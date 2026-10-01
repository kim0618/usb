"""H-V2-D4-BR: code-owned comparison affordance, and the structural pre-scan that stops one
validation error from hiding another.

Two things live here, and they exist because of two distinct Tier B findings.

**Comparability** (brief §6/§7/§8). A comparative state - RAISED, LOWERED, MIXED, ABOVE/WITHIN/BELOW
company guidance - is a claim about two operands. Whether those operands are present is arithmetic
over the payload, not a judgement, so it is decided here and the model cannot report it. The model
still SUPPLIES the operands, which is unavoidable: they are transcriptions from a filing and no code
path can invent them. What it can no longer do is name a comparative state while the operands that
would make the comparison meaningful are absent - and, more usefully, when they are absent the code
says WHICH one is missing, so the repair prompt names a field instead of restating a rule.

`analysis_schema` already enforced the per-item version of this inside two separate model
validators. Nothing is loosened here: the same conditions produce the same rejections. What is added
is a named reason (`ComparisonUnavailableReason`) and a representation that works on a plain mapping,
before pydantic has run - which is what the pre-scan needs.

**The pre-scan** (brief §16, and the mechanism behind the FRPT failure). Pydantic runs child
validators before a parent's `mode="after"` validator, and a child failure means the parent never
runs. So `MarketExpectationEvidenceV1._overall_state_follows_metrics` is INVISIBLE for as long as any
single assessment is malformed. The Tier B record shows what that costs:

    FRPT   initial     guidance_assessments[2] bounds half-stated   <- the aggregate rule did not run
           repair 1    same error, model restated the metric name
           repair 2    overall=RAISED vs per-metric {RAISED, MAINTAINED}  <- surfaced only now
           budget      exhausted; no final output

    IDCC   initial     result_vs_guidance[0] extra field
           repair 1    overall=RAISED vs per-metric {RAISED, INITIATED}   <- same masked rule
           repair 2    OK                                                 <- converged on its last round

Both candidates violated the aggregate rule in their INITIAL response. Neither was told so until a
round had been spent on something else. FRPT needed three serial discoveries against a two-round
budget and IDCC needed two - the difference between the only D4 failure of the run and a pass was one
masked error, not one unsupported claim. Replaying FRPT's initial payload with ga[2]'s bounds
completed and NOTHING else changed produces the aggregate error immediately, which is the proof that
it was present from the first call.

So the pre-scan evaluates, from the raw dict, the inconsistencies that a child failure would mask,
and `validate.py` appends them to the pydantic errors. This REPORTS MORE, EARLIER. It accepts nothing
pydantic would reject and rejects nothing pydantic would accept; a payload that is already valid
produces no pre-scan error, because every rule here is a restatement of a rule the schema enforces.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.backtest.strategy_h_v2.expectation.gap_contract import (
    GuidanceState,
    ResultVsCompanyGuidance,
)

COMPARABILITY_CONTRACT_VERSION = "h_v2_d4_br_comparability_v1"


class ComparisonKind(StrEnum):
    """The two comparison shapes D4 produces. There is no consensus kind, and there will not be one
    until a consensus provider exists - the same reason `ResultVsCompanyGuidance` is named after the
    company's own guidance."""

    GUIDANCE_RANGE_CHANGE = "GUIDANCE_RANGE_CHANGE"
    RESULT_VS_COMPANY_GUIDANCE = "RESULT_VS_COMPANY_GUIDANCE"


class ComparisonUnavailableReason(StrEnum):
    """Why a comparison cannot be made. One value per missing operand, deliberately: `INSUFFICIENT`
    would tell a repair prompt nothing it did not already know, and the Tier B FRPT rounds are what
    that costs - two rounds spent restating a rule because the error named no field."""

    MISSING_PREVIOUS_LOW = "MISSING_PREVIOUS_LOW"
    MISSING_PREVIOUS_HIGH = "MISSING_PREVIOUS_HIGH"
    MISSING_CURRENT_LOW = "MISSING_CURRENT_LOW"
    MISSING_CURRENT_HIGH = "MISSING_CURRENT_HIGH"
    MISSING_REPORTED_VALUE = "MISSING_REPORTED_VALUE"
    MISSING_PRIOR_GUIDANCE_LOW = "MISSING_PRIOR_GUIDANCE_LOW"
    MISSING_PRIOR_GUIDANCE_HIGH = "MISSING_PRIOR_GUIDANCE_HIGH"
    MISSING_UNIT = "MISSING_UNIT"


#: The deterministic operand requirement per comparison kind, in the order a reader checks them.
#: `unit` is in both because a comparison across incompatible units is not a comparison, and a bare
#: pair of numbers does not carry its own dimension - the `dimension_swap` class of defect.
REQUIRED_OPERANDS: dict[ComparisonKind, tuple[str, ...]] = {
    ComparisonKind.GUIDANCE_RANGE_CHANGE: (
        "previous_low", "previous_high", "current_low", "current_high", "unit",
    ),
    ComparisonKind.RESULT_VS_COMPANY_GUIDANCE: (
        "reported_value", "prior_guidance_low", "prior_guidance_high", "unit",
    ),
}

_REASON_FOR_OPERAND: dict[str, ComparisonUnavailableReason] = {
    "previous_low": ComparisonUnavailableReason.MISSING_PREVIOUS_LOW,
    "previous_high": ComparisonUnavailableReason.MISSING_PREVIOUS_HIGH,
    "current_low": ComparisonUnavailableReason.MISSING_CURRENT_LOW,
    "current_high": ComparisonUnavailableReason.MISSING_CURRENT_HIGH,
    "reported_value": ComparisonUnavailableReason.MISSING_REPORTED_VALUE,
    "prior_guidance_low": ComparisonUnavailableReason.MISSING_PRIOR_GUIDANCE_LOW,
    "prior_guidance_high": ComparisonUnavailableReason.MISSING_PRIOR_GUIDANCE_HIGH,
    "unit": ComparisonUnavailableReason.MISSING_UNIT,
}

#: States that ASSERT a comparison, so they require the full operand set. `INITIATED` is not one:
#: new guidance with no predecessor is a fact about one range. `WITHDRAWN`, `NOT_PROVIDED`,
#: `UNKNOWN`, `NO_PRIOR_GUIDANCE` are absences, which is the whole point - they are what an honest
#: abstention uses, and they are reachable with no operands at all.
COMPARATIVE_GUIDANCE_STATES: frozenset[GuidanceState] = frozenset({
    GuidanceState.RAISED, GuidanceState.LOWERED, GuidanceState.MIXED,
})
COMPARATIVE_RESULT_STATES: frozenset[ResultVsCompanyGuidance] = frozenset({
    ResultVsCompanyGuidance.ABOVE_COMPANY_GUIDANCE,
    ResultVsCompanyGuidance.WITHIN_COMPANY_GUIDANCE,
    ResultVsCompanyGuidance.BELOW_COMPANY_GUIDANCE,
})

#: The states an item may carry when `comparison_allowed` is False. Every comparison kind has at
#: least one, which is what "the schema affords honest abstention" means operationally.
ABSTENTION_GUIDANCE_STATES: frozenset[GuidanceState] = frozenset({
    GuidanceState.UNKNOWN, GuidanceState.NOT_PROVIDED, GuidanceState.WITHDRAWN,
    GuidanceState.INITIATED, GuidanceState.MAINTAINED,
})
ABSTENTION_RESULT_STATES: frozenset[ResultVsCompanyGuidance] = frozenset({
    ResultVsCompanyGuidance.UNKNOWN, ResultVsCompanyGuidance.NO_PRIOR_GUIDANCE,
})


@dataclass(frozen=True)
class ComparabilityVerdict:
    """Code's answer to "may this comparison be stated?". Frozen, and derived only from operands.

    There is no field the model can set to change this, and `from_mapping` ignores any key named
    `comparison_allowed` or `comparison_unavailable_reason` in the payload - `extra="forbid"` on
    every analysis model already rejects such a key outright, so a model that tries to assert its own
    comparability fails validation rather than being quietly believed.
    """

    kind: ComparisonKind
    comparison_allowed: bool
    missing_operands: tuple[str, ...]
    comparison_unavailable_reason: ComparisonUnavailableReason | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "comparison_allowed": self.comparison_allowed,
            "missing_operands": list(self.missing_operands),
            "comparison_unavailable_reason": (
                self.comparison_unavailable_reason.value
                if self.comparison_unavailable_reason else None
            ),
        }


def assess_comparability(item: Mapping[str, Any], kind: ComparisonKind) -> ComparabilityVerdict:
    """Operand completeness for one item. Takes a mapping so it works on a raw dict, before pydantic.

    `unit` is required only once another operand is present: an item with no numbers at all is an
    absence, and demanding a unit for nothing would turn "no guidance was given" into an error.
    """
    required = REQUIRED_OPERANDS[kind]
    numeric = [name for name in required if name != "unit"]
    any_numeric = any(item.get(name) is not None for name in numeric)
    missing = [name for name in numeric if item.get(name) is None]
    if any_numeric and item.get("unit") is None:
        missing.append("unit")
    if not missing:
        return ComparabilityVerdict(kind, True, (), None)
    return ComparabilityVerdict(
        kind, False, tuple(missing), _REASON_FOR_OPERAND[missing[0]],
    )


def _asserted_comparative(state: Any, kind: ComparisonKind) -> bool:
    try:
        if kind is ComparisonKind.GUIDANCE_RANGE_CHANGE:
            return GuidanceState(state) in COMPARATIVE_GUIDANCE_STATES
        return ResultVsCompanyGuidance(state) in COMPARATIVE_RESULT_STATES
    except ValueError:
        return False          # not a state this enum has; the enum validator owns that complaint


def _items(container: Any, key: str) -> list[Mapping[str, Any]]:
    value = container.get(key) if isinstance(container, Mapping) else None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [i for i in value if isinstance(i, Mapping)]


def comparability_errors(expectation: Mapping[str, Any]) -> list[str]:
    """Comparative states whose operands are incomplete, one error per item, naming the field.

    The same condition `GuidanceMetricAssessmentV1` and `ResultVsGuidanceItemV1` already reject. The
    difference is the message: it names the missing operand and the states that remain available, so
    a repair has somewhere to go other than back to the same claim.
    """
    errors: list[str] = []
    for key, kind, allowed in (
        ("guidance_assessments", ComparisonKind.GUIDANCE_RANGE_CHANGE,
         ABSTENTION_GUIDANCE_STATES),
        ("result_vs_guidance", ComparisonKind.RESULT_VS_COMPANY_GUIDANCE,
         ABSTENTION_RESULT_STATES),
    ):
        for index, item in enumerate(_items(expectation, key)):
            state = item.get("state")
            if not _asserted_comparative(state, kind):
                continue
            verdict = assess_comparability(item, kind)
            if verdict.comparison_allowed:
                continue
            errors.append(
                f"state={state} is a comparison but comparison_allowed=False "
                f"({verdict.comparison_unavailable_reason}); operands missing: "
                f"{', '.join(verdict.missing_operands)}. Code owns this and the model cannot set it. "
                f"With the operands absent the available states are "
                f"{', '.join(sorted(s.value for s in allowed))} - choosing one of those is a correct "
                f"answer, not a failure @ market_expectation_evidence.{key}.{index}"
            )
    return errors


def overall_guidance_state_errors(expectation: Mapping[str, Any]) -> list[str]:
    """The aggregate rule, computed from the raw dict so a malformed child cannot hide it.

    Mirrors `MarketExpectationEvidenceV1._overall_state_follows_metrics` exactly. Written as a second
    implementation rather than a call into the model because the point is to run when the model
    cannot be built - and `test_comparability` pins the two against each other on the Tier B
    fixtures so they cannot drift.
    """
    overall = expectation.get("overall_guidance_state")
    assessments = _items(expectation, "guidance_assessments")
    states: set[GuidanceState] = set()
    for item in assessments:
        try:
            states.add(GuidanceState(item.get("state")))
        except ValueError:
            return []     # an unknown state token: the enum validator reports it, not this
    try:
        overall_state = GuidanceState(overall)
    except ValueError:
        return []
    if not assessments:
        if overall_state not in (GuidanceState.NOT_PROVIDED, GuidanceState.UNKNOWN,
                                 GuidanceState.WITHDRAWN):
            return [
                f"overall_guidance_state={overall_state.value} with no per-metric assessment - an "
                "overall state must rest on at least one guided metric "
                "@ market_expectation_evidence"
            ]
        return []
    definite = states - {GuidanceState.UNKNOWN, GuidanceState.NOT_PROVIDED}
    if len(definite) > 1 and overall_state != GuidanceState.MIXED:
        return [
            f"per-metric states {sorted(s.value for s in definite)} disagree, so the overall state "
            "is MIXED - brief §7 forbids collapsing a disagreement into one direction "
            "@ market_expectation_evidence"
        ]
    if len(definite) == 1 and overall_state not in (definite | {GuidanceState.MIXED}):
        only = next(iter(definite))
        return [
            f"the only definite per-metric state is {only.value}, so overall_guidance_state cannot "
            f"be {overall_state.value} @ market_expectation_evidence"
        ]
    return []


def structural_prescan(content: Mapping[str, Any]) -> list[str]:
    """Every masked inconsistency, from the raw parsed payload. Safe on malformed input by design.

    Called only when pydantic has already failed, and its results are appended to pydantic's own.
    A rule that cannot be evaluated - because the value it needs is the wrong type, or missing - is
    skipped rather than guessed at: the schema will report that, and a pre-scan that invented a
    second complaint about the same field would make the repair prompt worse, not better.
    """
    expectation = content.get("market_expectation_evidence")
    if not isinstance(expectation, Mapping):
        return []
    return overall_guidance_state_errors(expectation) + comparability_errors(expectation)
