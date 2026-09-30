"""H-V2-D4-H §13/§14: the offline replay's acceptance criteria, as tests rather than as prose.

Every test here reads the stored, already-paid-for D4 records and makes zero live calls. They skip on
a clean checkout, because `data/runtime` is gitignored - which is why the criteria are ALSO asserted
on inline fixtures in `test_d4_h_numeric_role_coverage.py` and `test_d4_h_state_fidelity.py`. What
only this file can check is the part that is about the real corpus: that no M gate moved, and that
the state gate's first measurement is reported at its true value rather than at a convenient one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.expectation.d4_2_contract import D4_2_ROOT
from app.dev.replay_d4_h_integrity_hardening import AUTHORITATIVE_RUNS, replay

ANALYSES_ROOT = D4_2_ROOT / "analyses"
NEEDS_STORED_RUNS = pytest.mark.skipif(
    not all((ANALYSES_ROOT / run).exists() for run, _, _ in AUTHORITATIVE_RUNS)
    or not Path("data/runtime/strategy_h_v2/d4_2/replay"
                "/d4_3r_audit_alignment_replay-20260929T075703Z.json").exists(),
    reason="the stored D4 runs and the D4.3R replay baseline are gitignored runtime data",
)


@pytest.fixture(scope="module")
def report():
    return replay()


@NEEDS_STORED_RUNS
def test_the_replay_is_free(report):
    assert report["live_calls"] == 0
    assert report["live_cost_usd"] == 0.0


@NEEDS_STORED_RUNS
def test_all_three_authoritative_runs_and_all_seven_candidates_are_replayed(report):
    assert [run["run_id"] for run in report["runs"]] == [r for r, _, _ in AUTHORITATIVE_RUNS]
    assert report["totals"]["candidates_replayed"] == 7


# --- §12: no historical M1-M12 status moves --------------------------------------------------------

@NEEDS_STORED_RUNS
def test_every_m_gate_reproduces_its_recorded_status(report):
    """The immutability criterion, measured. A changed gate here would mean D4-H had altered what a
    frozen Tier A verdict measures, which §12 forbids outright."""
    assert report["totals"]["every_m_gate_reproduced"] is True
    for run in report["runs"]:
        moved = [g for g in run["gate_comparison"] if g["changed"]]
        assert moved == [], f"{run['run_id']}: {moved}"
        assert len(run["gate_comparison"]) == 12


@NEEDS_STORED_RUNS
def test_the_d4_3a_baseline_is_the_post_d4_3r_status_not_its_original_gateaudit(report):
    """D4.3A's own `gateaudit.json` records M4/M8 = FAIL from the pre-D4.3R detectors. Comparing
    against it would credit D4-H with D4.3R's repair and would report two gates as "moved". The
    baseline is that run's last status BEFORE D4-H, which is D4.3R's replay counterfactual."""
    d4_3a = next(run for run in report["runs"] if run["run_id"] == "D4_2_A-20260929T072105Z")
    assert "d4_3r_audit_alignment_replay" in d4_3a["recorded_gates_from"]
    assert all(g["recorded"] == "PASS" for g in d4_3a["gate_comparison"])


# --- §14: R1/R2 acceptance -------------------------------------------------------------------------

@NEEDS_STORED_RUNS
def test_no_m8_false_positive_survives_on_the_real_corpus(report):
    """The six compound-audit findings are gone, measured on the stored bytes rather than on the
    inline fixtures that reproduce them."""
    assert report["totals"]["m8_compound_coverage_gap_findings"] == 0


@NEEDS_STORED_RUNS
def test_no_new_true_numeric_mismatch_was_introduced(report):
    """The other half of §14's R1/R2 criterion: the parser change must not manufacture a defect where
    none existed. M8's atomic count was 0 on every run before D4-H and is 0 after."""
    assert report["totals"]["m8_new_true_numeric_defects"] == 0


# --- State Fidelity moved to H1's own replay --------------------------------------------------------

@NEEDS_STORED_RUNS
def test_this_replay_no_longer_measures_the_state_gate(report):
    """D4-H1 revised `CODE_OWNED_STATE_FIDELITY`'s contract, so re-measuring it under this file's
    schema name would publish different numbers for the same schema. D4-H's own stored artifact is the
    immutable record of what V1 measured, and H1's measurement lives in
    `test_d4_h1_offline_replay.py`. R1/R2 and the M-gate immutability check are unchanged and are
    still this file's subject."""
    assert "state_fidelity_eligible" not in report["totals"]
    assert set(report["totals"]) == {
        "candidates_replayed", "m8_new_true_numeric_defects",
        "m8_compound_coverage_gap_findings", "every_m_gate_reproduced"}
