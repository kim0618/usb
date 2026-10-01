"""H-V2-D4-B Tier B audit: E1-E8 + SF1 + C1/C4 over the stored Tier B records. Offline, 0 calls, $0.

No threshold lives in this file. E1-E8 come from `d4_1_contract` exactly as they were frozen before
D4.1 ran; SF1's id, threshold and aggregation come from `d4_b_contract`, frozen before Tier B ran; the
verdict is `d4_b_contract.d4_b_verdict`. What this file owns is the mapping from stored records to
observations, and it reuses the existing second-opinion detectors rather than writing third ones:

    D4 defects   `audit_strategy_h_v2_d4_1.audit_output`   independent of the validator that enforced
    D3 defects   `audit_strategy_h_v2_d3_3.audit_output`   the D3 leg's own frozen audit, unchanged

Two things worth stating because they are easy to get wrong in the direction of a false defect.

E2 and compound claims (brief §19). `ClaimV2`'s compound form REQUIRES `source_id` to be null, so
treating a null `source_id` as "unsourced" would demand an output the schema rejects. That was D4-S's
finding and the audit already carries the fix: `_citation_form` reads the form from the schema's own
definition and `classify_claim_sourcing` decides the sourcing obligation structurally. E2's numerator
is `material_source_required_defects` - A-type claims whose citation does not resolve - and B (code-
owned-fact explanations, held to M8's stricter test) and C (UNKNOWN, citation-exempt by schema) are
out of the denominator.

E6/E8 are fed the MECHANICAL count, not `None`. `d4_1_contract.evaluate_d4_1_gates` types both as
manual-audit inputs because D4.1 planned a human read for them, and `None` there means NOT_EVALUATED.
`audit_output` computes both mechanically over final outputs - `unknown_discipline_violations` is
C1/C5/C6 plus the confidence ceiling, `unsupported_priced_in` is a non-UNKNOWN priced-in assessment
missing evidence_ids, confidence or limitations - so passing those counts is strictly stronger than
declaring the gates unevaluated. The manual audit (`manual_audit_strategy_h_v2_d4_1`) is performed
separately and reported separately; it is not what these two gates are computed from.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    E_GATES_DO_NOT_PROVE,
    E_GATES_PROVE,
    GateStatus,
    TIER_B_SAMPLE,
    evaluate_d4_1_gates,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import TIER_B_BUDGET
from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
    ANALYSES_ROOT,
    D3_LEG_ATTEMPTS_ROOT,
    D4_B_CONTRACT_VERSION,
    D4_B_CORE_GATES,
    D4_B_GATES,
    D4_B_ROOT,
    M8_ATOMIC_NUMERIC_OWNERSHIP,
    M8_R1_STATE_TOKEN_COVERAGE,
    M8_R2_WINDOW_ELLIPSIS_COVERAGE,
    M8_R3_COMPOUND_SET_VALUED,
    M8_SCOPE_NOTE,
    SF1_GATE_ID,
    TIER_B_HARD_CAP_USD,
    d4_b_verdict,
    d5_authorization,
    sample_integrity,
    sf1_result,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.dev.audit_strategy_h_v2_d3_3 import PackageIndex
from app.dev.audit_strategy_h_v2_d3_3 import audit_output as audit_d3_output
from app.dev.audit_strategy_h_v2_d4_1 import audit_output, material_source_required_defects

REQUESTED_MODEL = "claude-opus-5-5"


def _records(analyses_root: Path, run_id: str) -> list[dict]:
    root = analyses_root / run_id
    return [json.loads(p.read_text()) for p in sorted(root.rglob("*.json"))
            if p.name != "expectation_evidence.json"]


def _d3_attempts(attempts_root: Path, run_id: str) -> dict[str, dict]:
    """Ticker -> that ticker's D3 attempt from THIS run. Keyed on `research_run_id` so a rerun
    cannot pick up another run's leg."""
    index: dict[str, dict] = {}
    for path in sorted(attempts_root.rglob("*.json")):
        record = json.loads(path.read_text())
        if record.get("research_run_id") != run_id:
            continue
        index[record["ticker"]] = record
    return index


