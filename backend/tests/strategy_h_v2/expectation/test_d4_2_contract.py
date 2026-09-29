"""The D4.2 preregistration, and the offline replay that must not rewrite history.

Two properties are worth a test rather than a sentence. The Tier A sample is the SAME three
issuers D4.1 used (brief §24) - a re-run on different issuers would not be a re-run. And the V2
counterfactual replay must leave D4.1's stored records byte-identical, because the moment a replay
can write, "we re-checked the old run" stops meaning anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    D4_CONTRACT_VERSION,
    D4_CONTRACT_VERSION_V1,
    GAP_CONTRACT_VERSION_UNCHANGED,
)
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    E1_MIN_SCHEMA_VALID_RATE,
    E3_MAX_FABRICATED_CONSENSUS,
    TIER_A_TICKERS,
    TIER_B_SAMPLE,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    D4_1_ALREADY_SPENT_USD,
    M_CORE_GATES,
    M_GATES,
    M_GATE_IDS,
    OVERALL_HARD_CAP_USD,
    TIER_A_TICKERS_UNCHANGED,
    MechanicalVerdict,
    build_gates,
    tier_a_verdict,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import (
    GAP_CONTRACT_VERSION,
    ExpectationGapState,
)

D4_1_ANALYSES = Path("data/runtime/strategy_h_v2/d4_1/analyses")
HAS_D4_1 = pytest.mark.skipif(not D4_1_ANALYSES.exists(), reason="D4.1 artifacts not present")


# --- the sample is not re-drawn ------------------------------------------------------------------

def test_tier_a_runs_the_same_three_issuers_d4_1_ran():
    assert TIER_A_TICKERS_UNCHANGED == TIER_A_TICKERS == ("SCCO", "GOOG", "BSY")


def test_tier_b_sample_is_untouched():
    assert tuple(e.ticker for e in TIER_B_SAMPLE) == (
        "IDCC", "DORM", "FRPT", "TG", "CRK", "SPSC")


# --- what V2 did and did not change ---------------------------------------------------------------

def test_the_gap_contract_version_did_not_move():
    """C1, C4, the six states and every confidence ceiling are V1's. If this line ever needs
    changing, the change is not an execution-layer repair."""
    assert GAP_CONTRACT_VERSION == GAP_CONTRACT_VERSION_UNCHANGED == "h_v2_d4_gap_contract_v1"
    assert D4_CONTRACT_VERSION != D4_CONTRACT_VERSION_V1


def test_the_six_gap_states_are_unchanged():
    assert [s.value for s in ExpectationGapState] == [
        "WIDE_POSITIVE", "POSITIVE", "NEUTRAL", "NEGATIVE", "WIDE_NEGATIVE", "UNKNOWN"]


def test_the_e_gates_and_their_thresholds_are_unchanged():
    assert E1_MIN_SCHEMA_VALID_RATE == 0.95
    assert E3_MAX_FABRICATED_CONSENSUS == 0


def test_the_overall_hard_cap_is_the_same_authorization_not_a_new_one():
    assert OVERALL_HARD_CAP_USD == 30.00
    assert D4_1_ALREADY_SPENT_USD == pytest.approx(4.541394)


# --- the mechanical gates -------------------------------------------------------------------------

def test_twelve_gates_in_frozen_order():
    assert M_GATE_IDS == tuple(f"M{n}" for n in range(1, 13))
    assert len({g[0] for g in M_GATES}) == 12


def test_every_core_gate_is_one_of_the_twelve():
    assert set(M_CORE_GATES) <= set(M_GATE_IDS)
    assert {"M3", "M5", "M12"} <= set(M_CORE_GATES), (
        "the two D4.1 defects and the reproducibility gate are not downgradable to limitations")


def test_ready_requires_all_twelve():
    passing = build_gates({g: (True, "ok") for g in M_GATE_IDS})
    assert tier_a_verdict(passing) == MechanicalVerdict.MECHANICAL_READY
    for gate in M_GATE_IDS:
        mixed = build_gates({g: (g != gate, "ok") for g in M_GATE_IDS})
        assert tier_a_verdict(mixed) == MechanicalVerdict.MECHANICAL_NOT_READY


def test_the_verdict_vocabulary_carries_no_quality_claim():
    assert {v.value for v in MechanicalVerdict} == {"MECHANICAL READY", "MECHANICAL NOT READY"}


# --- the replay is read-only -----------------------------------------------------------------------

def _digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.json"))}


@HAS_D4_1
def test_the_v2_replay_does_not_modify_a_single_d4_1_artifact(tmp_path):
    from app.dev.replay_d4_1_under_v2 import replay_all

    before = _digest(D4_1_ANALYSES)
    replay_all(out_root=tmp_path)
    assert _digest(D4_1_ANALYSES) == before


@HAS_D4_1
def test_the_replay_is_labelled_as_a_counterfactual_and_costs_nothing(tmp_path):
    from app.dev.replay_d4_1_under_v2 import replay_all

    report = replay_all(out_root=tmp_path)
    assert report["schema"] == "H_V2_D4_2_V2_COUNTERFACTUAL_REPLAY_V1"
    assert report["live_calls"] == 0
    assert report["cost_usd"] == 0.0
    written = sorted(tmp_path.glob("*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["responses_replayed"] == report["responses_replayed"]


@HAS_D4_1
def test_the_impossible_consensus_failures_are_gone_and_the_direction_ones_are_not(tmp_path):
    """Brief §23's expectation, checked rather than asserted.

    The consensus rejections disappear: those responses stated honestly that consensus evidence
    was unavailable, which V1 punished and V2 allows. The direction rejections SURVIVE, and must -
    those bytes were produced against the V1 prompt, whose schema listed no members, so the model
    was answering a different question. Reading their survival as a V2 failure, or their
    disappearance as a V2 success, would both be wrong.
    """
    from app.dev.replay_d4_1_under_v2 import replay_all

    report = replay_all(out_root=tmp_path)
    assert report["v1_impossible_consensus_failures"] > 0
    assert report["still_failing_consensus_under_v2"] == 0
    assert report["v1_direction_failures"] > 0
