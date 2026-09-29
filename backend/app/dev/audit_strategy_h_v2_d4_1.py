"""D4.1 mechanical gate audit: E1-E8 over the stored D4 analysis records.

Read-only, offline, zero model calls. No threshold lives in this file - every one comes from
`d4_1_contract.py`. What this file DOES own is a second, independent statement of what each gate
means, because a gate checked only by the code that enforced it proves nothing (the principle the
D3.1/D3.2/D3.3 audits were each built on).

One thing has to be said plainly rather than left for a reader to notice. E3, E5 and E7 are
*enforced by the validator*, so a final output that exists at all has already passed them: measured
on final outputs alone those gates are near-tautological. The number that carries real information
is the same defect counted on the INITIAL response, before any repair - that is the model's
unassisted behaviour, and it is reported here beside every final count rather than instead of it.
`d4_1_contract` adjudicates on final outputs, which is correct for a contract gate; this audit
reports both so the contract result is not mistaken for a behavioural one.

D4.3R repair (`H_V2_D4_3R_AUDIT_EXECUTION_ALIGNMENT_V1.md`). Two of this file's own detectors were
themselves the cause of D4.3A's M4 and M8 failures, and both are fixed here without touching what
"independent second opinion" means for anything else:

  M4 (`fabricated_consensus`, E3) used to run its OWN regex list to decide ASSERTED-vs-ABSENCE,
  duplicating a judgement `consensus_language.py` already makes for the live validator
  (`validate.py:check_consensus_not_fabricated`). Two independently written classifiers WILL
  disagree eventually - that is what happened - and an "independent second opinion" about whether a
  candidate satisfies the SAME rule the validator enforces has to start from the same answer to
  "does this sentence assert a consensus expectation", not a competing one. So this file now calls
  `asserted_consensus_findings` directly rather than restating the rule. The independence this audit
  still provides is real: it is a fresh statement of WHICH FIELDS to scan and WHEN the rule applies
  (§ below), not a fresh statement of what ASSERTED means.

  M8 (`code_owned_numeric_defects`, E5) used to flag a claim whenever ANY digit in its text failed
  to match the cited code fact's value, which is not what the rule means - the rule is "states a
  DIFFERENT number", not "contains an unrelated one". `numeric_roles.fact_is_restated` decides FIRST
  whether the text restates the fact's value at all (a fiscal-period label, a session count, a rule
  id are never candidates), and only then compares. `_matches_fact` below is a thin wrapper kept for
  its call sites' sake.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index, code_source_id
from app.backtest.strategy_h_v2.expectation.consensus_language import asserted_consensus_findings
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.numeric_roles import fact_is_restated
from app.dev.run_strategy_h_v2_d4_1 import ANALYSES_ROOT, D4_1_ROOT, PACKAGES_DIR

MATERIAL_TYPES = {"FACT", "INTERPRETATION", "INFERENCE"}

#: Restated independently of `analysis_schema.BANNED_D4_FIELD_NAMES` on purpose.
AUDIT_BANNED_FIELDS = frozenset({
    "decision", "recommendation", "rating", "action", "verdict", "approve", "watch", "reject",
    "buy", "sell", "hold", "fair_value", "fair_value_estimate", "intrinsic_value", "price_target",
    "target_price", "valuation", "valuation_view", "upside", "downside_target", "entry", "entry1",
    "entry2", "exit", "stop", "tp1", "tp2", "position_size", "portfolio_weight", "weight",
    "conviction_score", "expectation_gap_score",
})

#: Vocabulary that would make D4 a decision or valuation layer. Matched as whole words in free text.
DECISION_VOCABULARY = tuple(re.compile(rf"\b{p}\b", re.I) for p in (
    "approve", "reject", "buy", "sell", "overvalued", "undervalued", "fair value",
    "intrinsic value", "price target", "target price", "cheap", "expensive", "attractive entry",
    "position size", "we recommend", "recommendation",
))

#: Field paths whose value is a code-filled status token or a version string, never free prose.
NON_PROSE_KEYS = frozenset({
    "consensus_status", "estimate_revisions_status", "state", "claim_type", "confidence",
    "materiality", "origin", "resolution_status", "realization_status", "direction", "unit",
    "schema_version", "contract_version", "gap_contract_version", "prompt_version", "model_name",
    "model_version", "analysis_id", "candidate_id", "ticker", "research_input_id",
    "research_input_checksum", "expectation_evidence_id", "expectation_evidence_checksum",
    "expectation_gap", "expectation_gap_confidence", "confidence_ceiling",
    "d6_approve_precondition", "overall_guidance_state", "applied_contract_rules",
    "wide_positive_deferred_conjunct", "source_id", "evidence_id", "evidence_ids", "sources",
    "decision_time", "created_at", "version",
})

def iter_claims(node: Any, path: str = "root") -> Iterable[tuple[str, dict]]:
    if isinstance(node, dict):
        if "claim_type" in node and "text" in node:
            yield path, node
        for key, value in node.items():
            yield from iter_claims(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from iter_claims(value, f"{path}[{i}]")


def iter_prose(node: Any, path: str = "root", key: str | None = None) -> Iterable[tuple[str, str]]:
    """Every free-text string in the document. Status tokens, ids and version strings are skipped:
    counting `consensus_status = "SOURCE_NOT_AVAILABLE"` as a consensus assertion would turn the
    contract's own honest-absence marker into a defect."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_prose(v, f"{path}.{k}", k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_prose(v, f"{path}[{i}]", key)
    elif isinstance(node, str) and key not in NON_PROSE_KEYS:
        yield path, node


