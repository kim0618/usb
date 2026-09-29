"""D4.3R tests (`H_V2_D4_3R_AUDIT_EXECUTION_ALIGNMENT_V1.md`): the audit/execution semantic
alignment repair. Three concerns, each with its own section below:

  M4 unification - the audit's `fabricated_consensus` detector must agree with the live validator's
  `consensus_language` classifier, not restate it with a second regex list (§3-4 of the brief).

  Zero-denominator / gate aggregation - a rule with no eligible case must read NOT_EVALUATED, not a
  silent PASS, and a NOT_EVALUATED core gate must not let `tier_a_verdict` read READY (§10-11).

  Clean-checkout smoke - a skipped smoke must never read as a passed one, and a non-project
  interpreter's result must not count as official M12 evidence (§12-13).

M8's numeric-role repair has its own file, `test_numeric_roles.py`.

The D4.3A-record-dependent checks (does the repaired audit actually clear M4/M8 on the real stored
Tier A V2 responses) live under their own `NEEDS_STORED_D4_3A_RUN` skip, exactly like
`test_d4_1_artifacts.py`'s own convention: the records are gitignored runtime data, absent on a
fresh checkout, and a test failing for that reason would be noise rather than a signal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from d4_helpers import d4_content, excerpt, expectation_bundle, package

from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    D4_2_ROOT,
    MechanicalGateStatus,
    build_gates,
    tier_a_verdict,
)
from app.dev.audit_strategy_h_v2_d4_1 import audit_output
from app.dev.d4_clean_checkout import CleanCheckoutReport, ImportProbeResult, official_python, probe

# ---------------------------------------------------------------------------------------------
# M4: the audit no longer runs its own ASSERTED/ABSENCE classifier
# ---------------------------------------------------------------------------------------------

#: D4.3A's own real false positive (§O "M4 diagnosis"), verbatim.
D4_3A_FALSE_POSITIVE_SENTENCE = (
    "Consensus and estimate revisions are SOURCE_NOT_AVAILABLE, so the core input for judging "
    "what the market expects is absent."
)


def _output_with_limitation(sentence: str) -> dict:
    return d4_content(limitations=[sentence])


def test_d4_3a_false_positive_sentence_is_no_longer_flagged():
    bundle = expectation_bundle()  # consensus SOURCE_NOT_AVAILABLE by default
    pkg = package()
    output = _output_with_limitation(D4_3A_FALSE_POSITIVE_SENTENCE)
    defects = audit_output(output, package=pkg, bundle=bundle)
    assert defects["fabricated_consensus"] == []


def test_prescribed_absence_sentence_is_never_flagged():
    bundle = expectation_bundle()
    pkg = package()
    output = _output_with_limitation("Available evidence does not establish consensus expectations.")
    defects = audit_output(output, package=pkg, bundle=bundle)
    assert defects["fabricated_consensus"] == []


def test_a_genuine_consensus_assertion_is_still_flagged():
    """The repair must not become a relaxation (brief §25 "Consensus semantics weakened? NO"):
    an actual asserted consensus expectation, with no absence language anywhere in the sentence,
    is still caught."""
    bundle = expectation_bundle()
    pkg = package()
    output = _output_with_limitation("Analysts expect EPS of $5.00 this quarter.")
    defects = audit_output(output, package=pkg, bundle=bundle)
    assert len(defects["fabricated_consensus"]) == 1


def test_negated_consensus_content_is_still_an_assertion():
    """'Consensus does not expect growth' asserts a DIRECTION, it does not say the evidence is
    unavailable - `consensus_language.py`'s own docstring calls this out by name, and the audit
    must agree with the validator here too."""
    bundle = expectation_bundle()
    pkg = package()
    output = _output_with_limitation("Consensus does not expect growth this year.")
    defects = audit_output(output, package=pkg, bundle=bundle)
    assert len(defects["fabricated_consensus"]) == 1


def test_when_consensus_is_available_the_rule_does_not_apply():
    """M4 only constrains a candidate whose bundle reports consensus SOURCE_NOT_AVAILABLE - brief
    §0 forbids a consensus-provider semantics change, and this is the existing gating condition,
    unmodified."""
    from app.backtest.strategy_h_v2.expectation.evidence_schema import EvidenceBlock
    from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability

    bundle = expectation_bundle(consensus=EvidenceBlock(
        status=EvidenceAvailability.AVAILABLE, excerpts=[excerpt()],
    ))
    pkg = package()
    output = _output_with_limitation("Analysts expect EPS of $5.00 this quarter.")
    defects = audit_output(output, package=pkg, bundle=bundle)
    assert defects["fabricated_consensus"] == []


# ---------------------------------------------------------------------------------------------
# Zero-denominator / gate aggregation (brief §10-11)
# ---------------------------------------------------------------------------------------------

def test_a_gate_missing_from_observations_is_not_evaluated_not_a_silent_pass():
    gates = build_gates({})
    assert all(g.status == MechanicalGateStatus.NOT_EVALUATED for g in gates)
    assert tier_a_verdict(gates).value == "MECHANICAL NOT READY"


def test_none_observation_is_not_evaluated():
    gates = build_gates({"M1": (None, "not performed")})
    by_id = {g.gate: g for g in gates}
    assert by_id["M1"].status == MechanicalGateStatus.NOT_EVALUATED


def test_not_evaluated_core_gate_blocks_ready_even_if_every_other_gate_passes():
    """The failure mode brief §11 names: NOT_EVALUATED must never be silently promoted to PASS by
    the aggregator, and a run where every OTHER gate passed must still not read READY."""
    from app.backtest.strategy_h_v2.expectation.d4_2_contract import M_GATE_IDS

    observations = {gate_id: (True, "ok") for gate_id in M_GATE_IDS}
    observations["M4"] = (None, "not performed this run")  # a core gate, left unevaluated
    gates = build_gates(observations)
    by_id = {g.gate: g for g in gates}
    assert by_id["M4"].status == MechanicalGateStatus.NOT_EVALUATED
    assert tier_a_verdict(gates).value == "MECHANICAL NOT READY"


def test_all_gates_evaluated_and_passing_is_ready():
    from app.backtest.strategy_h_v2.expectation.d4_2_contract import M_GATE_IDS

    observations = {gate_id: (True, "ok") for gate_id in M_GATE_IDS}
    gates = build_gates(observations)
    assert tier_a_verdict(gates).value == "MECHANICAL READY"


# ---------------------------------------------------------------------------------------------
# Clean-checkout smoke (brief §12-13)
# ---------------------------------------------------------------------------------------------

def test_skipped_smoke_is_not_a_pass():
    """The exact vacuous-PASS pattern D4.3A §A.2 found: import failures should skip the smoke
    entirely, and a skipped smoke must never read `smoke_ok: true`."""
    report = CleanCheckoutReport(
        tree="deadbeef",
        results=[ImportProbeResult("d4_contract_v2", "app...", False, "ModuleNotFoundError")],
        smoke_executed=False, smoke_ok=False,
    )
    assert report.smoke_executed is False
    assert report.passed is False


def test_smoke_executed_and_passed_is_a_real_pass():
    report = CleanCheckoutReport(
        tree="deadbeef",
        results=[ImportProbeResult("d4_contract_v2", "app...", True, "")],
        smoke_executed=True, smoke_ok=True,
    )
    assert report.passed is True


def test_smoke_executed_but_failed_is_not_a_pass():
    report = CleanCheckoutReport(
        tree="deadbeef",
        results=[ImportProbeResult("d4_contract_v2", "app...", True, "")],
        smoke_executed=True, smoke_ok=False, smoke_error="AssertionError: boom",
    )
    assert report.passed is False


def test_official_python_resolves_the_project_venv():
    repo_root = Path(__file__).resolve().parents[4]
    interpreter, is_official = official_python(repo_root)
    if (repo_root / ".venv" / "bin" / "python").exists():
        assert is_official is True
        assert interpreter == repo_root / ".venv" / "bin" / "python"
    else:
        assert is_official is False


NEEDS_GIT_AND_VENV = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[4] / ".venv" / "bin" / "python").exists(),
    reason="the official-interpreter probe needs the project venv",
)


@NEEDS_GIT_AND_VENV
def test_probe_under_the_official_interpreter_is_marked_official():
    report = probe()
    assert report.official_interpreter is True
    assert report.interpreter.endswith("/.venv/bin/python")


@NEEDS_GIT_AND_VENV
def test_probe_under_a_forced_non_project_interpreter_is_marked_unofficial():
    import sys

    report = probe(python=Path(sys.executable))
    if Path(sys.executable) == (Path(__file__).resolve().parents[4] / ".venv" / "bin" / "python"):
        pytest.skip("this test process IS already running under the project venv")
    assert report.official_interpreter is False


# ---------------------------------------------------------------------------------------------
# D4.3A stored-record replay (real data, gitignored - skip on a fresh checkout)
# ---------------------------------------------------------------------------------------------

D4_3A_RUN_ID = "D4_2_A-20260929T072105Z"
NEEDS_STORED_D4_3A_RUN = pytest.mark.skipif(
    not (D4_2_ROOT / "analyses" / D4_3A_RUN_ID).exists(),
    reason="D4.3A's stored analysis records are gitignored runtime data",
)


@NEEDS_STORED_D4_3A_RUN
def test_m4_and_m8_pass_on_the_real_d4_3a_stored_records():
    from app.dev.audit_strategy_h_v2_d4_2 import audit_run

    report = audit_run(D4_3A_RUN_ID)
    by_gate = {g["gate"]: g for g in report["gates"]}
    assert by_gate["M4"]["status"] == "PASS"
    assert by_gate["M8"]["status"] == "PASS"


@NEEDS_STORED_D4_3A_RUN
def test_c1_zero_denominator_is_disclosed_while_m6_keeps_its_frozen_pass():
    from app.dev.audit_strategy_h_v2_d4_2 import audit_run

    report = audit_run(D4_3A_RUN_ID)
    by_gate = {g["gate"]: g for g in report["gates"]}
    assert report["denominators"]["C1"]["status"] == "NOT_EVALUATED"
    assert report["denominators"]["C1"]["eligible"] == 0
    assert by_gate["M6"]["status"] == "PASS"


@NEEDS_STORED_D4_3A_RUN
def test_every_other_gate_reproduces_d4_3a_unchanged():
    """brief §16 "other gates unchanged?" - every gate but M4/M8 must reproduce exactly the status
    D4.3A's own stored gate audit recorded."""
    import json

    from app.dev.audit_strategy_h_v2_d4_2 import audit_run

    old = json.loads((D4_2_ROOT / f"{D4_3A_RUN_ID}.gateaudit.json").read_text())
    new = audit_run(D4_3A_RUN_ID)
    old_by_gate = {g["gate"]: g["status"] for g in old["gates"]}
    new_by_gate = {g["gate"]: g["status"] for g in new["gates"]}
    for gate_id, old_status in old_by_gate.items():
        if gate_id in ("M4", "M8"):
            continue
        assert new_by_gate[gate_id] == old_status, f"{gate_id} changed unexpectedly"
