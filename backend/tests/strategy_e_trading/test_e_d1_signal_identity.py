"""E-D1 identity gate: Research = Confirmation = Forward = Trading H5."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.backtest.strategy_e1_forward import seal
from app.backtest.strategy_e1_h5_confirm.run import h5_mask as confirmation_h5_mask
from app.backtest.strategy_e1_premarket.evaluate import mask as research_mask
from app.strategy_e import signal as signal_module
from app.strategy_e.signal import (
    TRADING_RULES_CANONICAL_SHA256,
    SignalContractError,
    SignalFrame,
    evaluate_h5_signal,
)


SESSION = date(2026, 9, 21)
SOURCE_DIGEST = "a" * 64


def _features(**changes) -> dict[str, np.ndarray]:
    rows = 8
    features = {
        "premarket_gap": np.array([-0.001, 0.0, np.nextafter(0.0, 1.0), 0.02,
                                     0.02, 0.02, 0.02, np.nan]),
        "premarket_rvol": np.array([3.0, 3.0, 3.0, 2.999999, 3.0,
                                      3.000001, 3.0, 3.0]),
        "position_in_premarket_range": np.array([0.8, 0.8, 0.8, 0.8, 0.799999,
                                                   0.800001, 0.8, 0.8]),
        "return_0900_0925": np.array([0.01, 0.01, np.nextafter(0.0, 1.0), 0.01,
                                        0.01, 0.01, 0.0, 0.01]),
        "return_last30m": np.linspace(-0.01, 0.01, rows),
        "relative_strength_vs_spy": np.linspace(0.01, -0.01, rows),
        "premarket_dollar_volume": np.full(rows, 100_000.0),
        "previous_day_dollar_volume": np.full(rows, 10_000_000.0),
        "close_price": np.full(rows, 20.0),
        "spy_premarket_return": np.zeros(rows),
        "pm_bars": np.full(rows, 3.0),
    }
    features.update(changes)
    return features


def _frame(features=None, **changes) -> SignalFrame:
    values = {
        "session": SESSION,
        "symbols": ("H", "G", "F", "E", "D", "C", "B", "A"),
        "features": features or _features(),
        "source_digest": SOURCE_DIGEST,
    }
    values.update(changes)
    return SignalFrame(**values)


def test_threshold_boundaries_match_frozen_research_h5() -> None:
    features = _features()
    expected = [False, False, True, False, False, True, False, False]

    assert research_mask("H5", features).tolist() == expected
    assert evaluate_h5_signal(_frame(features)).h5_mask == tuple(expected)


@pytest.mark.parametrize("field", seal.H5_INPUTS)
def test_nan_is_fail_closed_and_identical(field: str) -> None:
    features = _features()
    features[field] = np.full(8, np.nan)

    research = research_mask("H5", features)
    trading = evaluate_h5_signal(_frame(features))

    assert not research.any()
    assert trading.h5_mask == tuple(research.tolist())
    assert trading.candidate_symbols == ()


def test_missing_column_and_none_are_explicit_fail_closed_rejections() -> None:
    missing = _features()
    del missing["premarket_gap"]
    with pytest.raises(SignalContractError, match="missing required sealed features"):
        evaluate_h5_signal(_frame(missing))

    invalid = _features()
    invalid["premarket_gap"] = np.array([None] * 8, dtype=object)
    with pytest.raises(SignalContractError, match="must contain numeric values"):
        evaluate_h5_signal(_frame(invalid))


def test_research_confirmation_forward_and_trading_identity() -> None:
    features = _features()
    symbols = _frame().symbols
    research = research_mask("H5", features)
    confirmation = confirmation_h5_mask(SimpleNamespace(features=features))
    forward = seal.build(
        SESSION,
        symbols,
        features,
        provenance=seal.LIVE,
        sources={"source_digest": SOURCE_DIGEST},
        rules_digest=TRADING_RULES_CANONICAL_SHA256,
        created_at=datetime(2026, 9, 21, 13, 25, tzinfo=timezone.utc),
    )
    trading = evaluate_h5_signal(_frame(features))

    assert research.tobytes() == confirmation.tobytes()
    assert trading.h5_mask == tuple(research.tolist())
    assert trading.eligible_count == len(symbols) == forward.eligible_rows
    assert trading.candidate_count == int(research.sum()) == forward.h5_rows
    assert trading.candidate_symbols == tuple(sorted(np.asarray(symbols)[research].tolist()))
    assert trading.decision_digest == forward.decision_digest


def test_repeated_and_reordered_equivalent_inputs_have_stable_identity() -> None:
    first = evaluate_h5_signal(_frame())
    second = evaluate_h5_signal(_frame())
    order = np.arange(7, -1, -1)
    reordered = evaluate_h5_signal(
        _frame(
            {name: values[order] for name, values in _features().items()},
            symbols=tuple(np.asarray(_frame().symbols)[order].tolist()),
        )
    )

    assert first == second
    assert first.candidate_symbols == reordered.candidate_symbols
    assert first.decision_digest == reordered.decision_digest


def test_rules_digest_mismatch_and_unsupported_rules_file_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SignalContractError, match="input rules digest"):
        evaluate_h5_signal(_frame(rules_digest="0" * 64))

    payload = {"declaration": {"contract_id": "STRATEGY_E_TRADING_RULES_V2"}}
    changed = tmp_path / "changed_rules.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    changed_digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(signal_module, "TRADING_RULES_CANONICAL_SHA256", changed_digest)
    with pytest.raises(SignalContractError, match="unsupported.*version"):
        evaluate_h5_signal(
            _frame(rules_digest=changed_digest),
            rules_path=changed,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"decision_time_et": "09:26"}, "decision cutoff"),
        ({"decision_time_et": "invalid"}, "decision cutoff"),
        ({"latest_feature_bar_start_et": "09:25"}, "after the 09:25"),
        ({"latest_feature_bar_start_et": "24:00"}, "invalid latest feature"),
    ],
)
def test_invalid_cutoff_or_post_cutoff_information_is_rejected(changes, message: str) -> None:
    with pytest.raises(SignalContractError, match=message):
        evaluate_h5_signal(_frame(**changes))


def test_extra_execution_fields_cannot_change_alpha_or_digest() -> None:
    plain = evaluate_h5_signal(_frame())
    with_execution = _features(
        entry_price=np.arange(8, dtype=float),
        liquidity_pass=np.ones(8, dtype=float),
        execution_rank=np.arange(8, dtype=float)[::-1],
    )
    extra = evaluate_h5_signal(_frame(with_execution))

    assert extra.h5_mask == plain.h5_mask
    assert extra.candidate_symbols == plain.candidate_symbols
    assert extra.decision_digest == plain.decision_digest


def test_duplicate_or_empty_candidate_identifiers_are_rejected() -> None:
    with pytest.raises(SignalContractError, match="duplicate"):
        evaluate_h5_signal(_frame(symbols=("A",) * 8))
    with pytest.raises(SignalContractError, match="non-empty"):
        evaluate_h5_signal(_frame(symbols=("", "B", "C", "D", "E", "F", "G", "H")))
