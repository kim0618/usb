from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app.strategy_e.costs import (
    COST_MODEL,
    COST_RULES_CANONICAL_SHA256,
    COST_RULES_PATH,
    CostContractError,
    CostScenario,
    apply_cost,
)
from app.strategy_e.execution import EXECUTION_RULES_CANONICAL_SHA256, EXECUTION_VERSION
from app.strategy_e.exits import (
    EXIT_MODEL,
    EXIT_RULES_CANONICAL_SHA256,
    EXIT_VERSION,
    HORIZON_SEMANTICS_CANONICAL_SHA256,
    VALID_EXIT,
    ExitRecord,
)
from app.strategy_e.signal import SIGNAL_VERSION, STRATEGY_ID, TRADING_RULES_CANONICAL_SHA256


def _exit(**changes) -> ExitRecord:
    record = ExitRecord(
        strategy_id=STRATEGY_ID,
        signal_version=SIGNAL_VERSION,
        execution_version=EXECUTION_VERSION,
        exit_version=EXIT_VERSION,
        session_date=date(2025, 1, 2),
        symbol="AAPL",
        signal_digest="signal-digest",
        signal_source_digest="source-digest",
        execution_digest="execution-digest",
        entry_model="FIRST_REGULAR_MINUTE_OPEN_PROXY_V1",
        entry_timestamp_et="09:30",
        entry_price=100.0,
        entry_status="EXECUTED_PROXY",
        exit_model=EXIT_MODEL,
        exit_timestamp_et="09:34",
        exit_price=100.2,
        exit_valid=True,
        exit_status=VALID_EXIT,
        exit_reason=None,
        e_d0_rules_digest=TRADING_RULES_CANONICAL_SHA256,
        e_d2_execution_rules_digest=EXECUTION_RULES_CANONICAL_SHA256,
        e_d3_horizon_semantics_digest=HORIZON_SEMANTICS_CANONICAL_SHA256,
        e_d3_exit_rules_digest=EXIT_RULES_CANONICAL_SHA256,
    )
    return replace(record, **changes)


@pytest.mark.parametrize(("scenario", "bp", "cost", "net"), (
    (CostScenario.COST_05BP, "5", "0.0005", "0.0015"),
    (CostScenario.COST_10BP, "10", "0.001", "0.001"),
    (CostScenario.COST_15BP, "15", "0.0015", "0.0005"),
    (CostScenario.COST_20BP, "20", "0.002", "0.000"),
))
def test_frozen_scenario_mapping_and_net_identity(scenario, bp, cost, net) -> None:
    result = apply_cost(_exit(), "exit-digest", scenario)
    assert result.cost_scenario == scenario.value
    assert result.round_trip_cost_bp == Decimal(bp)
    assert result.round_trip_cost_decimal == Decimal(cost)
    assert result.gross_return == Decimal("0.002")
    assert result.net_return == Decimal(net)


def test_cost_is_one_total_round_trip_deduction_not_twice_per_side() -> None:
    result = apply_cost(_exit(), "exit-digest", CostScenario.COST_10BP)
    assert result.net_return == result.gross_return - Decimal("0.001")
    assert result.net_return != result.gross_return - Decimal("0.002")


def test_cost_direction_is_always_adverse_for_long_trade() -> None:
    results = [apply_cost(_exit(), "exit-digest", scenario) for scenario in CostScenario]
    assert all(result.net_return <= result.gross_return for result in results)
    assert [result.net_return for result in results] == sorted(
        (result.net_return for result in results), reverse=True
    )


@pytest.mark.parametrize("changes", (
    {"entry_price": None}, {"entry_price": 0.0}, {"entry_price": -1.0},
    {"entry_price": float("nan")},
))
def test_invalid_entry_rejected(changes) -> None:
    with pytest.raises(CostContractError, match="entry_price"):
        apply_cost(_exit(**changes), "exit-digest", CostScenario.COST_05BP)


@pytest.mark.parametrize("changes", (
    {"exit_valid": False, "exit_status": "INVALID_EXIT"},
    {"exit_price": None}, {"exit_price": 0.0}, {"exit_price": float("nan")},
))
def test_invalid_exit_rejected(changes) -> None:
    with pytest.raises(CostContractError):
        apply_cost(_exit(**changes), "exit-digest", CostScenario.COST_05BP)


@pytest.mark.parametrize("scenario", ("COST_00BP", "COST_25BP", -5, None))
def test_unsupported_or_negative_scenario_rejected(scenario) -> None:
    with pytest.raises(CostContractError, match="unsupported.*scenario"):
        apply_cost(_exit(), "exit-digest", scenario)


def test_repeated_run_is_deterministic() -> None:
    first = apply_cost(_exit(), "exit-digest", "COST_15BP")
    second = apply_cost(_exit(), "exit-digest", "COST_15BP")
    assert first == second
    assert first.cost_digest == second.cost_digest


def test_scenario_and_exit_identity_change_digest() -> None:
    base = apply_cost(_exit(), "exit-digest", "COST_05BP")
    assert apply_cost(_exit(), "exit-digest", "COST_10BP").cost_digest != base.cost_digest
    assert apply_cost(_exit(), "other-exit", "COST_05BP").cost_digest != base.cost_digest


def test_rules_checksum_and_upstream_chain() -> None:
    payload = json.loads(COST_RULES_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == COST_RULES_CANONICAL_SHA256
    assert payload["upstream"] == {
        "e_d0_rules_canonical_sha256": TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": EXECUTION_RULES_CANONICAL_SHA256,
        "e_d2_execution_version": EXECUTION_VERSION,
        "e_d3_horizon_semantics_canonical_sha256": HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": EXIT_RULES_CANONICAL_SHA256,
        "e_d3_exit_version": EXIT_VERSION,
    }


def test_no_quotes_price_or_liquidity_dependent_cost_is_invented() -> None:
    rules = json.loads(COST_RULES_PATH.read_text(encoding="utf-8"))
    assert rules["data_capability"]["bid"] == "NOT_AVAILABLE"
    assert rules["data_capability"]["ask"] == "NOT_AVAILABLE"
    assert rules["data_capability"]["spread"] == "NOT_IDENTIFIABLE"
    assert rules["cost_model"]["price_dependent_cost"] is False
    assert rules["cost_model"]["liquidity_dependent_cost"] is False


def test_engine_is_a_single_record_primitive_not_a_historical_runner() -> None:
    import app.strategy_e.costs as costs
    source = inspect.getsource(costs)
    assert "pandas" not in source and "numpy" not in source
    assert "Sequence" not in source and "ExitBatch" not in source
    assert not hasattr(costs, "run") and not hasattr(costs, "run_backtest")
    assert COST_MODEL == "TOTAL_ROUND_TRIP_SUBTRACTION_V1"


def test_frozen_upstream_files_are_unchanged() -> None:
    root = Path(__file__).resolve().parents[3]
    expected = {
        "strategy_e_trading_rules_v1.json": "85bb7f87d8c086ce6312bef833131c90cc1fc5b3b102de4f0d74c91e04bf13be",
        "strategy_e_execution_rules_v1.json": "ca98ab63b780d7c3f6193f85601f7b43d0e83f3bbdf4aac4fcfc2eb9c10d4626",
        "strategy_e_exit_rules_v1.json": "8a086eb58c85d1ee84ed25beaf624bbf0682222774e1d7f5365c1689ac271c84",
    }
    directory = root / "docs/backtest/strategy_e_candidate"
    for name, digest in expected.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest
