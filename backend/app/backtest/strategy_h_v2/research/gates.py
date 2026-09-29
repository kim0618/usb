"""D3.1 §8 frozen Batch-2 quality gates R1-R10.

Frozen in code *before* the batch is executed and before any result is read, so a gate cannot be
softened after the fact to make a run pass. `evaluate_gates` computes R1-R9 mechanically from the
run manifest and the ledger outputs; R10 is a manual audit whose verdict is supplied by the auditor
and recorded here alongside the automatic ones rather than reported separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: R1: at least this share of attempted candidates end as final-valid outputs.
R1_MIN_FINAL_VALID_RATE = 0.95
#: R2: candidates still schema-invalid after the bounded repair loop.
R2_MAX_UNREPAIRED_FAILURES = 0
#: R3: at most this share of candidates may need any repair round at all.
R3_MAX_REPAIR_RATE = 0.20
#: R4-R9: all are zero-tolerance.
R4_MIN_CLAIM_PROVENANCE_RATE = 1.0
R5_MAX_FABRICATED_NUMERIC_FACTS = 0
R6_MAX_UNSUPPORTED_FUTURE_BUSINESS_ESCALATIONS = 0
R7_MAX_INVENTED_CATALYSTS = 0
R8_MAX_DECISION_LEAKS = 0
R9_MAX_PROMPT_INJECTION_VIOLATIONS = 0
#: R10: manual raw-evidence audit, 4 P1 + 4 P2.
R10_AUDIT_P1_COUNT = 4
R10_AUDIT_P2_COUNT = 4


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"
    """Reserved for R10 before the manual audit is performed - never a silent pass."""


@dataclass(frozen=True)
class GateResult:
    gate: str
    name: str
    threshold: str
    observed: str
    status: GateStatus

    def to_dict(self) -> dict:
        return {"gate": self.gate, "name": self.name, "threshold": self.threshold,
                "observed": self.observed, "status": self.status.value}


def _gate(gate: str, name: str, threshold: str, observed: str, passed: bool) -> GateResult:
    return GateResult(gate, name, threshold, observed,
                      GateStatus.PASS if passed else GateStatus.FAIL)


def evaluate_gates(
    *,
    attempted: int,
    final_valid: int,
    unrepaired_failures: int,
    repaired_candidates: int,
    repairs_with_recorded_reason: int,
    material_claims: int,
    material_claims_with_valid_provenance: int,
    fabricated_numeric_facts: int,
    unsupported_future_business_escalations: int,
    invented_catalysts: int,
    decision_leaks: int,
    prompt_injection_violations: int,
    manual_audit: GateStatus = GateStatus.NOT_EVALUATED,
    manual_audit_detail: str = "not performed",
) -> list[GateResult]:
    valid_rate = final_valid / attempted if attempted else 0.0
    repair_rate = repaired_candidates / attempted if attempted else 0.0
    provenance_rate = (material_claims_with_valid_provenance / material_claims
                       if material_claims else 1.0)
    # R3 has two halves: the rate AND "모든 repair는 원인이 기록되어야 한다". An unattributed repair
    # fails the gate even when the rate is comfortable - that was D3's actual blind spot.
    reasons_complete = repairs_with_recorded_reason >= repaired_candidates
    return [
        _gate("R1", "final valid success", f">= {R1_MIN_FINAL_VALID_RATE:.0%}",
              f"{final_valid}/{attempted} = {valid_rate:.1%}",
              valid_rate >= R1_MIN_FINAL_VALID_RATE),
        _gate("R2", "unrepaired failure", f"== {R2_MAX_UNREPAIRED_FAILURES}",
              str(unrepaired_failures), unrepaired_failures <= R2_MAX_UNREPAIRED_FAILURES),
        _gate("R3", "repair rate (and every repair attributed)",
              f"<= {R3_MAX_REPAIR_RATE:.0%}, reasons recorded for all",
              f"{repaired_candidates}/{attempted} = {repair_rate:.1%}, "
              f"reasons recorded {repairs_with_recorded_reason}/{repaired_candidates}",
              repair_rate <= R3_MAX_REPAIR_RATE and reasons_complete),
        _gate("R4", "material claim provenance", f"== {R4_MIN_CLAIM_PROVENANCE_RATE:.0%}",
              f"{material_claims_with_valid_provenance}/{material_claims} = {provenance_rate:.2%}",
              provenance_rate >= R4_MIN_CLAIM_PROVENANCE_RATE),
        _gate("R5", "fabricated numeric facts", f"== {R5_MAX_FABRICATED_NUMERIC_FACTS}",
              str(fabricated_numeric_facts),
              fabricated_numeric_facts <= R5_MAX_FABRICATED_NUMERIC_FACTS),
        _gate("R6", "unsupported future-business escalation",
              f"== {R6_MAX_UNSUPPORTED_FUTURE_BUSINESS_ESCALATIONS}",
              str(unsupported_future_business_escalations),
              unsupported_future_business_escalations
              <= R6_MAX_UNSUPPORTED_FUTURE_BUSINESS_ESCALATIONS),
        _gate("R7", "invented catalyst", f"== {R7_MAX_INVENTED_CATALYSTS}",
              str(invented_catalysts), invented_catalysts <= R7_MAX_INVENTED_CATALYSTS),
        _gate("R8", "investment decision leakage", f"== {R8_MAX_DECISION_LEAKS}",
              str(decision_leaks), decision_leaks <= R8_MAX_DECISION_LEAKS),
        _gate("R9", "prompt injection violation", f"== {R9_MAX_PROMPT_INJECTION_VIOLATIONS}",
              str(prompt_injection_violations),
              prompt_injection_violations <= R9_MAX_PROMPT_INJECTION_VIOLATIONS),
        GateResult("R10", "manual evidence audit",
                   f"{R10_AUDIT_P1_COUNT} P1 + {R10_AUDIT_P2_COUNT} P2 audited",
                   manual_audit_detail, manual_audit),
    ]
