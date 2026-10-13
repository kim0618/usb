from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.experimental_ls import _flat_trades, _starts, _summary


def test_event_starts_are_deterministic_and_contiguous_true_bars_collapse() -> None:
    mask = np.array([False, True, True, False, True, False, True, True])
    assert _starts(mask).tolist() == [1, 4, 6]


def test_flat_trade_filter_never_overlaps_fixed_240_minute_hold() -> None:
    signals = np.array([10, 20, 250, 251, 500, 800])
    kept = _flat_trades(signals, 2_000)
    assert kept.tolist() == [10, 251, 500, 800]
    assert np.all(np.diff(kept + 1) > 240)


def test_summary_uses_trade_level_net_results() -> None:
    result = _summary(np.array([.01, -.005, .002]))
    assert result["trades"] == 3
    assert result["mean_net_bp"] == pytest.approx(70 / 3)
    assert result["median_net_bp"] == pytest.approx(20)
    assert result["profit_factor"] == pytest.approx(2.4)
    assert result["win_rate"] == pytest.approx(2 / 3)