def audit_run(run_id: str, *, analyses_root: Path = ANALYSES_ROOT,
              d3_leg_root: Path = D3_LEG_ATTEMPTS_ROOT, manifest_root: Path = D4_B_ROOT,
              packages_dir: Path = PACKAGES_DIR) -> dict:
    manifest = json.loads((manifest_root / f"{run_id}.manifest.json").read_text())
    records = {r["ticker"]: r for r in _records(analyses_root, run_id)}
    d3_attempts = _d3_attempts(d3_leg_root, run_id)

    candidates: list[dict] = []
    for entry in TIER_B_SAMPLE:
        ticker = entry.ticker
        result = next((r for r in manifest["results"] if r["ticker"] == ticker), None)
        if result is None:
            candidates.append({"ticker": ticker, "cik": entry.cik, "attempted": False})
            continue

        package = AIResearchInputV1.model_validate_json(
            (packages_dir / f"{ticker}.json").read_text())
        d3 = d3_attempts.get(ticker)
        row: dict = {
            "ticker": ticker, "cik": entry.cik, "depth": entry.depth, "priority": entry.priority,
            "attempted": True,
            "d3_final_status": (d3 or {}).get("final_status"),
            "d3_initial_valid": (d3 or {}).get("initial_validation_status") == "OK",
            "d3_repair_rounds": len((d3 or {}).get("repair_attempts") or []),
            "d3_canonical_model": (d3 or {}).get("canonical_model"),
            "d3_model_mismatch": (d3 or {}).get("model_mismatch"),
            "d3_package_checksum": (d3 or {}).get("input_package_checksum"),
            "d4_final_status": result.get("final_status"),
        }
        if d3 and d3.get("final_output"):
            row["d3_audit"] = audit_d3_output(ticker, d3["final_output"], PackageIndex(package))

        record = records.get(ticker)
        if record is None or not record.get("final_output"):
            row.update({"has_final_output": False, "defects": {}})
            candidates.append(row)
            continue

        bundle = ExpectationEvidenceBundleV1.model_validate_json(
            (analyses_root / run_id / ticker / "expectation_evidence.json").read_text())
        defects = audit_output(record["final_output"], package=package, bundle=bundle)
        row.update({
            "has_final_output": True,
            "d4_canonical_model": record.get("canonical_model"),
            "d4_model_mismatch": record.get("model_mismatch"),
            "d4_initial_valid": record.get("initial_validation_status") == "OK",
            "d4_repair_rounds": len(record.get("repair_rounds") or []),
            "d4_repair_causes": [c for r in (record.get("repair_rounds") or [])
                                 for c in (r.get("failure_codes_before") or [])],
            "expectation_gap": record["final_output"].get("expectation_gap"),
            "expectation_gap_confidence": record["final_output"].get(
                "expectation_gap_confidence"),
            "priced_in": (record["final_output"].get("priced_in_assessment") or {}).get("state"),
            "consensus_status": bundle.consensus.status,
            "estimate_revisions_status": bundle.estimate_revisions.status,
            "consensus_ceiling_applies": (
                bundle.consensus.status == "SOURCE_NOT_AVAILABLE"
                and bundle.estimate_revisions.status == "SOURCE_NOT_AVAILABLE"),
            "telemetry_complete": bool(
                record.get("initial_raw_response", {}).get("raw_text")
                and all((r.get("raw_repair_response") or {}).get("raw_text")
                        for r in record.get("repair_rounds") or [])
                and record.get("started_at") and record.get("completed_at")),
            "defects": defects,
        })
        candidates.append(row)

    graded = [c for c in candidates if c.get("has_final_output")]
    attempted = [c for c in candidates if c.get("attempted")]

    def _count(key: str) -> int:
        return sum(len(c["defects"].get(key) or []) for c in graded)

    def _rules(*wanted: str) -> int:
        return sum(1 for c in graded
                   for item in c["defects"].get("unknown_discipline_violations") or []
                   if item.get("rule") in wanted)

    def _denominator(rule: str) -> int:
        """A rule's 0-violation count is only evidence the rule holds if the rule had a case to apply
        to (brief §14/§26). C1's denominator is the POSITIVE-family outputs; C4's is the candidates
        whose consensus ceiling actually applies."""
        if rule == "C1":
            return sum(1 for c in graded
                       if c["expectation_gap"] in ("POSITIVE", "WIDE_POSITIVE"))
        if rule == "C4":
            return sum(1 for c in graded if c["consensus_ceiling_applies"])
        if rule == "C5":
            return len(graded)
        if rule == "C6":
            return sum(1 for c in graded if c["expectation_gap"] not in ("UNKNOWN", None))
        raise ValueError(rule)

    def _rule_status(rule: str) -> str:
        if _rules(rule):
            return "FAIL"
        return "PASS" if _denominator(rule) else "NOT_EVALUATED"

    rules = {r: {"eligible": _denominator(r), "violations": _rules(r), "status": _rule_status(r)}
             for r in ("C1", "C4", "C5", "C6")}

    # --- SF1, aggregated exactly as the preregistration froze it -----------------------------
    sf_evaluated = sum(c["defects"]["code_owned_state_fidelity"]["assertions_evaluated"]
                       for c in graded)
    sf_failed = sum(c["defects"]["code_owned_state_fidelity"]["assertions_failed"] for c in graded)
    sf1 = sf1_result(evaluated_assertions=sf_evaluated, failed_assertions=sf_failed)

    e_gates = evaluate_d4_1_gates(
        attempted=len(attempted),
        schema_valid=len(graded),
        unsourced_material_gap_claims=sum(
            len(material_source_required_defects(c["defects"])) for c in graded),
        fabricated_consensus=_count("fabricated_consensus"),
        future_source_leaks=_count("future_source_leaks"),
        code_owned_numeric_defects=_count("code_owned_numeric_defects"),
        unknown_discipline_violations=_rules("C1", "C4", "C5", "C6"),
        decision_leaks=_count("decision_field_leaks") + _count("decision_vocabulary_leaks"),
        unsupported_priced_in_claims=_count("unsupported_priced_in"),
        tier="B",
    )
    gates = [g.to_dict() for g in e_gates] + [sf1.to_dict()]
    statuses = {g["gate"]: g["status"] for g in gates}
    verdict = d4_b_verdict(statuses)

    spends = manifest.get("candidate_spends") or []
    budget = {
        "hard_cap_usd": TIER_B_HARD_CAP_USD,
        "run_total_cost_usd": manifest.get("run_total_cost_usd"),
        "within_hard_cap": (manifest.get("run_total_cost_usd") or 0.0) <= TIER_B_HARD_CAP_USD,
        "candidate_worst_case_usd": TIER_B_BUDGET.candidate_worst_case_budget_usd,
        "every_candidate_within_its_worst_case": all(
            s["candidate_total_cost_usd"] <= TIER_B_BUDGET.candidate_worst_case_budget_usd
            for s in spends),
        "d3_total_usd": sum(s["preceding_stage_cost_usd"] for s in spends),
        "d4_initial_usd": sum(s["initial_cost_usd"] for s in spends),
        "d4_repair_usd": sum(s["repair_cost_usd"] for s in spends),
        "observed_projection_usd": manifest.get("observed_projection_usd"),
        "stopped_early": manifest.get("stopped_early"),
        "budget_exhausted": manifest.get("budget_exhausted"),
    }

    models = {
        "requested": REQUESTED_MODEL,
        "d3_canonical": sorted({c["d3_canonical_model"] for c in attempted
                                if c.get("d3_canonical_model")}),
        "d4_canonical": sorted({c.get("d4_canonical_model") for c in graded
                                if c.get("d4_canonical_model")}),
        "any_mismatch": any(c.get("d3_model_mismatch") or c.get("d4_model_mismatch")
                            for c in attempted),
    }

    gap_distribution: dict[str, int] = {}
    for c in graded:
        key = str(c["expectation_gap"])
        gap_distribution[key] = gap_distribution.get(key, 0) + 1

    return {
        "schema": "H_V2_D4_B_TIER_B_AUDIT_V1", "run_id": run_id,
        "contract_version": D4_B_CONTRACT_VERSION,
        "manifest_contract_version": manifest.get("contract_version"),
        "prompt_version": PROMPT_VERSION,
        "sample_integrity": {**sample_integrity(),
                             "manifest_checksum": manifest.get("tier_b_checksum")},
        "sample_size": len(TIER_B_SAMPLE), "attempted": len(attempted), "graded": len(graded),
        "models": models,
        "budget": budget,
        "expectation_gap_distribution": gap_distribution,
        "rules": rules,
        "m8_scope": {
            "atomic_numeric_ownership": M8_ATOMIC_NUMERIC_OWNERSHIP,
            "r1_state_token_coverage": M8_R1_STATE_TOKEN_COVERAGE,
            "r2_window_ellipsis_coverage": M8_R2_WINDOW_ELLIPSIS_COVERAGE,
            "r3_compound_set_valued": M8_R3_COMPOUND_SET_VALUED,
            "note": M8_SCOPE_NOTE,
            "atomic_defects": _count("code_owned_numeric_defects"),
            "compound_coverage_gap_findings": _count("compound_claim_coverage_gap"),
        },
        "state_fidelity": {
            "gate": SF1_GATE_ID,
            "assertions_total": sum(
                c["defects"]["code_owned_state_fidelity"]["assertions_total"] for c in graded),
            "assertions_evaluated": sf_evaluated,
            "assertions_failed": sf_failed,
            "not_evaluated_by_reason": _merge_counts(
                c["defects"]["code_owned_state_fidelity"]["not_evaluated_by_reason"]
                for c in graded),
            "polarity_counts": _merge_counts(
                c["defects"]["code_owned_state_fidelity"]["polarity_counts"] for c in graded),
            "violating_claims": [
                {"ticker": c["ticker"], **claim}
                for c in graded
                for claim in c["defects"]["code_owned_state_fidelity"]["violating_claims"]],
        },
        "telemetry_complete": all(c.get("telemetry_complete") for c in graded) and bool(graded),
        "candidates": candidates,
        "gates": gates,
        "core_gates": list(D4_B_CORE_GATES),
        "gate_order": list(D4_B_GATES),
        "verdict": verdict.value,
        "d5_authorization": d5_authorization(verdict),
        "gates_prove": E_GATES_PROVE,
        "gates_do_not_prove": E_GATES_DO_NOT_PROVE,
    }


def _merge_counts(dicts) -> dict[str, int]:
    merged: dict[str, int] = {}
    for d in dicts:
        for key, value in (d or {}).items():
            merged[key] = merged.get(key, 0) + value
    return dict(sorted(merged.items()))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.dev.audit_strategy_h_v2_d4_b <run_id>")
    report = audit_run(sys.argv[1])
    (D4_B_ROOT / f"{sys.argv[1]}.gateaudit.json").write_text(
        json.dumps(report, indent=2, default=str))
    print(json.dumps({
        "verdict": report["verdict"], "d5_authorization": report["d5_authorization"],
        "gates": report["gates"], "rules": report["rules"],
        "expectation_gap_distribution": report["expectation_gap_distribution"],
        "budget": report["budget"], "models": report["models"],
    }, indent=2, default=str))
    if any(g["status"] == GateStatus.FAIL.value for g in report["gates"]):
        print("AT LEAST ONE GATE FAILED", file=sys.stderr)


if __name__ == "__main__":
    main()