def iter_field_names(node: Any, path: str = "root") -> Iterable[tuple[str, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", k
            yield from iter_field_names(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_field_names(v, f"{path}[{i}]")


def _cited_evidence(claim: dict) -> list[str]:
    ids = [claim["evidence_id"]] if claim.get("evidence_id") else []
    return ids + list(claim.get("evidence_ids") or [])


def _matches_fact(text: str, value: Any, unit: str) -> bool:
    """Does the claim restate the cited code fact's value, or is it purely qualitative?

    D4.3R: delegates to `numeric_roles.fact_is_restated`, which classifies every digit in `text` by
    role FIRST (a fiscal-period label, a session count, a rule id are never restatement candidates)
    and compares only the ones actually playing the VALUE_RESTATEMENT role. Kept as a thin wrapper
    so `audit_output` below did not need to change its call site.
    """
    return fact_is_restated(text, value, unit)


def audit_output(output: dict, *, package: AIResearchInputV1,
                 bundle: ExpectationEvidenceBundleV1) -> dict:
    """Every mechanical defect class for one final output, as a dict of lists (not counts) so a
    reader can see WHICH claim failed rather than only how many did."""
    code_facts = build_code_fact_index(
        bundle, research_facts=package.evidence_bundle.fundamental_changes)
    valid_evidence = ({c.evidence_id for c in package.chunks}
                      | set(bundle.valid_evidence_ids()) | set(code_facts))
    valid_sources = ({s.source_id for s in package.source_manifest}
                     | {code_source_id(bundle.bundle_id)})
    decision_time = bundle.decision_time
    source_by_id = {s.source_id: s for s in package.source_manifest}

    unsourced, e4, e5, e7_fields, e7_text, e3 = [], [], [], [], [], []

    for path, claim in iter_claims(output):
        if claim.get("claim_type") not in MATERIAL_TYPES:
            continue
        cited = _cited_evidence(claim)
        if not cited or not claim.get("source_id"):
            unsourced.append({"path": path, "text": claim["text"][:160], "reason": "no citation"})
            continue
        unknown = [e for e in cited if e not in valid_evidence]
        if unknown:
            unsourced.append({"path": path, "text": claim["text"][:160],
                              "reason": f"unresolvable evidence_id {unknown}"})
        if claim["source_id"] not in valid_sources:
            unsourced.append({"path": path, "text": claim["text"][:160],
                              "reason": f"unresolvable source_id {claim['source_id']}"})
        provenance = source_by_id.get(claim["source_id"])
        if provenance is not None and provenance.available_at > decision_time:
            e4.append({"path": path, "source_id": claim["source_id"],
                       "available_at": provenance.available_at.isoformat()})
        for evidence_id in cited:
            fact = code_facts.get(evidence_id)
            if fact is not None and not _matches_fact(claim["text"], fact.value, fact.unit):
                e5.append({"path": path, "evidence_id": evidence_id, "fact_value": fact.value,
                           "unit": fact.unit, "text": claim["text"][:200]})

    for path, name in iter_field_names(output):
        if name in AUDIT_BANNED_FIELDS:
            e7_fields.append(path)

    consensus_absent = bundle.consensus.status == "SOURCE_NOT_AVAILABLE"
    for path, text in iter_prose(output):
        if consensus_absent:
            for finding in asserted_consensus_findings(text):
                e3.append({"path": path, "trigger": finding.trigger, "sentence": finding.sentence,
                           "text": text[:200]})
        for pattern in DECISION_VOCABULARY:
            if pattern.search(text):
                e7_text.append({"path": path, "match": pattern.pattern, "text": text[:200]})
                break

    return {
        "unsourced_material_claims": unsourced,
        "future_source_leaks": e4,
        "code_owned_numeric_defects": e5,
        "fabricated_consensus": e3,
        "decision_field_leaks": e7_fields,
        "decision_vocabulary_leaks": e7_text,
        "unknown_discipline_violations": _unknown_discipline(output, bundle),
        "unsupported_priced_in": _priced_in(output, valid_evidence),
    }


def _non_price_expectation_items(output: dict) -> list[str]:
    """Rule C1's own definition, restated: a guidance assessment with a real state, a comparative
    result-vs-guidance item, or a sourced management signal change. An absent guidance block does
    NOT count - an absence cannot be the evidence that the market is behind."""
    evidence = output.get("market_expectation_evidence", {})
    items = []
    for a in evidence.get("guidance_assessments", []):
        if a.get("state") not in ("NOT_PROVIDED", "UNKNOWN"):
            items.append(f"guidance:{a.get('metric')}={a.get('state')}")
    for r in evidence.get("result_vs_guidance", []):
        if r.get("state") in ("ABOVE_COMPANY_GUIDANCE", "WITHIN_COMPANY_GUIDANCE",
                              "BELOW_COMPANY_GUIDANCE"):
            items.append(f"result_vs_guidance:{r.get('metric')}={r.get('state')}")
    for m in evidence.get("management_signal_changes", []):
        items.append(f"management_signal:{m.get('topic')}={m.get('direction')}")
    return items


def _unknown_discipline(output: dict, bundle: ExpectationEvidenceBundleV1) -> list[dict]:
    """C1, C5, C6 and the confidence ceiling, restated independently of `validate.py`."""
    out = []
    gap = output.get("expectation_gap")
    confidence = output.get("expectation_gap_confidence")
    non_price = _non_price_expectation_items(output)
    if gap in ("POSITIVE", "WIDE_POSITIVE") and not non_price:
        out.append({"rule": "C1", "detail": f"gap={gap} with no non-price expectation evidence"})
    if (gap == "UNKNOWN") != (confidence == "UNKNOWN"):
        out.append({"rule": "C5", "detail": f"gap={gap} confidence={confidence}"})
    if gap not in ("UNKNOWN", None) and not non_price and not bundle.price_reaction:
        out.append({"rule": "C6", "detail": f"gap={gap} with neither expectation evidence nor a "
                                            "computed price reaction"})
    ceiling_applies = (bundle.consensus.status == "SOURCE_NOT_AVAILABLE"
                       and bundle.estimate_revisions.status == "SOURCE_NOT_AVAILABLE")
    if ceiling_applies and confidence == "HIGH":
        out.append({"rule": "C4", "detail": "confidence=HIGH above the MEDIUM ceiling"})
    return out


def _priced_in(output: dict, valid_evidence: set[str]) -> list[dict]:
    block = output.get("priced_in_assessment") or {}
    if block.get("state") in (None, "UNKNOWN"):
        return []
    out = []
    if not block.get("evidence_ids"):
        out.append({"reason": "no evidence_ids", "state": block.get("state")})
    if not block.get("confidence") or block["confidence"] == "UNKNOWN":
        out.append({"reason": "no real confidence", "state": block.get("state")})
    if not block.get("limitations"):
        out.append({"reason": "no limitation", "state": block.get("state")})
    unknown = [e for e in block.get("evidence_ids") or [] if e not in valid_evidence]
    if unknown:
        out.append({"reason": f"unresolvable evidence_ids {unknown}", "state": block.get("state")})
    return out


def audit_run(run_id: str, *, analyses_root: Path = ANALYSES_ROOT) -> dict:
    """Every stored analysis in one run, plus the initial-response defect diagnostic."""
    run_dir = analyses_root / run_id
    per_candidate = {}
    for ticker_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        ticker = ticker_dir.name
        bundle = ExpectationEvidenceBundleV1.model_validate_json(
            (ticker_dir / "expectation_evidence.json").read_text())
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        for path in sorted(ticker_dir.glob("*.json")):
            if path.name == "expectation_evidence.json":
                continue
            record = json.loads(path.read_text())
            entry = {
                "analysis_id": record["analysis_id"], "final_status": record["final_status"],
                "canonical_model": record["canonical_model"],
                "model_mismatch": record["model_mismatch"], "cost_usd": record["cost_usd"],
                "repair_rounds": len(record["repair_rounds"]),
                "initial_validation_status": record["initial_validation_status"],
                "initial_failure_codes": record["initial_failure_codes"],
                "initial_failure_details": record["initial_failure_details"],
                "applied_contract_rules": record["applied_contract_rules"],
            }
            if record.get("final_output"):
                out = record["final_output"]
                entry["expectation_gap"] = out["expectation_gap"]
                entry["expectation_gap_confidence"] = out["expectation_gap_confidence"]
                entry["confidence_ceiling"] = out["confidence_ceiling"]
                entry["priced_in"] = out["priced_in_assessment"]["state"]
                entry["defects"] = audit_output(out, package=package, bundle=bundle)
                entry["non_price_expectation_items"] = _non_price_expectation_items(out)
            per_candidate[ticker] = entry
    return {"run_id": run_id, "candidates": per_candidate}


def totals(audit: dict) -> dict:
    """The counts `evaluate_d4_1_gates` takes, summed over candidates with a final output."""
    keys = ("unsourced_material_claims", "future_source_leaks", "code_owned_numeric_defects",
            "fabricated_consensus", "decision_field_leaks", "decision_vocabulary_leaks",
            "unknown_discipline_violations", "unsupported_priced_in")
    out = {k: 0 for k in keys}
    attempted = valid = 0
    for entry in audit["candidates"].values():
        attempted += 1
        if entry["final_status"] == "OK":
            valid += 1
        for k in keys:
            out[k] += len(entry.get("defects", {}).get(k, []))
    out["attempted"] = attempted
    out["schema_valid"] = valid
    return out


def main() -> None:
    run_id = sys.argv[1]
    audit = audit_run(run_id)
    (D4_1_ROOT / f"{run_id}.gateaudit.json").write_text(json.dumps(audit, indent=2, default=str))
    print(json.dumps(totals(audit), indent=2))


if __name__ == "__main__":
    main()
