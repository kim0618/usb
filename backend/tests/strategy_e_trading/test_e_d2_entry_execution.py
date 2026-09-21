"""E-D2 entry identity tests. No label, return, PnL, exit, stop, size or cost is used."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from app.strategy_e import execution as execution_module
from app.strategy_e.execution import (
    DUPLICATE_ENTRY_BAR,
    ENTRY_MODEL,
    EXECUTION_RULES_CANONICAL_SHA256,
    INVALID_ENTRY_OPEN,
    MISSING_ENTRY_BAR,
    NOT_SELECTED_CAPACITY,
    SESSION_MISMATCH,
    SHORTENED_SESSION,
    EntryBar,
    ExecutionContractError,
    build_entry_records,
)
from app.strategy_e.signal import SignalFrame, evaluate_h5_signal


SESSION = date(2026, 9, 21)
SYMBOLS = ("TSLA", "NVDA", "AAPL", "MSFT", "AMD")
ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = ROOT / "docs/backtest/strategy_e_candidate/strategy_e_execution_rules_v1.json"
SHA_PATH = ROOT / "docs/backtest/strategy_e_candidate/strategy_e_execution_rules_v1.sha256"


def _features(order: np.ndarray | None = None, **extra) -> dict[str, np.ndarray]:
    count = len(SYMBOLS)
    values = {
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
    values.update(extra)
    if order is not None:
        values = {name: value[order] for name, value in values.items()}
    return values


def _signal(order: np.ndarray | None = None, **extra_features):
    symbols = SYMBOLS if order is None else tuple(np.asarray(SYMBOLS)[order].tolist())
    return evaluate_h5_signal(
        SignalFrame(
            session=SESSION,
            symbols=symbols,
            features=_features(order, **extra_features),
            source_digest="source-v1",
        )
    )


def _bar(symbol: str, *, session: date = SESSION, minute: str = "09:30",
         open_: float = 100.0, high: float = 110.0, low: float = 90.0,
         close: float = 105.0, volume: float = 1_000.0, vwap: float = 101.0) -> EntryBar:
    return EntryBar(symbol, session, minute, open_, high, low, close, volume, vwap)


def _bars() -> list[EntryBar]:
    return [_bar(symbol, open_=100.0 + index) for index, symbol in enumerate(SYMBOLS)]


def test_h5_candidates_are_preserved_across_selection_and_execution() -> None:
    signal = _signal()
    batch = build_entry_records(signal, _bars())

    assert signal.candidate_count == 5
    assert tuple(record.symbol for record in batch.records) == tuple(sorted(SYMBOLS))
    assert all(record.alpha_candidate for record in batch.records)
    assert [record.selected for record in batch.records] == [True, True, True, False, False]
    assert [record.skip_reason for record in batch.records[3:]] == [
        NOT_SELECTED_CAPACITY,
        NOT_SELECTED_CAPACITY,
    ]


def test_selection_is_symbol_ascending_not_alpha_strength() -> None:
    features = _features(
        premarket_gap=np.array([0.50, 0.40, 0.01, 0.30, 0.02]),
        premarket_rvol=np.array([100.0, 90.0, 3.0, 80.0, 3.1]),
    )
    signal = evaluate_h5_signal(
        SignalFrame(SESSION, SYMBOLS, features, source_digest="source-v1")
    )
    records = build_entry_records(signal, _bars()).records

    assert tuple(record.symbol for record in records if record.selected) == ("AAPL", "AMD", "MSFT")
    assert [record.selection_rank for record in records] == [1, 2, 3, 4, 5]


def test_input_order_and_repeated_runs_do_not_change_identity() -> None:
    first = build_entry_records(_signal(), _bars())
    order = np.array([4, 3, 2, 1, 0])
    reordered = build_entry_records(_signal(order), list(reversed(_bars())))
    repeated = build_entry_records(_signal(), _bars())

    assert first == repeated == reordered


def test_valid_0930_open_is_the_only_entry_price_field() -> None:
    signal = _signal()
    baseline = build_entry_records(signal, _bars())
    changed = [replace(bar, high=9999.0, low=0.01, close=7777.0, volume=999999.0, vwap=5555.0)
               for bar in _bars()]
    changed.extend(_bar(symbol, minute="09:31", open_=8888.0) for symbol in SYMBOLS)
    mutated = build_entry_records(signal, changed)

    assert [record.entry_price for record in baseline.records] == [102.0, 104.0, 103.0, None, None]
    assert baseline == mutated
    assert all(record.entry_model == ENTRY_MODEL for record in baseline.records)


def test_missing_or_delayed_0930_bar_is_no_trade() -> None:
    bars = [bar for bar in _bars() if bar.symbol != "AAPL"]
    bars.append(_bar("AAPL", minute="09:31"))
    record = build_entry_records(_signal(), bars).records[0]

    assert record.symbol == "AAPL"
    assert record.selected and not record.execution_eligible
    assert record.entry_price is None and record.skip_reason == MISSING_ENTRY_BAR


@pytest.mark.parametrize("value", [float("nan"), 0.0, -1.0])
def test_nan_zero_or_negative_open_is_rejected(value: float) -> None:
    bars = [replace(bar, open=value) if bar.symbol == "AAPL" else bar for bar in _bars()]
    record = build_entry_records(_signal(), bars).records[0]

    assert not record.execution_eligible
    assert record.skip_reason == INVALID_ENTRY_OPEN


def test_duplicate_0930_bar_fails_closed() -> None:
    bars = _bars() + [_bar("AAPL", open_=101.0)]
    record = build_entry_records(_signal(), bars).records[0]

    assert not record.execution_eligible
    assert record.skip_reason == DUPLICATE_ENTRY_BAR


def test_session_mismatch_fails_closed() -> None:
    bars = [bar for bar in _bars() if bar.symbol != "AAPL"]
    bars.append(_bar("AAPL", session=date(2026, 9, 22)))
    record = build_entry_records(_signal(), bars).records[0]

    assert not record.execution_eligible
    assert record.skip_reason == SESSION_MISMATCH


def test_shortened_session_is_explicitly_no_trade() -> None:
    signal = replace(_signal(), session=date(2024, 11, 29))
    records = build_entry_records(signal, []).records

    assert all(record.skip_reason == SHORTENED_SESSION for record in records[:3])


def test_execution_fields_cannot_change_h5_mask() -> None:
    plain = _signal()
    extra = _signal(
        entry_open=np.arange(5, dtype=float),
        execution_eligible=np.ones(5),
        selection_rank=np.arange(5),
    )

    assert plain.h5_mask == extra.h5_mask
    assert plain.candidate_symbols == extra.candidate_symbols
    assert plain.decision_digest == extra.decision_digest


def test_execution_provenance_and_digest_are_stable() -> None:
    batch = build_entry_records(_signal(), _bars())

    assert len(batch.execution_digest) == 64
    assert all(record.execution_rules_digest == EXECUTION_RULES_CANONICAL_SHA256
               for record in batch.records)
    assert all(record.signal_digest == _signal().decision_digest for record in batch.records)


def test_execution_rules_checksum_file_is_frozen() -> None:
    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    checksum = json.loads(SHA_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()

    assert hashlib.sha256(canonical).hexdigest() == EXECUTION_RULES_CANONICAL_SHA256
    assert checksum["canonical_sha256"] == EXECUTION_RULES_CANONICAL_SHA256
    assert hashlib.sha256(RULES_PATH.read_bytes()).hexdigest() == checksum["file_sha256"]


def test_rules_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "changed.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ExecutionContractError, match="rules digest mismatch"):
        build_entry_records(_signal(), _bars(), rules_path=path)


def test_unsupported_entry_model_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = json.loads(execution_module.EXECUTION_RULES_PATH.read_text(encoding="utf-8"))
    original["entry"]["model"] = "UNSUPPORTED_MODEL"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    digest = hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    monkeypatch.setattr(execution_module, "EXECUTION_RULES_CANONICAL_SHA256", digest)

    with pytest.raises(ExecutionContractError, match="unsupported.*entry model"):
        build_entry_records(_signal(), _bars(), rules_path=path)
