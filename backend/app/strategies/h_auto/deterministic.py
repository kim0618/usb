"""Production D3 and D4 without a model: what H's frozen contracts let code produce, and nothing more.

Audit basis (2026-10-14, ``H_V2_PRODUCTION_NO_AI_RUNTIME_REPAIR_V1.md`` §3-§5):

* **D3.** Every content field of ``h_research_interpretation_v2`` is model-written. D0 §V assigns
  business-model interpretation, future-business classification (STORY vs REAL), catalyst
  materiality and timing, and the risk/invalidation reading to AI. The Future Business table in
  ``research/schema.py`` is a floor (stage -> minimum count of model-written evidence flags), not a
  map from evidence to a stage, and no rule or validator exists for ``growth_durability``. What code
  owns is upstream of D3: D1/E2's fundamental-change states (``change_detection``) and the D2.1
  chunks, each with an SEC source id.
* **D4.** No function maps evidence to an ``expectation_gap`` state. D0 §L: "a frozen categorical
  state, judged by the AI and checked by code"; C1-C7 can only reject a model's state. Code owns the
  expectation evidence bundle (prices, event reactions, located guidance passages, consensus
  availability), the C4 confidence ceiling and the D6 precondition function.

So the code-owned generators below produce exactly that, with reason codes, and never fill an
AI-owned field:

* ``d3_code_owned`` writes the code-owned evidence and lists every AI-owned field it did not produce.
  It has **no** ``final_output``. D6 therefore projects it as ``D3_REFUSED_NO_FINAL_OUTPUT`` and the
  issuer is ``NOT_DECISION_ELIGIBLE``: the existing decision stands. An empty interpretation would
  have been worse than none - D6's ``thesis_unsupported_by_the_evidence_bundle`` trigger fires on an
  empty ``business_model``, so a missing interpretation would have become a REJECT.
* ``d4_code_owned`` builds the frozen expectation bundle and returns ``UNKNOWN`` with ``UNKNOWN``
  confidence: D0 §L's UNKNOWN ("a pricing-in assessment cannot be made responsibly"), C5 pairs the
  two, C1 makes POSITIVE inadmissible (every non-price expectation item is model-written), and
  NEUTRAL/NEGATIVE need a judged rationale that no frozen rule supplies. C6 forces UNKNOWN on its own
  when the bundle also has no price reaction.

Nothing here reads a forward return, sets a threshold or chooses a state for being convenient.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

D3_GENERATOR = "H_D3_CODE_OWNED_V1"
D4_GENERATOR = "H_D4_CODE_OWNED_V1"
D3_STATUS = "CODE_OWNED_EVIDENCE_ONLY"

#: Every D3 field D6 or D4 reads that only a model has ever produced, and why code does not.
D3_NOT_PRODUCED: dict[str, str] = {
    "business_model": "AI_OWNED_D0_SV_BUSINESS_MODEL_INTERPRETATION",
    "fundamental_change.explanation": "AI_OWNED_D0_SI_WHY_IT_CHANGED",
    "growth_durability.state": "NO_FROZEN_RULE_OR_VALIDATOR",
    "future_business": "NO_FROZEN_EVIDENCE_TO_STAGE_GENERATOR_FLOOR_TABLE_IS_A_VALIDATOR",
    "future_business.evidence_flags": "MODEL_AUTHORED_FLAGS_NO_CODE_DERIVATION",
    "catalyst_candidates": "AI_OWNED_D0_SV_CATALYST_MATERIALITY_AND_TIMING",
    "competitive_position": "AI_OWNED_INTERPRETATION",
    "management_execution": "AI_OWNED_INTERPRETATION",
    "why_now_candidate": "AI_OWNED_INTERPRETATION",
    "risks": "AI_OWNED_INTERPRETATION",
    "invalidation_candidates": "AI_OWNED_INTERPRETATION",
    "evidence_conflicts": "AI_OWNED_INTERPRETATION",
    "unknown_fields": "AI_OWNED_FREE_TEXT",
    "research_completeness": "AI_OWNED_SELF_ASSESSMENT",
}

#: D6 input backtrace (repair brief §3). Every stored-output key ``run_strategy_h_v2_d6.project_d3`` /
#: ``project_d4`` read, its producer before this repair, and what produces it now. A test pins the key
#: set to the projection functions' source, so a new D6 input cannot appear without a row here.
D6_INPUT_BACKTRACE: tuple[dict[str, str], ...] = (
    {"leg": "D3", "key": "research_id", "use": "provenance / D4 chain", "producer_v1": "code (assemble_v2 metadata)", "now": "D3C id (code)"},
    {"leg": "D3", "key": "research_completeness", "use": "basis text only", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "business_model", "use": "APPROVE business_understood; REJECT thesis_unsupported", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "revenue_drivers", "use": "as business_model", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "fundamental_change", "use": "APPROVE anchored change; REJECT thesis_unsupported", "producer_v1": "AI (state copied from code, explanation AI)", "now": "code-owned states recorded, interpretation NOT_PRODUCED"},
    {"leg": "D3", "key": "growth_durability", "use": "basis text only (deterioration trigger NOT_EVALUATED)", "producer_v1": "AI", "now": "UNKNOWN"},
    {"leg": "D3", "key": "state", "use": "growth_durability.state", "producer_v1": "AI", "now": "UNKNOWN"},
    {"leg": "D3", "key": "future_business", "use": "APPROVE future/catalyst; REJECT story-only; WATCH", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "stage", "use": "future_business stages", "producer_v1": "AI (floor validator only)", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "evidence_flags", "use": "§J flags (_JJ_EVIDENCE_FLAGS): APPROVE future_business; REJECT story-only", "producer_v1": "AI (model-authored booleans)", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "source_id", "use": "fundamental_change anchoring (_claims_are_sourced)", "producer_v1": "AI-written citation, validated", "now": "code-owned SEC source ids recorded"},
    {"leg": "D3", "key": "catalyst_candidates", "use": "APPROVE; REJECT no_catalyst; WATCH undated", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "materiality_candidate", "use": "catalyst material+timed", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "timing_confidence", "use": "catalyst material+timed", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "competitive_position", "use": "count, no clause", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "risks", "use": "APPROVE risks_with_invalidation", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "invalidation_candidates", "use": "APPROVE risks_with_invalidation", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "unknown_fields", "use": "APPROVE no_blocking_unknown", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "evidence_conflicts", "use": "count, no clause", "producer_v1": "AI", "now": "NOT_PRODUCED"},
    {"leg": "D3", "key": "final_output", "use": "provenance (absent -> D3_REFUSED_NO_FINAL_OUTPUT)", "producer_v1": "AI", "now": "None by design"},
    {"leg": "D3", "key": "final_output_checksum", "use": "provenance", "producer_v1": "code", "now": "None"},
    {"leg": "D4", "key": "final_output", "use": "provenance", "producer_v1": "AI + code residue", "now": "code (H_D4_CODE_OWNED_V1)"},
    {"leg": "D4", "key": "analysis_id", "use": "provenance", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "final_output_checksum", "use": "provenance", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "research_input_id", "use": "D3->D4 chain", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "research_output_checksum", "use": "D3->D4 chain", "producer_v1": "code", "now": "code (None: D3 has no output)"},
    {"leg": "D4", "key": "initial_failure_codes", "use": "refused projection", "producer_v1": "code", "now": "n/a"},
    {"leg": "D4", "key": "expectation_gap", "use": "APPROVE §L precondition; REJECT adverse gap; WATCH", "producer_v1": "AI (C1/C2/C5/C6 reject only)", "now": "UNKNOWN (no frozen generator)"},
    {"leg": "D4", "key": "expectation_gap_confidence", "use": "APPROVE §L precondition", "producer_v1": "AI (<= ceiling)", "now": "UNKNOWN (C5)"},
    {"leg": "D4", "key": "confidence_ceiling", "use": "record only", "producer_v1": "code (C4; C3 from AI conflicts)", "now": "code (C4)"},
    {"leg": "D4", "key": "d6_approve_precondition", "use": "cross-check vs D6's own §L", "producer_v1": "code on AI inputs", "now": "code: BLOCKED"},
    {"leg": "D4", "key": "conflicts", "use": "count, no clause", "producer_v1": "AI", "now": "[]"},
    {"leg": "D4", "key": "unknown_fields", "use": "count, no clause", "producer_v1": "AI", "now": "code list"},
    {"leg": "D4", "key": "terminal_failure_codes", "use": "record only", "producer_v1": "code", "now": "n/a"},
)

D4_REASONS_ALWAYS: tuple[str, ...] = (
    "GAP_STATE_HAS_NO_FROZEN_CODE_GENERATOR_D0_SL",
    "C1_POSITIVE_INADMISSIBLE_NON_PRICE_ITEMS_ARE_MODEL_WRITTEN",
    "NEUTRAL_OR_NEGATIVE_NEEDS_JUDGED_RATIONALE_NO_FROZEN_RULE",
    "PRICED_IN_ASSESSMENT_AI_OWNED",
    "C3_INPUT_MODEL_DECLARED_CONFLICTS_NOT_EVALUATED",
    "C5_UNKNOWN_GAP_PAIRS_UNKNOWN_CONFIDENCE",
)


def sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _package_body(package: Any) -> dict[str, Any]:
    if isinstance(package, Mapping):
        return dict(package)
    return json.loads(package.model_dump_json())


def code_owned_fundamental_changes(bundle: Mapping[str, Any], cik: str) -> list[dict[str, Any]]:
    """D1/E2's own states, per metric, with the SEC filing the latest point came from. No new reading."""
    out: list[dict[str, Any]] = []
    for metric, body in sorted((bundle.get("fundamental_changes") or {}).items()):
        if not isinstance(body, Mapping):
            continue
        points = [p for p in body.get("points") or [] if isinstance(p, Mapping)]
        latest = next((p for p in reversed(points) if p.get("current_accession")), None)
        accession = None if latest is None else latest.get("current_accession")
        out.append({"metric": metric, "code_owned_state": body.get("state"), "confidence": body.get("confidence"),
                    "current_value": body.get("current_value"),
                    "period_end": None if latest is None else latest.get("end"),
                    "source_id": f"SEC:{cik}:{accession}" if accession else None})
    return out


def d3_code_owned(prep: Mapping[str, Any], *, run_id: str, work: Path | None = None) -> dict[str, Any]:
    """The code-owned D3 record. ``final_output`` is None by design (see module docstring)."""
    ident = prep["identity"]
    package = _package_body(prep["package"])
    bundle = package.get("evidence_bundle") or {}
    changes = code_owned_fundamental_changes(bundle, ident.cik)
    evidence = {
        "fundamental_changes": changes,
        "trigger_accessions_chunked": list(prep.get("triggers_chunked") or []),
        "chunk_count": len(package.get("chunks") or []),
        "package_sha256": (prep.get("package_meta") or {}).get("package_sha256"),
        "data_cutoff": str(bundle.get("data_cutoff")),
        "growth_durability_state": "UNKNOWN",
        "future_business_state": "UNKNOWN",
    }
    record = {
        "record_schema": "h_d3_code_owned_v1", "generator": D3_GENERATOR, "ticker": ident.ticker,
        "cik": ident.cik, "research_id": f"D3C-{ident.ticker}-{run_id}", "final_status": D3_STATUS,
        "final_output": None, "final_output_checksum": None, "model": None, "model_calls": 0,
        "code_owned": evidence, "code_owned_checksum": sha(evidence),
        "not_produced": dict(D3_NOT_PRODUCED),
        "reason_codes": ["D3_INTERPRETATION_AI_OWNED_NOT_PRODUCED",
                         "D6_PROJECTS_D3_REFUSED_NO_FINAL_OUTPUT_DECISION_STANDS"],
    }
    if work is not None:
        (work / "d3_code_owned.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                 encoding="utf-8")
    return record


def d4_from_bundle(bundle: Any, d3: Mapping[str, Any], *, analysis_id: str) -> dict[str, Any]:
    """The frozen D4 rules applied to a code-owned expectation bundle. Pure; used by replay too."""
    from app.backtest.strategy_h_v2.expectation.gap_contract import (
        ContractRule, ExpectationGapState, confidence_ceiling, d6_approve_precondition)
    from app.backtest.strategy_h_v2.research.schema import Confidence

    body = bundle if isinstance(bundle, Mapping) else json.loads(bundle.model_dump_json())
    status = lambda block: ((body.get(block) or {}).get("status"))            # noqa: E731
    consensus_available = status("consensus") not in (None, "SOURCE_NOT_AVAILABLE")
    revisions_available = status("estimate_revisions") not in (None, "SOURCE_NOT_AVAILABLE")
    ceiling, fired = confidence_ceiling(consensus_available=consensus_available,
                                        estimate_revisions_available=revisions_available,
                                        has_unresolved_material_conflict=False)
    reactions = body.get("price_reaction") or []
    reasons = list(D4_REASONS_ALWAYS)
    rules = [r.value for r in fired] + [ContractRule.C5_UNKNOWN_GAP_FORCES_UNKNOWN_CONFIDENCE.value]
    if not reactions:
        # C6: no non-price item (none is code-owned) and no price reaction -> UNKNOWN is forced.
        reasons.append("C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN")
        rules.append(ContractRule.C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN.value)
    if d3.get("final_output") is None:
        reasons.append("D3_INTERPRETATION_NOT_PRODUCED")
    gap, confidence = ExpectationGapState.UNKNOWN, Confidence.UNKNOWN
    located = {name: len((body.get(name) or {}).get("excerpts") or [])
               for name in ("guidance", "earnings_history", "management_expectation_signals")}
    final = {
        "analysis_id": analysis_id, "generator": D4_GENERATOR,
        "expectation_gap": gap.value, "expectation_gap_confidence": confidence.value,
        "confidence_ceiling": ceiling.value, "applied_contract_rules": rules,
        "d6_approve_precondition": d6_approve_precondition(gap, confidence).value,
        "priced_in_assessment": {"state": "UNKNOWN", "confidence": "UNKNOWN"},
        "code_owned_expectation_evidence": {
            "consensus_status": status("consensus"), "estimate_revisions_status": status("estimate_revisions"),
            "price_reaction_events": len(reactions), "located_excerpts": located,
            "non_price_expectation_items_code_owned": 0,
            "expectation_bundle_checksum": sha(body)},
        "conflicts": [], "unknown_fields": ["expectation_gap", "priced_in_assessment"],
        "reason_codes": reasons,
    }
    return {"record_schema": "h_d4_code_owned_v1", "generator": D4_GENERATOR, "final_status": "OK",
            "analysis_id": analysis_id, "model": None, "model_calls": 0,
            "research_input_id": d3.get("research_id"),
            "research_output_checksum": d3.get("final_output_checksum"),
            "final_output": final, "final_output_checksum": sha(final)}


def d4_code_owned(prep: Mapping[str, Any], d3: Mapping[str, Any], *, run_id: str,
                  work: Path | None = None) -> dict[str, Any]:
    """Build the frozen code expectation bundle on the evaluation's own panel, then ``d4_from_bundle``."""
    from app.dev.run_strategy_h_v2_d4_2 import build_bundle_for
    ticker = prep["identity"].ticker
    bundle = build_bundle_for(prep["package"], prep["panel"].close_panel(),
                              bundle_id=f"{run_id}:{ticker}:expectation", generated_at=prep["cutoff"])
    record = d4_from_bundle(bundle, d3, analysis_id=f"D4C-{ticker}-{run_id}")
    if work is not None:
        (work / "expectation_evidence.json").write_text(bundle.model_dump_json(indent=1), encoding="utf-8")
        (work / "d4_code_owned.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                 encoding="utf-8")
    return record
