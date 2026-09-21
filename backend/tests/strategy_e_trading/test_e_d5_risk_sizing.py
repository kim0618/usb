from dataclasses import replace
from datetime import date
from fractions import Fraction
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app.risk.config import RiskConfig
from app.strategy_e.costs import COST_RULES_CANONICAL_SHA256
from app.strategy_e.execution import (
    EXECUTION_RULES_CANONICAL_SHA256,
    EXECUTION_VERSION,
    NOT_SELECTED_CAPACITY,
)
from app.strategy_e.exits import (
    EXIT_MODEL,
    EXIT_RULES_CANONICAL_SHA256,
    EXIT_VERSION,
    HORIZON_SEMANTICS_CANONICAL_SHA256,
    VALID_EXIT,
    ExitBatch,
    ExitRecord,
)
from app.strategy_e.risk import (
    INVALID_FOR_STANDARD_PNL,
    MAX_POSITIONS,
    RISK_RULES_CANONICAL_SHA256,
    RISK_RULES_PATH,
    SIZING_MODEL,
    UNRESOLVED_EXIT,
    RiskContractError,
    build_sizing_records,
)
from app.strategy_e.signal import SIGNAL_VERSION, STRATEGY_ID, TRADING_RULES_CANONICAL_SHA256


def _record(symbol: str, *, entry_status: str = "EXECUTED_PROXY",
            exit_valid: bool = True) -> ExitRecord:
    return ExitRecord(
        strategy_id=STRATEGY_ID, signal_version=SIGNAL_VERSION,
        execution_version=EXECUTION_VERSION, exit_version=EXIT_VERSION,
        session_date=date(2025, 1, 2), symbol=symbol,
        signal_digest="signal-digest", signal_source_digest="source-digest",
        execution_digest="execution-digest", entry_model="FIRST_REGULAR_MINUTE_OPEN_PROXY_V1",
        entry_timestamp_et="09:30" if entry_status == "EXECUTED_PROXY" else None,
        entry_price=100.0 if entry_status == "EXECUTED_PROXY" else None,
        entry_status=entry_status, exit_model=EXIT_MODEL,
        exit_timestamp_et="09:34" if entry_status == "EXECUTED_PROXY" else None,
        exit_price=101.0 if exit_valid else None, exit_valid=exit_valid,
        exit_status=VALID_EXIT if exit_valid else "INVALID_EXIT",
        exit_reason=None if exit_valid else "NO_TRADE_MISSING_EXIT_BAR",
        e_d0_rules_digest=TRADING_RULES_CANONICAL_SHA256,
        e_d2_execution_rules_digest=EXECUTION_RULES_CANONICAL_SHA256,
        e_d3_horizon_semantics_digest=HORIZON_SEMANTICS_CANONICAL_SHA256,
        e_d3_exit_rules_digest=EXIT_RULES_CANONICAL_SHA256,
    )


def _batch(*records: ExitRecord, digest: str = "exit-digest") -> ExitBatch:
    return ExitBatch(tuple(records), digest)


@pytest.mark.parametrize(("symbols", "weight"), (
    (("AAPL",), Fraction(1, 1)),
    (("AAPL", "MSFT"), Fraction(1, 2)),
    (("AAPL", "MSFT", "NVDA"), Fraction(1, 3)),
))
def test_equal_weight_for_one_two_and_three_positions(symbols, weight) -> None:
    result = build_sizing_records(_batch(*(_record(symbol) for symbol in symbols)))
    assert [record.normalized_weight for record in result.records] == [weight] * len(symbols)
    assert result.normalized_daily_gross_exposure == Fraction(1, 1)


def test_maximum_position_count_is_common_risk_three() -> None:
    config = RiskConfig()
    assert MAX_POSITIONS == config.max_new_symbols_per_day == config.max_open_positions == 3
    with pytest.raises(RiskContractError, match="exceed"):
        build_sizing_records(_batch(*(_record(symbol) for symbol in ("A", "B", "C", "D"))))


def test_invalid_candidate_is_zero_and_executable_set_is_renormalized() -> None:
    result = build_sizing_records(_batch(
        _record("AAPL"),
        _record("MSFT", entry_status="NO_TRADE_MISSING_ENTRY_BAR", exit_valid=False),
        _record("NVDA"),
    ))
    by_symbol = {record.symbol: record for record in result.records}
    assert by_symbol["AAPL"].normalized_weight == Fraction(1, 2)
    assert by_symbol["MSFT"].normalized_weight == Fraction(0, 1)
    assert by_symbol["NVDA"].normalized_weight == Fraction(1, 2)
    assert result.normalized_daily_gross_exposure == Fraction(1, 1)


def test_one_executable_is_weight_one() -> None:
    result = build_sizing_records(_batch(
        _record("AAPL", exit_valid=False),
        _record("MSFT", entry_status="NO_TRADE_MISSING_ENTRY_BAR", exit_valid=False),
        _record("NVDA"),
    ))
    assert [record.normalized_weight for record in result.records] == [
        Fraction(0), Fraction(0), Fraction(1)
    ]


