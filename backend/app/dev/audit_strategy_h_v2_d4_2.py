"""D4.2 Tier A mechanical audit: M1-M12 over the stored records. Read-only, offline, zero calls.

No threshold or gate text lives here - every one comes from `d4_2_contract.py`, frozen before the
re-run. What this file owns is the mapping from stored records to observations, and it reuses
`audit_strategy_h_v2_d4_1`'s defect detectors deliberately: those detectors are a SECOND opinion,
written independently of the validator that enforced the same rules, and replacing them with a
third would throw that property away to gain nothing.

M3 and M5 are the two D4.1 defects and are the reason this audit exists as its own file. M3 asks
something no D4.1 gate could ask: did any candidate get rejected for honestly reporting that
consensus evidence is absent? Under V1 the answer was yes for every candidate that reached a
second repair round, and the gate that should have caught it did not exist.

D4.3R adds two things without touching M1-M12's frozen list or `tier_a_verdict`'s all-PASS
requirement: `_m12_observation` refuses to call a clean-checkout run under a non-project
interpreter official M12 evidence (NOT_EVALUATED instead), and `denominators` makes D4.3A §O's own
hand-written "C1's 0 violations is a zero-denominator result" observation structural - every run
now reports, per rule, whether it had a case to apply to at all. M6/M7's PASS/FAIL is still exactly
`_rules(...) == 0`; the denominator only changes what the disclosed text says about a 0 that never
had anything to fail.
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.contract_v2 import ManagementSignalDirection
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    D4_2_CONTRACT_VERSION,
    D4_2_REMAINING_CAP_USD,
    D4_2_ROOT,
    TIER_A_BUDGET,
    build_gates,
    tier_a_verdict,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION, content_only_schema
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.dev.audit_strategy_h_v2_d4_1 import audit_output
from app.dev.d4_clean_checkout import probe

ANALYSES_ROOT = D4_2_ROOT / "analyses"
REQUESTED_MODEL = "claude-opus-5-5"

#: The V2 validator's consensus rejection text. M3 asks whether this fired on an ABSENCE sentence,
#: which V2 is built never to do; finding one would mean the repair is incomplete.
V2_CONSENSUS_REJECTION = "attributes an expectation to analysts or the market"
#: V1's impossible rejection. It must not appear anywhere in a D4.2 record.
V1_IMPOSSIBLE_CONSENSUS_REJECTION = "text asserts a consensus expectation"


def _records(analyses_root: Path, run_id: str) -> list[dict]:
    root = analyses_root / run_id
    return [json.loads(p.read_text()) for p in sorted(root.rglob("*.json"))
            if p.name != "expectation_evidence.json"]


def _every_failure_detail(record: dict) -> list[str]:
    details = list(record.get("initial_failure_details") or [])
    for round_record in record.get("repair_rounds") or []:
        details.extend(round_record.get("failure_details_before") or [])
    return details


def _direction_enum_is_visible() -> bool:
    """M5's second half: the schema the model is shown must list the members.

    Checked against the generated schema rather than against the Python enum, because the whole
    D4.1 defect was that those two disagreed and only the Python side was ever inspected.
    """
    frozen = sorted(d.value for d in ManagementSignalDirection)
    for definition in (content_only_schema().get("$defs") or {}).values():
        if sorted(definition.get("enum") or []) == frozen:
            return True
    return False


def _m12_observation(clean) -> tuple[bool | None, str]:
    """D4.3R (brief §13): a clean-checkout run under a non-project interpreter is not official M12
    evidence - it measures a different environment's dependency resolution, not this repository's.
    NOT_EVALUATED here, not a silent PASS or a misleading FAIL, so a caller cannot mistake it for
    either."""
    if not clean.official_interpreter:
        return None, (f"skipped: probe ran under {clean.interpreter!r}, not the project venv - "
                       "not official M12 evidence")
    if clean.passed:
        return True, "clean-checkout import passed"
    if clean.blockers():
        return False, "; ".join(f"{b.name}: {b.error.splitlines()[0][:120]}"
                                 for b in clean.blockers())
    if not clean.smoke_executed:
        return False, "smoke did not execute"
    return False, f"smoke failed: {clean.smoke_error}"


def audit_run(run_id: str, *, analyses_root: Path = ANALYSES_ROOT,
              manifest_root: Path = D4_2_ROOT, packages_dir: Path = PACKAGES_DIR) -> dict:
    records = _records(analyses_root, run_id)
    manifest = json.loads((manifest_root / f"{run_id}.manifest.json").read_text())
    per_candidate: list[dict] = []

    for record in records:
        ticker = record["ticker"]
        bundle = ExpectationEvidenceBundleV1.model_validate_json(
            (analyses_root / run_id / ticker / "expectation_evidence.json").read_text())
        package = AIResearchInputV1.model_validate_json(
            (packages_dir / f"{ticker}.json").read_text())
        final = record.get("final_output")
        defects = audit_output(final, package=package, bundle=bundle) if final else {}
        details = _every_failure_detail(record)
        per_candidate.append({
            "ticker": ticker,
            "final_status": record["final_status"],
            "canonical_model": record["canonical_model"],
            "model_mismatch": record["model_mismatch"],
            "repair_rounds": len(record.get("repair_rounds") or []),
            "has_final_output": final is not None,
            "v1_impossible_consensus_rejections": sum(
                1 for d in details if V1_IMPOSSIBLE_CONSENSUS_REJECTION in d),
            "v2_consensus_rejections": sum(1 for d in details if V2_CONSENSUS_REJECTION in d),
            "directions_used": sorted({
                change.get("direction") for change in
                ((final or {}).get("market_expectation_evidence") or {}).get(
                    "management_signal_changes") or []}),
            "telemetry_complete": bool(
                record.get("initial_raw_response", {}).get("raw_text")
                and all((r.get("raw_repair_response") or {}).get("raw_text")
                        for r in record.get("repair_rounds") or [])
                and record.get("started_at") and record.get("completed_at")),
            "expectation_gap": (final or {}).get("expectation_gap"),
            "expectation_gap_confidence": (final or {}).get("expectation_gap_confidence"),
            "consensus_ceiling_applies": (
                bundle.consensus.status == "SOURCE_NOT_AVAILABLE"
                and bundle.estimate_revisions.status == "SOURCE_NOT_AVAILABLE"),
            "defects": defects,
        })

    spends = manifest.get("candidate_spends") or []
    frozen = {d.value for d in ManagementSignalDirection}
    clean = probe()

    def _count(key: str) -> int:
        return sum(len(c["defects"].get(key) or []) for c in per_candidate)

    def _rules(*wanted: str) -> int:
        """`unknown_discipline_violations` is one list covering C1, C4, C5 and C6, so M6 and M7 -
        which are about different rules - are split by the `rule` tag rather than by re-detecting
        them here."""
        return sum(
            1 for c in per_candidate
            for item in c["defects"].get("unknown_discipline_violations") or []
            if item.get("rule") in wanted
        )

    #: brief §10/§11: a rule's 0-violation count is only evidence the rule holds if the rule had a
    #: case to apply to at all. `_denominator("C1")` is exactly D4.3A §O's own hand-written
    #: "positive outputs = 0" observation, made structural instead of prose so a future run cannot
    #: silently lose it. M6 and M7's PASS/FAIL booleans below are unchanged - this only decides
    #: whether a 0-violation rule gets called NOT_EVALUATED in the disclosed text.
    def _denominator(rule: str) -> int:
        if rule == "C1":
            return sum(1 for c in per_candidate
                       if c["expectation_gap"] in ("POSITIVE", "WIDE_POSITIVE"))
        if rule == "C4":
            return sum(1 for c in per_candidate if c["consensus_ceiling_applies"])
        if rule == "C5":
            return sum(1 for c in per_candidate if c["has_final_output"])
        if rule == "C6":
            return sum(1 for c in per_candidate
                       if c["expectation_gap"] not in ("UNKNOWN", None))
        raise ValueError(rule)

    def _rule_status(rule: str) -> str:
        violations = _rules(rule)
        if violations:
            return "FAIL"
        return "PASS" if _denominator(rule) else "NOT_EVALUATED"

    def _rule_summary(*rules: str) -> str:
        return "; ".join(
            f"{r}: {_rule_status(r)} ({_denominator(r)} eligible, {_rules(r)} violations)"
            for r in rules
        )

    denominators = {r: {"eligible": _denominator(r), "violations": _rules(r),
                         "status": _rule_status(r)} for r in ("C1", "C4", "C5", "C6")}

    observations = {
        "M1": (all(c["canonical_model"] == REQUESTED_MODEL and not c["model_mismatch"]
                   for c in per_candidate) and bool(per_candidate),
               f"{sum(1 for c in per_candidate if c['canonical_model'] == REQUESTED_MODEL)}/"
               f"{len(per_candidate)} canonical == {REQUESTED_MODEL}"),
        "M2": (all(c["has_final_output"] for c in per_candidate) and bool(per_candidate),
               f"{sum(1 for c in per_candidate if c['has_final_output'])}/{len(per_candidate)} "
               "final outputs schema-valid"),
        "M3": (all(c["v1_impossible_consensus_rejections"] == 0 for c in per_candidate),
               f"{sum(c['v1_impossible_consensus_rejections'] for c in per_candidate)} rejections "
               "of an honest consensus-absence statement"),
        "M4": (_count("fabricated_consensus") == 0,
               f"{_count('fabricated_consensus')} asserted consensus expectations in final output"),
        "M5": (all(set(c["directions_used"]) <= frozen for c in per_candidate)
               and _direction_enum_is_visible(),
               f"directions used {sorted({d for c in per_candidate for d in c['directions_used']})}"
               f"; enum visible in generated schema: {_direction_enum_is_visible()}"),
        "M6": (_rules("C1", "C5", "C6") == 0,
               f"{_rules('C1', 'C5', 'C6')} C1/C5/C6 violations | {_rule_summary('C1', 'C5', 'C6')}"),
        "M7": (_rules("C4") == 0,
               f"{_rules('C4')} confidences above the frozen MEDIUM ceiling (C4) | "
               f"{_rule_summary('C4')}"),
        "M8": (_count("code_owned_numeric_defects") == 0,
               f"{_count('code_owned_numeric_defects')} claims restating a code-owned number"),
        "M9": (_count("decision_field_leaks") == 0 and _count("decision_vocabulary_leaks") == 0,
               f"{_count('decision_field_leaks')} prohibited fields, "
               f"{_count('decision_vocabulary_leaks')} prohibited vocabulary hits"),
        "M10": (all(c["telemetry_complete"] for c in per_candidate) and bool(per_candidate),
                f"{sum(1 for c in per_candidate if c['telemetry_complete'])}/{len(per_candidate)} "
                "records carry full untruncated telemetry"),
        "M11": (bool(spends) and all(
            s["candidate_total_cost_usd"] <= TIER_A_BUDGET.candidate_worst_case_budget_usd
            for s in spends) and manifest["run_total_cost_usd"] <= D4_2_REMAINING_CAP_USD,
            f"max candidate ${max((s['candidate_total_cost_usd'] for s in spends), default=0):.2f} "
            f"vs reserve ${TIER_A_BUDGET.candidate_worst_case_budget_usd:.2f}; run total "
            f"${manifest.get('run_total_cost_usd', 0):.2f} vs remaining cap "
            f"${D4_2_REMAINING_CAP_USD:.2f}"),
        "M12": _m12_observation(clean),
    }

    gates = build_gates(observations)
    verdict = tier_a_verdict(gates)
    return {
        "schema": "H_V2_D4_2_TIER_A_AUDIT_V1", "run_id": run_id,
        "contract_version": D4_2_CONTRACT_VERSION, "prompt_version": PROMPT_VERSION,
        "candidates": per_candidate,
        "clean_checkout": clean.to_dict(),
        "denominators": denominators,
        "gates": [g.to_dict() for g in gates],
        "verdict": verdict.value,
        "verdict_means": (
            "whether the frozen Expectation Gap contract can be executed live. It is not a "
            "statement about interpretation quality: Tier A's issuers are three of D3.3's twelve "
            "and were read closely while D4 was designed."
        ),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.dev.audit_strategy_h_v2_d4_2 <run_id>")
    report = audit_run(sys.argv[1])
    (D4_2_ROOT / f"{sys.argv[1]}.gateaudit.json").write_text(
        json.dumps(report, indent=2, default=str))
    print(json.dumps({"verdict": report["verdict"],
                      "gates": [g for g in report["gates"]]}, indent=2))


if __name__ == "__main__":
    main()
