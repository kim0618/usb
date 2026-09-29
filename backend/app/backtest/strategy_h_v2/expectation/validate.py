"""D4 validation: the checks that cannot live in the schema because they need the code-owned inputs.

`analysis_schema.py` enforces everything checkable from the output alone. This module enforces
everything that requires comparing the output against something else - the frozen contract, the
immutable D3 research, the code-owned expectation bundle, or arithmetic the model was not allowed
to do. That split is deliberate: a Pydantic model that reached out to a price series to validate
itself would be a model that cannot be used without one.

Order matters and is not arbitrary. Banned field names are checked FIRST, before JSON is even
handed to Pydantic, so an attempted `fair_value` fails with "prohibited D4 field" rather than
Pydantic's generic "extra inputs are not permitted" - D3.1 §I.2 measured what an unspecific
rejection does to a repair round, and a decision field is the one rejection that must never be
vague.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.analysis_schema import (
    BANNED_D4_FIELD_NAMES,
    HExpectationGapAnalysisV1,
)
from app.backtest.strategy_h_v2.expectation.code_facts import CodeFact, code_source_id
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.gap_contract import (
    C7_PRICED_IN_MIN_EVIDENCE_IDS,
    ContractRule,
    EvidenceAvailability,
    ExpectationGapState,
    GuidanceState,
    NEGATIVE_STATES,
    POSITIVE_STATES,
    PricedInAssessment,
    apply_confidence_ceiling,
    confidence_ceiling,
    d6_approve_precondition,
)
from app.backtest.strategy_h_v2.expectation.guidance_arithmetic import (
    GuidanceRange,
    classify_range_movement,
    result_vs_range,
    state_disagrees_with_arithmetic,
)
from app.backtest.strategy_h_v2.research.schema import FutureBusinessStage
from app.backtest.strategy_h_v2.research.validate import (
    ExtractionError,
    extract_json_object,
    package_checksum,
    valid_evidence_ids,
    valid_source_ids,
)
from app.backtest.strategy_h_v2.research import validation_v2

VALIDATION_CONTRACT_VERSION = "h_v2_d4_validation_contract_v1"

#: Metadata the orchestration layer fills in, never asked of the model - the D4 counterpart of
#: `prompt_builder.METADATA_FIELDS`, including the four contract-residue fields code writes after
#: the model has answered.
METADATA_FIELDS = frozenset({
    "schema_version", "contract_version", "gap_contract_version", "analysis_id", "version",
    "candidate_id", "ticker", "decision_time", "research_input_id", "research_input_checksum",
    "expectation_evidence_id", "expectation_evidence_checksum", "model_name", "model_version",
    "prompt_version", "created_at",
    "applied_contract_rules", "confidence_ceiling", "d6_approve_precondition",
    "wide_positive_deferred_conjunct",
})

#: Stage tokens that count as "above STORY" for rule C2's future-business precondition.
_STAGES_ABOVE_STORY = frozenset({
    FutureBusinessStage.EARLY_EVIDENCE.value, FutureBusinessStage.COMMERCIALIZING.value,
    FutureBusinessStage.REAL_BUSINESS.value, FutureBusinessStage.MATURE.value,
})
#: `fundamental_changes[].state` values D1 emits that count as evidenced improvement (C2).
IMPROVING_STATES = frozenset({"IMPROVING", "ACCELERATING", "INFLECTION_POSITIVE", "INCREASING"})


def bundle_checksum(bundle: ExpectationEvidenceBundleV1) -> str:
    import hashlib
    canonical = bundle.model_dump_json(exclude={"generated_at"})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_banned_fields(content: Any, path: str = "") -> list[str]:
    """Every prohibited D4 field name anywhere in the response, at any nesting depth.

    Recursive on purpose: `{"why_now": [{"price_target": 42}]}` is exactly as prohibited as a
    top-level `price_target`, and a top-level-only check would pass it straight to Pydantic, which
    would reject it for the wrong reason inside a list element nobody reads.
    """
    found: list[str] = []
    if isinstance(content, dict):
        for key, value in content.items():
            where = f"{path}.{key}" if path else key
            if key.lower() in BANNED_D4_FIELD_NAMES:
                found.append(where)
            found.extend(find_banned_fields(value, where))
    elif isinstance(content, list):
        for index, item in enumerate(content):
            found.extend(find_banned_fields(item, f"{path}[{index}]"))
    return found


def _code_fact_support_text(fact: CodeFact) -> str:
    """A synthetic chunk whose numbers are every legitimate way to write this fact's value.

    Reuses `validation_v2._supported_by` rather than writing a second numeric matcher: that one has
    already been tuned against real D3 output for unit scales, table layouts and rounding, and a
    fresh comparator here would be a second, less-tested opinion about the same question.
    """
    value = fact.value
    if not isinstance(value, (int, float)):
        return str(value)
    renderings = [f"{value}", f"{abs(value)}"]
    if fact.unit == "RETURN_FRACTION":
        renderings += [f"{value * 100}%", f"{abs(value) * 100}%", f"{value * 100}",
                       f"{abs(value) * 100}"]
    elif fact.unit == "ANNUALIZED_STDEV":
        renderings += [f"{value * 100}%", f"{value * 100}"]
    else:
        renderings.append(f"${value}")
    return " | ".join(renderings)


#: Which numeric units in a claim could actually BE the cited code fact. Anything outside this
#: mapping is descriptive context, not the quoted figure, and is not checked.
#:
#: The exclusion of bare COUNT is the important part, and it was found by a test rather than
#: reasoned: "The shares rose 18% over the last 63 sessions" citing a 3-month return was reported
#: as a numeric defect because "63" is a COUNT token that matches nothing in the fact's value. A
#: session count, a quarter number or a segment count in a sentence about a return is ordinary
#: writing, and flagging it manufactures exactly the false-positive class D3.1 §I.2 measured once
#: already with `fair value`. What is checked is the figure that claims to BE the fact.
_COMPARABLE_UNITS: dict[str, frozenset] = {
    "RETURN_FRACTION": frozenset({validation_v2.NumericUnit.PERCENT,
                                  validation_v2.NumericUnit.BASIS_POINTS}),
    "ANNUALIZED_STDEV": frozenset({validation_v2.NumericUnit.PERCENT}),
    "USD": frozenset({validation_v2.NumericUnit.USD}),
}


def check_code_fact_numerics(
    analysis: HExpectationGapAnalysisV1, code_facts: dict[str, CodeFact],
) -> list[str]:
    """Every claim citing a code-owned number must state that number, or no number at all.

    A qualitative claim citing a code fact ("the shares underperformed the benchmark over the
    quarter") is fine and common - the fact is the basis, not a figure being quoted. What is not
    fine is citing the 3-month return and stating a DIFFERENT 3-month return, which is the exact
    shape of brief §25's prohibition on AI arithmetic: the model did not transcribe, it computed.

    Only units comparable with the cited fact are examined - see `_COMPARABLE_UNITS`.
    """
    errors: list[str] = []
    for claim in analysis._all_claims():
        cited = [claim.evidence_id] if claim.evidence_id else list(claim.evidence_ids)
        facts = [code_facts[cid] for cid in cited if cid in code_facts]
        if not facts:
            continue
        units: frozenset = frozenset().union(
            *(_COMPARABLE_UNITS.get(f.unit, frozenset()) for f in facts)
        ) if facts else frozenset()
        tokens = [t for t in validation_v2.parse_numeric_tokens(claim.text) if t.unit in units]
        if not tokens:
            continue
        support = " | ".join(_code_fact_support_text(f) for f in facts)
        unmatched = [
            t for t in tokens
            if validation_v2.numeric_support(t, claim.text, cited_chunk_text=support).scope
            == validation_v2.SupportScope.NOT_FOUND
        ]
        if unmatched:
            bad = ", ".join(repr(t.raw) for t in unmatched)
            errors.append(
                f"claim cites code-owned fact(s) {[f.path for f in facts]} but states number(s) "
                f"{bad} that are not those values - a number attached to a code-owned citation is "
                "transcribed, never computed (brief §25) @ supporting_claims"
            )
    return errors


def check_guidance_arithmetic(analysis: HExpectationGapAnalysisV1) -> list[str]:
    """Code recomputes what the two transcribed ranges did and rejects a contradicting state."""
    errors: list[str] = []
    for assessment in analysis.market_expectation_evidence.guidance_assessments:
        comparison = classify_range_movement(
            assessment.previous_range(), assessment.current_range()
        )
        if state_disagrees_with_arithmetic(assessment.state, comparison):
            errors.append(
                f"guidance metric {assessment.metric!r} is reported as {assessment.state.value}, "
                f"but its own transcribed ranges move {comparison.movement.value} "
                f"(low {comparison.low_change_abs}, high {comparison.high_change_abs}) - the "
                "direction of a guidance change is code's arithmetic, not a judgement "
                "@ market_expectation_evidence.guidance_assessments"
            )
    for item in analysis.market_expectation_evidence.result_vs_guidance:
        if (item.reported_value is None or item.prior_guidance_low is None
                or item.prior_guidance_high is None or item.unit is None):
            continue
        expected = result_vs_range(
            item.reported_value,
            GuidanceRange(item.prior_guidance_low, item.prior_guidance_high, item.unit),
        )
        if item.state.value != expected:
            errors.append(
                f"result_vs_guidance {item.metric!r} is reported as {item.state.value}, but "
                f"{item.reported_value} against [{item.prior_guidance_low}, "
                f"{item.prior_guidance_high}] is {expected} "
                "@ market_expectation_evidence.result_vs_guidance"
            )
    return errors


def check_d3_tokens_unmodified(
    analysis: HExpectationGapAnalysisV1, research_output: dict[str, Any],
) -> list[str]:
    """D4 may not restate a D3 conclusion (brief §3). The copies must match the source exactly."""
    errors: list[str] = []
    summary = analysis.fundamental_reality_summary
    d3_durability = ((research_output.get("growth_durability") or {}).get("state"))
    if d3_durability is not None and summary.d3_growth_durability_state != d3_durability:
        errors.append(
            f"d3_growth_durability_state={summary.d3_growth_durability_state!r} does not match the "
            f"immutable D3 output's {d3_durability!r} - D4 carries D3's conclusion forward, it does "
            "not re-decide it @ fundamental_reality_summary.d3_growth_durability_state"
        )
    stages = [
        str(item.get("stage")) for item in (research_output.get("future_business") or [])
        if item.get("stage") is not None
    ]
    order = ["UNKNOWN", "STORY", "EARLY_EVIDENCE", "COMMERCIALIZING", "REAL_BUSINESS", "MATURE"]
    expected = max(stages, key=lambda s: order.index(s) if s in order else -1) if stages else "UNKNOWN"
    if summary.d3_future_business_max_stage != expected:
        errors.append(
            f"d3_future_business_max_stage={summary.d3_future_business_max_stage!r} does not match "
            f"the highest stage in the D3 output ({expected!r}) "
            "@ fundamental_reality_summary.d3_future_business_max_stage"
        )
    return errors


def check_consensus_not_fabricated(
    analysis: HExpectationGapAnalysisV1, bundle: ExpectationEvidenceBundleV1,
) -> list[str]:
    """Brief §13/§18. The statuses must be the bundle's own, verbatim; and no field anywhere may
    claim a consensus expectation when the bundle says no consensus source exists."""
    errors: list[str] = []
    expectation = analysis.market_expectation_evidence
    for field, block, label in (
        (expectation.consensus_status, bundle.consensus, "consensus_status"),
        (expectation.estimate_revisions_status, bundle.estimate_revisions,
         "estimate_revisions_status"),
    ):
        if field != block.status.value:
            errors.append(
                f"{label}={field!r} contradicts the code-owned expectation bundle "
                f"({block.status.value!r}) - the availability of a data source is not the model's "
                f"to report @ market_expectation_evidence.{label}"
            )
    if bundle.consensus.status == EvidenceAvailability.SOURCE_NOT_AVAILABLE:
        for phrase in ("analysts expect", "analysts estimate", "consensus expect",
                       "consensus estimate", "wall street expect", "the street expect",
                       "investors are pricing", "consensus assumes", "beat consensus",
                       "missed consensus", "consensus forecast"):
            for text in _every_text(analysis):
                if phrase in text.lower():
                    errors.append(
                        f"text asserts a consensus expectation ({phrase!r}) while the expectation "
                        "bundle reports consensus SOURCE_NOT_AVAILABLE - brief §18 requires "
                        "'Available evidence does not establish consensus expectations.' instead "
                        "@ market_expectation_evidence"
                    )
                    break
    return errors


def _every_text(analysis: HExpectationGapAnalysisV1) -> list[str]:
    texts = [c.text for c in analysis._all_claims()]
    texts += analysis.limitations + analysis.unknown_fields
    texts += [i.summary for i in analysis.why_now]
    texts += analysis.priced_in_assessment.limitations
    texts += [c.topic for c in analysis.conflicts] + [c.description for c in analysis.conflicts]
    return texts


def _non_price_expectation_items(analysis: HExpectationGapAnalysisV1) -> int:
    """Rule C1's counter: expectation evidence that is not price history.

    A guidance assessment whose state is UNKNOWN or NOT_PROVIDED does not count - a block saying
    "no guidance was given" is an absence, and an absence cannot be the evidence that the market is
    behind. Counting it would let C1 be satisfied by the very thing it exists to catch.
    """
    expectation = analysis.market_expectation_evidence
    informative = {GuidanceState.RAISED, GuidanceState.MAINTAINED, GuidanceState.LOWERED,
                   GuidanceState.INITIATED, GuidanceState.WITHDRAWN, GuidanceState.MIXED}
    count = sum(1 for a in expectation.guidance_assessments if a.state in informative)
    count += sum(1 for r in expectation.result_vs_guidance
                 if r.state.value not in ("UNKNOWN", "NO_PRIOR_GUIDANCE"))
    count += sum(1 for m in expectation.management_signal_changes if m.direction != "UNCHANGED")
    return count


def check_contract_rules(
    analysis: HExpectationGapAnalysisV1,
    bundle: ExpectationEvidenceBundleV1,
    research_output: dict[str, Any],
) -> tuple[list[str], list[ContractRule]]:
    """C1-C7 against the frozen contract. Returns (errors, rules that fired)."""
    errors: list[str] = []
    fired: list[ContractRule] = []
    state = analysis.expectation_gap
    non_price = _non_price_expectation_items(analysis)

    if state in POSITIVE_STATES:
        fired.append(ContractRule.C1_POSITIVE_NEEDS_NON_PRICE_EVIDENCE)
        if non_price == 0:
            errors.append(
                f"expectation_gap={state.value} rests on price history alone (rule C1). Price "
                "history shows that the market HAS MOVED; it can never show the market is BEHIND, "
                "because an un-re-rated stock is equally consistent with 'not noticed yet' and "
                "'noticed and disagreeing for a reason outside this evidence pool'. A positive gap "
                "needs guidance, a result-vs-prior-guidance comparison, or a sourced management "
                "expectation signal @ expectation_gap"
            )

    if state == ExpectationGapState.WIDE_POSITIVE:
        fired.append(ContractRule.C2_WIDE_POSITIVE_CONJUNCTION)
        changes = (research_output.get("_fundamental_changes") or {})
        improving = any(
            isinstance(v, dict) and str(v.get("state")) in IMPROVING_STATES
            for v in changes.values()
        )
        if changes and not improving:
            errors.append(
                "expectation_gap=WIDE_POSITIVE requires material evidenced improvement, but no "
                "code-owned fundamental_changes state is improving (rule C2) @ expectation_gap"
            )
        stages = {str(i.get("stage")) for i in (research_output.get("future_business") or [])}
        if not (stages & _STAGES_ABOVE_STORY):
            errors.append(
                "expectation_gap=WIDE_POSITIVE requires at least one D3 future-business item above "
                "STORY (rule C2); this candidate has none @ expectation_gap"
            )
        if not any(i.realization_status.value != "REALIZED" for i in analysis.why_now):
            errors.append(
                "expectation_gap=WIDE_POSITIVE requires an unrealized why-now item (rule C2) - a "
                "re-rating window needs something that has not happened yet @ why_now"
            )

    if state in NEGATIVE_STATES and not analysis.gap_rationale:
        errors.append(f"expectation_gap={state.value} requires gap_rationale @ gap_rationale")

    has_conflict = bool(analysis.material_unresolved_conflicts())
    ceiling, ceiling_rules = confidence_ceiling(
        consensus_available=bundle.consensus.status != EvidenceAvailability.SOURCE_NOT_AVAILABLE,
        estimate_revisions_available=(
            bundle.estimate_revisions.status != EvidenceAvailability.SOURCE_NOT_AVAILABLE
        ),
        has_unresolved_material_conflict=has_conflict,
    )
    fired.extend(ceiling_rules)
    capped = apply_confidence_ceiling(analysis.expectation_gap_confidence, ceiling)
    if capped != analysis.expectation_gap_confidence:
        errors.append(
            f"expectation_gap_confidence={analysis.expectation_gap_confidence.value} exceeds the "
            f"frozen ceiling {ceiling.value} set by {[r.value for r in ceiling_rules]}. The prompt "
            "states this ceiling explicitly, so report at most the ceiling rather than relying on "
            "code to lower it - a silently lowered confidence would hide that the constraint was "
            "ignored @ expectation_gap_confidence"
        )

    if state != ExpectationGapState.UNKNOWN and non_price == 0 and not bundle.price_reaction:
        fired.append(ContractRule.C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN)
        errors.append(
            f"expectation_gap={state.value} with no non-price expectation evidence and no computed "
            "price reaction at all (rule C6) - with nothing on the expectation side of the "
            "comparison, the only defensible state is UNKNOWN @ expectation_gap"
        )

    priced_in = analysis.priced_in_assessment
    if priced_in.state != PricedInAssessment.UNKNOWN:
        fired.append(ContractRule.C7_PRICED_IN_NEEDS_EVIDENCE)
        if len(priced_in.evidence_ids) < C7_PRICED_IN_MIN_EVIDENCE_IDS:
            errors.append(
                f"priced_in_assessment={priced_in.state.value} needs at least "
                f"{C7_PRICED_IN_MIN_EVIDENCE_IDS} evidence id(s) (rule C7) @ priced_in_assessment"
            )
    return errors, fired


def assemble_and_validate_d4(
    raw_text: str,
    *,
    package: AIResearchInputV1,
    bundle: ExpectationEvidenceBundleV1,
    research_output: dict[str, Any],
    code_facts: dict[str, CodeFact],
    analysis_id: str,
    version: int,
    model_name: str,
    model_version: str | None,
    prompt_version: str,
    created_at: datetime,
) -> tuple[HExpectationGapAnalysisV1 | None, list[str]]:
    """`(validated_output, errors)`, the same contract `validate.assemble_and_validate` uses, so
    the D3 bounded-repair loop drives D4 unchanged.

    `research_output` is the immutable D3 `final_output` dict. It is READ here and never written -
    D4 has no code path that can modify a D3 artifact.
    """
    try:
        content = extract_json_object(raw_text)
    except ExtractionError as error:
        return None, [str(error)]

    banned = find_banned_fields(content)
    if banned:
        return None, [
            f"prohibited D4 field {name!r}: D4 is an expectation interpretation layer and produces "
            "no decision, valuation, price level or position. APPROVE/WATCH/REJECT belongs to D6 "
            f"and fair value to D5, neither of which has run @ {name}"
            for name in sorted(banned)
        ]

    injected = {k: v for k, v in content.items() if k not in METADATA_FIELDS}
    enriched_research = {**research_output,
                         "_fundamental_changes": package.evidence_bundle.fundamental_changes}
    full_record = {
        **injected,
        "analysis_id": analysis_id,
        "version": version,
        "candidate_id": bundle.company_id,
        "ticker": bundle.ticker,
        "decision_time": bundle.decision_time.isoformat(),
        "research_input_id": research_output.get("research_id", "UNKNOWN"),
        "research_input_checksum": package_checksum(package),
        "expectation_evidence_id": bundle.bundle_id,
        "expectation_evidence_checksum": bundle_checksum(bundle),
        "model_name": model_name,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "created_at": created_at.isoformat(),
    }
    citable_sources = valid_source_ids(package) | {code_source_id(bundle.bundle_id)}
    citable_evidence = (
        valid_evidence_ids(package) | set(bundle.valid_evidence_ids()) | set(code_facts)
    )
    try:
        analysis = HExpectationGapAnalysisV1.model_validate(
            full_record,
            context={"valid_source_ids": citable_sources,
                     "valid_evidence_ids": citable_evidence},
        )
    except ValidationError as error:
        return None, [str(e["msg"]) + " @ " + ".".join(str(p) for p in e["loc"])
                      for e in error.errors()]

    errors: list[str] = []
    errors += check_d3_tokens_unmodified(analysis, research_output)
    errors += check_consensus_not_fabricated(analysis, bundle)
    errors += check_guidance_arithmetic(analysis)
    errors += check_code_fact_numerics(analysis, code_facts)
    rule_errors, fired = check_contract_rules(analysis, bundle, enriched_research)
    errors += rule_errors
    if errors:
        return None, errors

    ceiling, _ = confidence_ceiling(
        consensus_available=bundle.consensus.status != EvidenceAvailability.SOURCE_NOT_AVAILABLE,
        estimate_revisions_available=(
            bundle.estimate_revisions.status != EvidenceAvailability.SOURCE_NOT_AVAILABLE
        ),
        has_unresolved_material_conflict=bool(analysis.material_unresolved_conflicts()),
    )
    from app.backtest.strategy_h_v2.expectation.gap_contract import (
        WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED,
    )
    return analysis.model_copy(update={
        "applied_contract_rules": sorted(set(fired), key=lambda r: r.value),
        "confidence_ceiling": ceiling,
        "d6_approve_precondition": d6_approve_precondition(
            analysis.expectation_gap, analysis.expectation_gap_confidence
        ),
        "wide_positive_deferred_conjunct": (
            WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED
            if analysis.expectation_gap == ExpectationGapState.WIDE_POSITIVE else None
        ),
    }), []