def test_no_executable_position_has_zero_exposure() -> None:
    result = build_sizing_records(_batch(_record("AAPL", exit_valid=False)))
    assert result.normalized_daily_gross_exposure == Fraction(0, 1)
    assert result.records[0].normalized_weight == Fraction(0, 1)
    assert result.records[0].risk_status == UNRESOLVED_EXIT
    assert result.records[0].skip_reason == INVALID_FOR_STANDARD_PNL


def test_candidate_four_is_not_promoted() -> None:
    result = build_sizing_records(_batch(
        _record("AAPL", exit_valid=False), _record("MSFT"), _record("NVDA"),
        _record("TSLA", entry_status=NOT_SELECTED_CAPACITY, exit_valid=False),
    ))
    by_symbol = {record.symbol: record for record in result.records}
    assert by_symbol["TSLA"].selected is False
    assert by_symbol["TSLA"].executable is False
    assert by_symbol["TSLA"].normalized_weight == 0
    assert by_symbol["MSFT"].normalized_weight == by_symbol["NVDA"].normalized_weight == Fraction(1, 2)


def test_ordering_independence_and_repeat_determinism() -> None:
    one = build_sizing_records(_batch(_record("NVDA"), _record("AAPL"), _record("MSFT")))
    two = build_sizing_records(_batch(_record("MSFT"), _record("NVDA"), _record("AAPL")))
    three = build_sizing_records(_batch(_record("NVDA"), _record("AAPL"), _record("MSFT")))
    assert one == two == three
    assert [record.symbol for record in one.records] == ["AAPL", "MSFT", "NVDA"]


def test_signal_strength_cannot_affect_sizing() -> None:
    fields = set(inspect.signature(build_sizing_records).parameters)
    assert fields == {"exits", "rules_path"}
    source = inspect.getsource(build_sizing_records)
    assert "alpha" not in source.lower() and "strength" not in source.lower()


def test_no_stop_target_partial_exit_or_volatility_gate() -> None:
    rules = json.loads(RISK_RULES_PATH.read_text(encoding="utf-8"))
    assert rules["exit_risk"]["intratrade_stop"] == "NONE"
    assert rules["exit_risk"]["profit_target"] == "NONE"
    assert rules["exit_risk"]["partial_exit"] == "NONE"
    assert rules["concentration_and_volatility"]["additional_opening_volatility_gate"] == "NONE"
    assert rules["concentration_and_volatility"]["post_0925_risk_feature"] == "FORBIDDEN"


def test_sector_and_real_money_limits_are_not_invented() -> None:
    rules = json.loads(RISK_RULES_PATH.read_text(encoding="utf-8"))
    block = rules["concentration_and_volatility"]
    assert block["sector_metadata"] == "NOT_RELIABLY_IDENTIFIABLE"
    assert block["sector_control"] == "NOT_ENFORCED"
    assert all(value == "NOT_SET" for key, value in rules["capacity"].items()
               if key in {"real_account_capital", "broker_buying_power", "live_leverage",
                          "real_dollar_position_size"})


def test_rules_checksum_and_complete_upstream_chain() -> None:
    payload = json.loads(RISK_RULES_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == RISK_RULES_CANONICAL_SHA256
    assert payload["upstream"] == {
        "e_d0_rules_canonical_sha256": TRADING_RULES_CANONICAL_SHA256,
        "e_d1_signal_version": SIGNAL_VERSION,
        "e_d2_execution_rules_canonical_sha256": EXECUTION_RULES_CANONICAL_SHA256,
        "e_d2_execution_version": EXECUTION_VERSION,
        "e_d3_horizon_semantics_canonical_sha256": HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": EXIT_RULES_CANONICAL_SHA256,
        "e_d3_exit_version": EXIT_VERSION,
        "e_d4_cost_rules_canonical_sha256": COST_RULES_CANONICAL_SHA256,
        "common_risk_version": RiskConfig().version,
    }


def test_no_historical_pnl_or_portfolio_return_engine() -> None:
    import app.strategy_e.risk as risk
    source = inspect.getsource(risk)
    assert "gross_return" not in source and "net_return" not in source
    assert "equity_curve" not in source and "apply_cost" not in source
    assert not hasattr(risk, "run") and not hasattr(risk, "run_backtest")
    assert SIZING_MODEL == "EQUAL_WEIGHT_EXECUTABLE_V1"


def test_frozen_e_d4_and_earlier_files_are_unchanged() -> None:
    root = Path(__file__).resolve().parents[3] / "docs/backtest/strategy_e_candidate"
    expected = {
        "strategy_e_trading_rules_v1.json": "85bb7f87d8c086ce6312bef833131c90cc1fc5b3b102de4f0d74c91e04bf13be",
        "strategy_e_execution_rules_v1.json": "ca98ab63b780d7c3f6193f85601f7b43d0e83f3bbdf4aac4fcfc2eb9c10d4626",
        "strategy_e_exit_rules_v1.json": "8a086eb58c85d1ee84ed25beaf624bbf0682222774e1d7f5365c1689ac271c84",
        "strategy_e_cost_rules_v1.json": "af6f9e6ebdd636de77b5fd9d8b426cae875a012e23de81098b4ea1bc0cec51bb",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
