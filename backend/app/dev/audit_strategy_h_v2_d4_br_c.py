"""H-V2-D4-BR-C audit: E1-E8 + SF1 + C1/C4 over the stored confirmation records. Offline, 0 calls.

No threshold lives here either. This is `audit_strategy_h_v2_d4_b.audit_run` pointed at the
confirmation's sample and store, so the gates that grade four unseen issuers are byte-for-byte the
gates that graded the frozen six - E1-E8 from `d4_1_contract` as frozen before D4.1 ran, SF1 from
`d4_b_contract` as frozen before Tier B ran, and the same second-opinion detectors.

The one thing added on top is `d4_br_c_contract.d4_br_c_verdict`, which runs the frozen
`d4_b_verdict` and can only move a PASS down to PASS_WITH_LIMITATIONS when the run's repair
dependence is high. It has no path to lifting a gate.

E1 at n=4. The gate is a RATE against the frozen >= 95%, so nothing about it is restated for a
denominator of four: 4/4 is 100% and passes, 3/4 is 75% and fails. `e1_confirmation_passes` is
asserted against the gate's own verdict below rather than trusted to agree with it.
"""

from __future__ import annotations

import json
import sys

from app.backtest.strategy_h_v2.expectation.d4_1_contract import GateStatus
from app.backtest.strategy_h_v2.expectation.d4_br_c_contract import (
    ANALYSES_ROOT,
    D3_LEG_ATTEMPTS_ROOT,
    D4_BR_C_CONTRACT_VERSION,
    D4_BR_C_HARD_CAP_USD,
    D4_BR_C_ROOT,
    d4_br_c_verdict,
    repair_dependence_is_high,
    sample_integrity,
)
from app.backtest.strategy_h_v2.expectation.d4_br_contract import (
    D4_BR_CONFIRMATION_SAMPLE,
    e1_confirmation_passes,
)
from app.dev.audit_strategy_h_v2_d4_b import audit_run as audit_tier_b_run

SCHEMA = "H_V2_D4_BR_C_CONFIRMATION_AUDIT_V1"


def audit_confirmation(run_id: str, *, structural_instability_found: bool = False) -> dict:
    def _verdict(statuses: dict[str, str], diagnostics: dict):
        return d4_br_c_verdict(
            statuses,
            repair_dependence_high=repair_dependence_is_high(
                diagnostics["full_repair_budget_candidates"]),
            structural_instability_found=structural_instability_found,
        )

    report = audit_tier_b_run(
        run_id,
        analyses_root=ANALYSES_ROOT,
        d3_leg_root=D3_LEG_ATTEMPTS_ROOT,
        manifest_root=D4_BR_C_ROOT,
        sample=D4_BR_CONFIRMATION_SAMPLE,
        hard_cap_usd=D4_BR_C_HARD_CAP_USD,
        schema=SCHEMA,
        contract_version=D4_BR_C_CONTRACT_VERSION,
        sample_integrity_fn=sample_integrity,
        verdict_fn=_verdict,
    )

    # The §17 convergence line, read off the rows rather than recounted by hand.
    graded = [c for c in report["candidates"] if c.get("has_final_output")]
    attempted = [c for c in report["candidates"] if c.get("attempted")]
    e1 = next(g for g in report["gates"] if g["gate"] == "E1")
    report["convergence"] = {
        "d3_final_valid": sum(1 for c in attempted if c.get("d3_final_status") == "OK"),
        "d4_initial_valid": sum(1 for c in attempted if c.get("d4_initial_valid")),
        "d4_final_valid": len(graded),
        "attempted": len(attempted),
        "e1_gate_status": e1["status"],
        "e1_confirmation_rule_passes": e1_confirmation_passes(len(graded), len(attempted) or 1),
        "repair_candidates": report["repair_dependence"]["candidates_needing_repair"],
        "repair_rounds": report["repair_dependence"]["total_repair_rounds"],
        "repair_dependence_high": repair_dependence_is_high(
            report["repair_dependence"]["full_repair_budget_candidates"]),
        "attempted_consensus_attributions": sum(
            (c.get("attempted_consensus_attributions") or 0) for c in attempted),
        "attempted_quantified_consensus": sum(
            (c.get("attempted_quantified_consensus") or 0) for c in attempted),
        "final_unsupported_consensus": sum(
            len(c["defects"].get("fabricated_consensus") or []) for c in graded),
        "abstaining_candidates": [
            c["ticker"] for c in graded if c.get("expectation_gap") == "UNKNOWN"],
        "structural_instability_found": structural_instability_found,
    }
    # The two readings of E1 must agree, or one of them is wrong about the frozen threshold.
    assert report["convergence"]["e1_confirmation_rule_passes"] == (
        e1["status"] == GateStatus.PASS.value), (
        "the confirmation's 4/4 rule and the E1 gate disagree; they are the same threshold"
    )
    return report


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.dev.audit_strategy_h_v2_d4_br_c <run_id>")
    report = audit_confirmation(sys.argv[1])
    (D4_BR_C_ROOT / f"{sys.argv[1]}.gateaudit.json").write_text(
        json.dumps(report, indent=2, default=str))
    print(json.dumps({
        "verdict": report["verdict"], "d5_authorization": report["d5_authorization"],
        "convergence": report["convergence"],
        "gates": report["gates"], "rules": report["rules"],
        "state_fidelity": {k: report["state_fidelity"][k] for k in
                           ("assertions_total", "assertions_evaluated", "assertions_failed")},
        "m8_atomic_defects": report["m8_scope"]["atomic_defects"],
        "expectation_gap_distribution": report["expectation_gap_distribution"],
        "budget": report["budget"], "models": report["models"],
        "sample_integrity": report["sample_integrity"],
    }, indent=2, default=str))
    if any(g["status"] == GateStatus.FAIL.value for g in report["gates"]):
        print("AT LEAST ONE GATE FAILED", file=sys.stderr)


if __name__ == "__main__":
    main()
