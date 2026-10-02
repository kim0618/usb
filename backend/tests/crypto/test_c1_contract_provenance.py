"""Every frozen number in `c1.contract` against the artefact it was copied from.

The signal engine hard-codes the volatility cutoff, the taker rate and the microstructure cost so
that the deployed snapshot does not have to carry the research data. That is only safe while
these assertions hold: if a research artefact is ever re-run and a figure moves, this file fails
and the operational contract has to be re-stated on purpose rather than drifting.

Skipped where the artefact is absent, because the server snapshot does not ship it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.crypto.c1 import contract as K

REPO = Path(__file__).resolve().parents[3]
CELLS = REPO / "data/runtime/crypto/d5_2/cells_v1.json"
FEES = REPO / "data/runtime/crypto/reference/fee_source_verification_v1.json"
MICRO = REPO / "data/runtime/crypto/d5/realtime_cost_measurement_v1.json"
FREEZE = REPO / "data/runtime/crypto/d5_2/contract_freeze_v1.json"


def _load(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f"research artefact not present: {path}")
    return json.loads(path.read_text())


def test_contract_document_hash_is_the_frozen_one() -> None:
    document = REPO / K.CONTRACT_DOC
    if not document.exists():
        pytest.skip("contract document not present")
    assert hashlib.sha256(document.read_bytes()).hexdigest() == K.CONTRACT_SHA256
    assert _load(FREEZE)["sha256"] == K.CONTRACT_SHA256


def test_volatility_cutoffs_are_the_f1_train_pair() -> None:
    low, high = _load(CELLS)["vol_cutoffs_f1_train"]
    assert low == K.VOL_CUTOFF_LO_F1_TRAIN
    assert high == K.VOL_CUTOFF_HI_F1_TRAIN
    assert K.VOL_REGIME_HIGH_CUTOFF == high


def test_taker_and_microstructure_match_the_cost_sources() -> None:
    cells = _load(CELLS)
    assert cells["taker"] == K.TAKER_RATE
    assert cells["micro"]["BASE"] == K.MICRO_BASE_FRAC
    assert cells["micro"]["STRESS"] == K.MICRO_STRESS_FRAC
    assert float(_load(FEES)["adopted"]["taker_rate"]) == K.TAKER_RATE
    micro = _load(MICRO)
    impact = micro["impact"]["0.1"]
    assert (micro["spread_frac"]["p50"] + impact["buy_frac"]["p50"]
            + impact["sell_frac"]["p50"]) == K.MICRO_BASE_FRAC


def test_the_published_cell_figures_match_the_frozen_run() -> None:
    """The numbers the screen quotes at the operator are the study's, to the digit."""
    cell = _load(CELLS)["cells"][K.RESEARCH_CELL]
    oos = cell["oos"]
    published = K.RESEARCH_OOS
    assert cell["verdict"] == "WEAK"
    assert oos["ZERO"]["mean"] * 10_000 == pytest.approx(published["gross_bp"], abs=1e-9)
    assert oos["VIP0_BASE"]["mean"] * 10_000 == pytest.approx(published["net_vip0_base_bp"], abs=1e-9)
    assert oos["VIP0_STRESS"]["mean"] * 10_000 == pytest.approx(published["net_vip0_stress_bp"], abs=1e-9)
    low, high = oos["VIP0_BASE"]["ci95"]
    assert (low * 10_000, high * 10_000) == pytest.approx(published["net_ci95_bp"], abs=1e-9)
    assert oos["N"] == published["n_bars"]
    assert oos["days"] == published["days"]
    assert oos["N_eff"] == pytest.approx(published["n_eff"])


def test_the_study_found_no_survivor_and_the_contract_says_so() -> None:
    """Nothing downstream may read C1 as a validated strategy. D5.2's gate was CASE C."""
    assert _load(CELLS)["gate"] == {"case": "C", "survive": []}
    assert K.RESEARCH_VERDICT == "WEAK_W1_NOT_SURVIVE"
    assert K.RESEARCH_OOS["gate"] == "CASE_C_NO_SURVIVE"


def test_short_was_evaluated_and_rejected_so_the_contract_is_long_only() -> None:
    """The grid scored both sides. C1's SHORT is the negative of its LONG and lost at every
    horizon, so there is no SHORT definition to implement."""
    cells = _load(CELLS)["cells"]
    for horizon in (15, 30, 60, 120, 240):
        short = cells[f"C1-EVENT-{horizon}m-SHORT"]
        assert short["verdict"] == "REJECT"
        assert short["oos"]["VIP0_BASE"]["mean"] < 0
    assert cells["C1-EVENT-240m-SHORT"]["oos"]["ZERO"]["mean"] == pytest.approx(
        -cells["C1-EVENT-240m-LONG"]["oos"]["ZERO"]["mean"])
    assert K.DIRECTION_CONTRACT == "LONG_ONLY"


def test_the_bucket_and_window_rules_are_the_study_constants() -> None:
    from app.crypto.research import features as RF

    assert K.BUCKET_QUANTILES == RF.BUCKET_QUANTILES
    assert K.NORM_DAYS == RF.NORM_DAYS
    assert K.DAY_BARS == RF.DAY_BARS
    assert K.VOL_WINDOW_BARS == RF.DAY_BARS
    assert K.B1_QUANTILE == RF.BUCKET_QUANTILES[0]
