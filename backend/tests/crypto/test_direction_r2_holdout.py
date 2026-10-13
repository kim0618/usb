"""R2 final holdout tests (contract docs/crypto/direction_asymmetry_r2/CONTRACT_DIRECTION_R2_FINAL_HOLDOUT_V1.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data as PD
from app.crypto.research.direction_asymmetry_r2 import hdata as HD


def test_window_and_guards():
    assert HD.MONTHS[0] == "2021-02" and HD.MONTHS[-1] == "2026-08"
    assert HD.LAST_DAY.isoformat() == "2026-08-31"
    assert HD.HOLDOUT_START_MS == 1_743_465_600_000          # 2025-04-01
    assert HD.VAL_END_MS == 1_788_220_800_000                # 2026-09-01 exclusive
    with pytest.raises(HD.HoldoutGuard):
        HD._open_month(PD.MONTHLY / "BTCUSDT-1m-2026-09.zip", PD.ReadLog())
    with pytest.raises(HD.HoldoutGuard):
        HD._idx(HD.VAL_END_MS)


def test_spearman_and_ranks():
    import importlib.util
    if importlib.util.find_spec("sklearn") is None:
        pytest.skip("holdout module imports sklearn (system interpreter only)")
    from app.crypto.research.direction_asymmetry_r2 import holdout as H
    assert H.spearman(np.array([1, 2, 3, 4.0]), np.array([10, 20, 30, 40.0])) == pytest.approx(1.0)
    assert H.spearman(np.array([1, 2, 3, 4.0]), np.array([4, 3, 2, 1.0])) == pytest.approx(-1.0)
    assert list(H.ranks(np.array([5.0, 1.0, 5.0]))) == [2.5, 1.0, 2.5]
