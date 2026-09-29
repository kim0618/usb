"""D5.1 PIT guards for the multi-timeframe and basis features."""
from __future__ import annotations

import numpy as np

from app.crypto.research import dataset, long_horizon as LH

MIN = dataset.MINUTE_MS


def _grid(n: int = 6000, seed: int = 7):
    rng = np.random.default_rng(seed)
    c = 30000 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    ts = dataset.RESEARCH_START_MS + np.arange(n, dtype=np.int64) * MIN
    idx = c * (1 + rng.normal(0, 1e-4, n))
    return {"ts": ts, "close": c, "mark_close": c * (1 + rng.normal(0, 1e-4, n)), "index_close": idx}


def test_last_closed_k_bar_never_includes_open_bar():
    ts = dataset.RESEARCH_START_MS + np.arange(30, dtype=np.int64) * MIN
    j5 = LH.last_closed_index(ts, 5)
    # row 4 closes 00:05 -> the 00:00-00:05 5m bar is closed and its last 1m row is 4
    assert j5[4] == 4
    # rows 5..8 are inside the 00:05-00:10 bar -> still the previous closed bar (row 4)
    assert (j5[5:9] == 4).all()
    assert j5[9] == 9
    assert (j5[:4] < 0).all()


def test_new_features_ignore_future_bars():
    g = _grid()
    t = 5000
    base = LH.new_features(g)
    g2 = {k: v.copy() for k, v in g.items()}
    for k in ("close", "mark_close", "index_close"):
        g2[k][t + 1:] *= 1.5
    moved = LH.new_features(g2)
    for name in base:
        a, b = base[name][: t + 1], moved[name][: t + 1]
        assert np.array_equal(np.isnan(a), np.isnan(b)), name
        assert np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], atol=1e-12), name


def test_mtf_values_match_manual_aggregation():
    g = _grid()
    f = LH.new_features(g)
    lnC = np.log(g["close"])
    t = 4000 + 7  # not on a 15m boundary
    j15 = (t + 1) // 15 * 15 - 1
    assert np.isclose(f["mtf15_trend_4h"][t], lnC[j15] - lnC[j15 - 240])
    j60 = (t + 1) // 60 * 60 - 1
    hr = [lnC[j60 - 60 * m] - lnC[j60 - 60 * (m + 1)] for m in range(24)]
    assert np.isclose(f["mtf1h_vol_24h"][t], np.std(hr))
    assert np.isclose(f["basis_mark_index"][t], np.log(g["mark_close"][t] / g["index_close"][t]))
