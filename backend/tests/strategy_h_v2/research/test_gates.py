from __future__ import annotations

from app.backtest.strategy_h_v2.research.gates import GateStatus, evaluate_gates


def _clean(**overrides):
    kwargs = dict(
        attempted=24, final_valid=24, unrepaired_failures=0, repaired_candidates=0,
        repairs_with_recorded_reason=0, material_claims=1000,
        material_claims_with_valid_provenance=1000, fabricated_numeric_facts=0,
        unsupported_future_business_escalations=0, invented_catalysts=0, decision_leaks=0,
        prompt_injection_violations=0,
    )
    kwargs.update(overrides)
    return {g.gate: g for g in evaluate_gates(**kwargs)}


def test_clean_run_passes_r1_to_r9():
    gates = _clean()
    assert all(gates[f"R{i}"].status is GateStatus.PASS for i in range(1, 10))


def test_r10_is_never_a_silent_pass():
    assert _clean()["R10"].status is GateStatus.NOT_EVALUATED


def test_r1_fails_below_95_percent():
    assert _clean(final_valid=22)["R1"].status is GateStatus.FAIL
    assert _clean(final_valid=23)["R1"].status is GateStatus.PASS


def test_r3_fails_on_an_unattributed_repair_even_within_the_rate():
    """The rate alone would pass at 4/24. D3's real blind spot was repairs nobody could explain,
    so an unattributed repair fails R3 on its own."""
    within_rate = _clean(repaired_candidates=4, repairs_with_recorded_reason=4)
    assert within_rate["R3"].status is GateStatus.PASS
    unattributed = _clean(repaired_candidates=4, repairs_with_recorded_reason=3)
    assert unattributed["R3"].status is GateStatus.FAIL


def test_r4_fails_on_a_single_bad_provenance_claim():
    gates = _clean(material_claims_with_valid_provenance=999)
    assert gates["R4"].status is GateStatus.FAIL


def test_zero_tolerance_gates_fail_at_one():
    for field, gate in (("fabricated_numeric_facts", "R5"),
                        ("unsupported_future_business_escalations", "R6"),
                        ("invented_catalysts", "R7"),
                        ("decision_leaks", "R8"),
                        ("prompt_injection_violations", "R9")):
        assert _clean(**{field: 1})[gate].status is GateStatus.FAIL
