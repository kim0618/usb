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


# --- §14: State Fidelity acceptance ----------------------------------------------------------------

@NEEDS_STORED_RUNS
def test_the_state_gate_had_a_real_denominator(report):
    """§11's rule in the direction it is usually needed. A 0-eligible measurement would be
    NOT_EVALUATED and would say nothing about the gate; this one had 38 eligible claims."""
    assert report["totals"]["state_fidelity_eligible"] > 0


@NEEDS_STORED_RUNS
def test_the_state_gate_findings_are_reported_at_their_measured_value(report):
    """§14: an actual state mismatch in the stored outputs is not hidden. The measured numbers are
    pinned here so a later change to the gate's contract cannot quietly move them - the mechanism
    behind each is §F/§H of `H_V2_D4_H_PRE_TIER_B_INTEGRITY_HARDENING_V1.md`, and this test asserts
    the count, not that the count is acceptable."""
    totals = report["totals"]
    assert totals["state_fidelity_violating_claims"] == 6
    assert totals["state_fidelity_unowned_tokens"] == 8
    assert (totals["unowned_tokens_the_candidate_owns_uncited"]
            + totals["unowned_tokens_no_metric_of_the_candidate_holds"]
            == totals["state_fidelity_unowned_tokens"])
    assert totals["unowned_tokens_the_candidate_owns_uncited"] == 5
    assert totals["unowned_tokens_no_metric_of_the_candidate_holds"] == 3


@NEEDS_STORED_RUNS
def test_no_finding_is_a_state_the_candidate_could_not_have_seen(report):
    """The substantive reading of the eight, and the reason the verdict is NEEDS REVISION rather than
    FAIL: none of them is a state invented out of nothing.

    Five are states the candidate's OWN `fundamental_changes` block holds under a metric the claim did
    not cite - a citation-set finding, structurally decidable and asserted here. The other three are
    BSY naming ACCELERATING or IMPROVING only to DENY them ("not accelerating", "mixed rather than
    improving"); the gate has no negation scope, which is the second mechanism §F records. Both are
    gate false positives, and neither is repaired in D4-H: adjusting the contract after seeing these
    counts is the result-driven tuning §14 exists to prevent.
    """
    denied_by_the_prose = {
        ("BSY", "root.fundamental_reality_summary.improvement_claims[4]", "ACCELERATING"),
        ("BSY", "root.gap_rationale[2]", "IMPROVING"),
        ("BSY", "root.fundamental_reality_summary.improvement_claims[3]", "IMPROVING"),
    }
    seen = set()
    for run in report["runs"]:
        for candidate in run["candidates"]:
            for violation in candidate["violations"]:
                key = (candidate["ticker"], violation["path"], violation["unowned_state"])
                if violation["candidate_owns_it_uncited"]:
                    assert violation["unowned_state"] in candidate["candidate_owned_states"]
                    continue
                assert key in denied_by_the_prose, (
                    f"a state no metric of {candidate['ticker']} holds and the prose does not deny: "
                    f"{key} - this WOULD be a true fidelity violation")
                seen.add(key)
    assert seen == denied_by_the_prose
