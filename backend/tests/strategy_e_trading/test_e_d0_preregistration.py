"""E-D0 artifact identity checks; this is not a Strategy E signal implementation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_e1_forward.seal import H5_INPUTS
from app.backtest.strategy_e1_h5_confirm.run import RUNS_DIR
from app.backtest.strategy_e1_premarket.evaluate import mask
from app.backtest.strategy_e1_premarket.premarket import DECISION_LAST_BAR


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs" / "backtest" / "strategy_e_candidate"
TRADING_PATH = DOCS / "strategy_e_trading_rules_v1.json"
TRADING_SHA_PATH = DOCS / "strategy_e_trading_rules_v1.sha256"
E1_PATH = DOCS / "e1_premarket_rules_v1.json"
H5_PATH = DOCS / "e1_h5_confirmation_rules_v1.json"

H5_STATEMENT = (
    "premarket_gap > 0 and premarket_rvol >= 3.0 and "
    "position_in_premarket_range >= 0.8 and return_0900_0925 > 0"
)
H5_FIELDS = (
    "premarket_gap",
    "premarket_rvol",
    "position_in_premarket_range",
    "return_0900_0925",
)
H5_PREDICATES = [
    {"feature": "premarket_gap", "operator": ">", "value": 0.0},
    {"feature": "premarket_rvol", "operator": ">=", "value": 3.0},
    {"feature": "position_in_premarket_range", "operator": ">=", "value": 0.8},
    {"feature": "return_0900_0925", "operator": ">", "value": 0.0},
]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _canonical_digest(path: Path) -> str:
    return hashlib.sha256(_canonical(_read(path))).hexdigest()


def test_research_and_trading_h5_are_identical() -> None:
    trading = _read(TRADING_PATH)
    e1 = _read(E1_PATH)
    confirmation = _read(H5_PATH)

    assert e1["hypotheses"]["H5"]["rule"] == H5_STATEMENT
    assert confirmation["hypothesis"]["statement"] == H5_STATEMENT
    assert trading["alpha"]["statement"] == H5_STATEMENT
    assert trading["alpha"]["predicates"] == H5_PREDICATES
    assert tuple(trading["alpha"]["allowed_fields"]) == H5_FIELDS == H5_INPUTS
    assert trading["alpha"]["additional_alpha_fields_forbidden"] is True


def test_actual_h5_mask_uses_the_frozen_boundaries_and_fails_closed() -> None:
    features = {
        "premarket_gap": np.array([0.01, 0.0, 0.01, 0.01, 0.01, np.nan]),
        "premarket_rvol": np.array([3.0, 3.0, 2.999, 3.0, 3.0, 3.0]),
        "position_in_premarket_range": np.array([0.8, 0.8, 0.8, 0.799, 0.8, 0.8]),
        "return_0900_0925": np.array([0.001, 0.001, 0.001, 0.001, 0.0, 0.001]),
        "return_last30m": np.zeros(6),
        "relative_strength_vs_spy": np.zeros(6),
        "premarket_dollar_volume": np.zeros(6),
    }
    assert mask("H5", features).tolist() == [True, False, False, False, False, False]


def test_decision_cutoff_identity() -> None:
    trading = _read(TRADING_PATH)
    e1 = _read(E1_PATH)
    confirmation = _read(H5_PATH)

    assert trading["decision"]["signal_cutoff_et"] == "09:25"
    assert e1["decision_time"]["primary_et"] == "09:25"
    assert confirmation["decision_time"]["primary_et"] == "09:25"
    assert trading["decision"]["last_usable_bar_start_et"] == "09:24"
    assert DECISION_LAST_BAR == 9 * 60 + 24


def test_frozen_research_rule_checksums() -> None:
    trading = _read(TRADING_PATH)
    expected = trading["integrity"]["research_rules"]

    assert _canonical_digest(E1_PATH) == expected["e1_premarket_canonical_sha256"]
    assert _canonical_digest(H5_PATH) == expected["e1_h5_confirmation_canonical_sha256"]
    assert trading["alpha"]["research_rules_canonical_sha256"] == _canonical_digest(H5_PATH)


def test_trading_preregistration_checksum_and_serialization_are_deterministic() -> None:
    payload = _read(TRADING_PATH)
    checksum = _read(TRADING_SHA_PATH)
    first = _canonical(payload)
    second = _canonical(json.loads(first.decode("utf-8")))

    assert first == second
    assert hashlib.sha256(first).hexdigest() == checksum["canonical_sha256"]
    assert hashlib.sha256(TRADING_PATH.read_bytes()).hexdigest() == checksum["file_sha256"]


def test_contract_keeps_execution_and_risk_out_of_alpha() -> None:
    trading = _read(TRADING_PATH)
    alpha_text = json.dumps(trading["alpha"], sort_keys=True)
    forbidden = ("price", "liquidity", "spread", "slippage", "sector", "volatility", "rank")

    assert set(predicate["feature"] for predicate in trading["alpha"]["predicates"]) == set(H5_FIELDS)
    assert not any(field in alpha_text.lower() for field in forbidden)
    assert trading["universe"]["execution_or_risk_exclusions_are_not_alpha"] is True


def test_missing_historical_payload_is_a_declared_runtime_only_limitation() -> None:
    trading = _read(TRADING_PATH)
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "data/runtime/*" in ignore_rules
    assert str(RUNS_DIR) == "data/runtime/strategy_e_candidate/confirmation_runs"
    assert trading["integrity"]["historical_confirmation_run_id"] == "e1h5-d57b212da84c"
    assert trading["integrity"]["historical_confirmation_artifact_status"] == (
        "RUNTIME_ONLY_GITIGNORED_NOT_PRESENT_IN_CURRENT_WORKSPACE"
    )
    assert "does not modify or re-estimate" in (
        trading["integrity"]["historical_confirmation_limitation"]
    )


def test_document_and_json_share_core_contract_terms() -> None:
    document = (DOCS / "E_D0_TRADING_PREREGISTRATION_V1.md").read_text(encoding="utf-8")
    trading = _read(TRADING_PATH)

    for term in (
        trading["strategy"]["id"],
        trading["strategy"]["name"],
        H5_STATEMENT,
        "09:25 ET",
        "E0_OFFICIAL_OPEN_PROXY",
        "Open + 5 minutes",
        "TBD — MUST BE FROZEN BEFORE E-D2 BACKTEST",
        "Trading Backtest = NOT YET",
    ):
        assert term in document
