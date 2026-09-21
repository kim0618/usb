"""E-D3 exact exit identity only. No return, PnL, cost, or horizon comparison."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.strategy_e import exits as exits_module
from app.strategy_e.execution import EntryBar, ExecutionBatch, build_entry_records
from app.strategy_e.exits import (
    DUPLICATE_EXIT_BAR,
    EXIT_MODEL,
    EXIT_RULES_CANONICAL_SHA256,
    HORIZON_SEMANTICS_CANONICAL_SHA256,
    INVALID_EXIT_CLOSE,
    MISSING_EXIT_BAR,
    NO_EXIT_NO_VALID_ENTRY,
    SESSION_MISMATCH,
    SHORTENED_SESSION,
    SYMBOL_MISMATCH,
    ExitContractError,
    resolve_exit_batch,
)
from app.strategy_e.signal import SignalFrame, evaluate_h5_signal


SESSION = date(2026, 9, 21)
ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/backtest/strategy_e_candidate"


def _features(count: int) -> dict[str, np.ndarray]:
    return {
        "premarket_gap": np.full(count, 0.01),
        "premarket_rvol": np.full(count, 3.0),
        "position_in_premarket_range": np.full(count, 0.8),
        "return_0900_0925": np.full(count, 0.001),
        "return_last30m": np.zeros(count),
        "relative_strength_vs_spy": np.zeros(count),
        "premarket_dollar_volume": np.full(count, 100_000.0),
        "previous_day_dollar_volume": np.full(count, 10_000_000.0),
        "close_price": np.full(count, 20.0),
        "spy_premarket_return": np.zeros(count),
        "pm_bars": np.full(count, 3.0),
    }


def _bar(symbol: str = "AAPL", *, session: date = SESSION, minute: str = "09:34",
         open_: float = 100.0, high: float = 110.0, low: float = 90.0,
         close: float = 105.0, volume: float = 1_000.0, vwap: float = 101.0) -> EntryBar:
    return EntryBar(symbol, session, minute, open_, high, low, close, volume, vwap)


def _entry_batch(symbols: tuple[str, ...] = ("AAPL",)) -> ExecutionBatch:
    signal = evaluate_h5_signal(
        SignalFrame(SESSION, symbols, _features(len(symbols)), source_digest="source-v1")
    )
    entry_bars = [_bar(symbol, minute="09:30", open_=100.0) for symbol in symbols]
    return build_entry_records(signal, entry_bars)


def _resolve(bars=(_bar(),), entries: ExecutionBatch | None = None):
    batch = entries or _entry_batch()
    return resolve_exit_batch(batch, {"AAPL": bars})


def test_exact_0934_close_mapping_and_one_bar_requirement() -> None:
    record = _resolve((_bar(close=123.45),)).records[0]

    assert record.exit_valid
    assert record.exit_model == EXIT_MODEL
    assert record.exit_timestamp_et == "09:34"
    assert record.exit_price == 123.45


def test_missing_0934_fails_closed_and_never_uses_0933_or_0935() -> None:
    record = _resolve((_bar(minute="09:33", close=77.0),
                       _bar(minute="09:35", open_=88.0, close=99.0))).records[0]

    assert not record.exit_valid and record.exit_price is None
    assert record.exit_reason == MISSING_EXIT_BAR


def test_duplicate_0934_fails_closed() -> None:
    record = _resolve((_bar(close=101.0), _bar(close=102.0))).records[0]

    assert not record.exit_valid and record.exit_reason == DUPLICATE_EXIT_BAR


@pytest.mark.parametrize("close", [float("nan"), 0.0, -1.0])
def test_invalid_exit_close_fails_closed(close: float) -> None:
    record = _resolve((_bar(close=close),)).records[0]

    assert not record.exit_valid and record.exit_price is None
    assert record.exit_reason == INVALID_EXIT_CLOSE


def test_session_mismatch_is_rejected() -> None:
    record = _resolve((_bar(session=date(2026, 9, 22)),)).records[0]

    assert not record.exit_valid and record.exit_reason == SESSION_MISMATCH


def test_symbol_mismatch_is_rejected() -> None:
    record = _resolve((_bar(symbol="MSFT"),)).records[0]

    assert not record.exit_valid and record.exit_reason == SYMBOL_MISMATCH


@pytest.mark.parametrize("selected,eligible", [(True, False), (False, False)])
def test_invalid_or_skipped_entry_produces_no_exit(selected: bool, eligible: bool) -> None:
    batch = _entry_batch()
    bad = replace(batch.records[0], selected=selected, execution_eligible=eligible,
                  entry_price=None, entry_timestamp_et=None, skip_reason="UPSTREAM_SKIP")
    changed = ExecutionBatch((bad,), batch.execution_digest)
    record = _resolve((_bar(close=999.0),), changed).records[0]

    assert not record.exit_valid and record.exit_price is None
    assert record.exit_timestamp_et is None
    assert record.exit_reason == NO_EXIT_NO_VALID_ENTRY


def test_row_order_and_repeated_run_are_deterministic() -> None:
    entries = _entry_batch(("MSFT", "AAPL"))
    bars = {"MSFT": (_bar("MSFT", close=202.0),), "AAPL": (_bar(close=101.0),)}
    first = resolve_exit_batch(entries, bars)
    reordered_entries = ExecutionBatch(tuple(reversed(entries.records)), entries.execution_digest)
    reordered = resolve_exit_batch(
        reordered_entries,
        {"AAPL": tuple(reversed(bars["AAPL"])), "MSFT": bars["MSFT"]},
    )

    assert first == resolve_exit_batch(entries, bars) == reordered
    assert tuple(record.symbol for record in first.records) == ("AAPL", "MSFT")


def test_post_0934_data_and_unused_0934_fields_are_independent() -> None:
    baseline = _resolve((_bar(close=105.0),))
    changed = _resolve((
        _bar(close=105.0, high=9999.0, low=0.01, volume=999999.0, vwap=7777.0),
        _bar(minute="09:35", open_=1.0, high=2.0, low=0.5, close=1.5),
        _bar(minute="15:59", close=99999.0),
    ))

    assert baseline == changed


def test_exit_layer_does_not_mutate_entry_record_or_h5_identity() -> None:
    entries = _entry_batch()
    before = asdict(entries.records[0])
    result = _resolve(entries=entries)

    assert asdict(entries.records[0]) == before
    assert result.records[0].signal_digest == entries.records[0].signal_digest
    assert result.records[0].entry_price == entries.records[0].entry_price
    assert result.records[0].entry_timestamp_et == entries.records[0].entry_timestamp_et


def test_shortened_session_executed_entry_is_defensively_rejected() -> None:
    entries = _entry_batch()
    malformed = replace(entries.records[0], session_date=date(2024, 11, 29))
    batch = ExecutionBatch((malformed,), entries.execution_digest)
    record = resolve_exit_batch(batch, {"AAPL": (_bar(session=date(2024, 11, 29)),)}).records[0]

    assert not record.exit_valid and record.exit_reason == SHORTENED_SESSION


def test_exit_rules_checksum_and_frozen_chain() -> None:
    path = DOCS / "strategy_e_exit_rules_v1.json"
    sha = json.loads((DOCS / "strategy_e_exit_rules_v1.sha256").read_text())
    payload = json.loads(path.read_text())
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode()

    assert hashlib.sha256(canonical).hexdigest() == EXIT_RULES_CANONICAL_SHA256
    assert sha["canonical_sha256"] == EXIT_RULES_CANONICAL_SHA256
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha["file_sha256"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("strategy_e_trading_rules_v1", "f1534f07688c801f2979491e447eefbb3e62b8045afb9c4ab302b90e4593d4b2"),
        ("strategy_e_execution_rules_v1", "d204b1dac9dd40530a7e03bebd36b32fd1950efde2e8b226a5eaedd5f196d041"),
        ("strategy_e_horizon_semantics_v1", HORIZON_SEMANTICS_CANONICAL_SHA256),
    ],
)
def test_upstream_frozen_checksums_are_unchanged(name: str, expected: str) -> None:
    payload = json.loads((DOCS / f"{name}.json").read_text())
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode()

    assert hashlib.sha256(canonical).hexdigest() == expected


def test_research_artifact_is_unchanged_and_not_imported_by_resolver() -> None:
    semantics = json.loads((DOCS / "strategy_e_horizon_semantics_v1.json").read_text())
    research_path = ROOT / semantics["research_implementation"]["path"]
    source = (ROOT / "backend/app/strategy_e/exits.py").read_text()

    assert hashlib.sha256(research_path.read_bytes()).hexdigest() == (
        semantics["research_implementation"]["file_sha256_at_resolution"]
    )
    assert "strategy_e1_premarket" not in source
    assert "R_5m" not in source


def test_rules_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "changed.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ExitContractError, match="rules digest mismatch"):
        resolve_exit_batch(_entry_batch(), {"AAPL": (_bar(),)}, rules_path=path)


def test_unsupported_exit_version_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(exits_module.EXIT_RULES_PATH.read_text())
    payload["declaration"]["version"] = "STRATEGY_E_EXIT_V2"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode()
    ).hexdigest()
    monkeypatch.setattr(exits_module, "EXIT_RULES_CANONICAL_SHA256", digest)

    with pytest.raises(ExitContractError, match="unsupported.*version"):
        resolve_exit_batch(_entry_batch(), {"AAPL": (_bar(),)}, rules_path=path)
