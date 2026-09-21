"""Semantic resolution only: no return aggregation, comparison, or backtest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_e1_premarket import premarket as research


ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_candidate"
SEMANTICS_PATH = DOCS / "strategy_e_horizon_semantics_v1.json"
SEMANTICS_SHA_PATH = DOCS / "strategy_e_horizon_semantics_v1.sha256"
E_D0_PATH = DOCS / "strategy_e_trading_rules_v1.json"
E_D2_PATH = DOCS / "strategy_e_execution_rules_v1.json"
RESEARCH_PATH = ROOT / "backend/app/backtest/strategy_e1_premarket/premarket.py"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _opening(minutes: list[int], opens: list[float], closes: list[float]) -> dict[str, float]:
    count = len(minutes)
    return research.opening_block(
        np.asarray(minutes),
        np.asarray(opens, dtype=float),
        np.full(count, 200.0),
        np.full(count, 1.0),
        np.asarray(closes, dtype=float),
    )


def test_research_r5m_uses_exact_0934_close_when_present() -> None:
    block = _opening([570, 572, 574], [100.0, 150.0, 175.0], [101.0, 102.0, 105.0])

    assert block["last_5m"] == 105.0
    assert block["strict_5m"] == 105.0
    assert np.isclose(research.labels(block)["R_5m"], 0.05)


def test_research_r5m_uses_last_existing_close_when_0934_is_absent() -> None:
    block = _opening([570, 572], [100.0, 150.0], [101.0, 103.0])

    assert block["last_5m"] == 103.0
    assert np.isclose(research.labels(block)["R_5m"], 0.03)


def test_research_strict_r5m_is_nan_when_0934_is_absent() -> None:
    block = _opening([570, 572], [100.0, 150.0], [101.0, 103.0])

    assert np.isnan(block["strict_5m"])
    assert np.isnan(research.labels(block)["R_5m_strict"])


def test_trading_contract_requires_exact_0934_close_and_forbids_fallback() -> None:
    e_d0 = _read(E_D0_PATH)
    semantics = _read(SEMANTICS_PATH)
    primary = e_d0["exit"]["primary"]
    trading = semantics["trading_exit_model"]

    assert primary["price"] == "close of the 09:34 ET one-minute aggregate bar"
    assert primary["exact_bar_required"] is True
    assert trading["exit_timestamp_et"] == "09:34"
    assert trading["exit_field"] == "close"
    assert trading["exact_0934_required"] is True
    assert trading["fallback_policy"] == "FORBIDDEN"
    assert trading["missing_exact_0934"] == "exit_valid=false"
    assert "last existing bar" in trading["forbidden_substitutes"]


def test_e_d0_frozen_checksum_is_unchanged() -> None:
    assert hashlib.sha256(_canonical(_read(E_D0_PATH))).hexdigest() == (
        "f1534f07688c801f2979491e447eefbb3e62b8045afb9c4ab302b90e4593d4b2"
    )


def test_research_implementation_is_unchanged_at_resolution() -> None:
    semantics = _read(SEMANTICS_PATH)

    assert hashlib.sha256(RESEARCH_PATH.read_bytes()).hexdigest() == (
        semantics["research_implementation"]["file_sha256_at_resolution"]
    )
    assert semantics["research_implementation"]["modified_by_resolution"] is False


def test_e_d2_entry_contract_is_unchanged() -> None:
    e_d2 = _read(E_D2_PATH)
    chain = _read(SEMANTICS_PATH)["frozen_chain"]

    assert e_d2["entry"]["model"] == "FIRST_REGULAR_MINUTE_OPEN_PROXY_V1"
    assert e_d2["entry"]["entry_bar_start_et"] == "09:30"
    assert e_d2["entry"]["entry_field"] == "open"
    assert hashlib.sha256(_canonical(e_d2)).hexdigest() == (
        chain["e_d2_execution_rules_canonical_sha256"]
    )


def test_semantics_artifact_checksum_and_non_retuning_declaration() -> None:
    payload = _read(SEMANTICS_PATH)
    checksum = _read(SEMANTICS_SHA_PATH)

    assert hashlib.sha256(_canonical(payload)).hexdigest() == checksum["canonical_sha256"]
    assert hashlib.sha256(SEMANTICS_PATH.read_bytes()).hexdigest() == checksum["file_sha256"]
    assert payload["relationship"]["alpha_retuning"] is False
    assert payload["relationship"]["performance_claim"] == "NONE"
    assert payload["declaration"]["performance_comparison_performed"] is False
