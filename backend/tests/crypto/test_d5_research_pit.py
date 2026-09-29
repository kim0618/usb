"""D5 PIT guards: features never see bar t+1, targets never start inside bar t."""
from __future__ import annotations

import numpy as np

from app.crypto.research import dataset, features as F

MIN = dataset.MINUTE_MS


def _grid(n: int = 4000, seed: int = 1) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    c = 30000 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    o = np.r_[c[0], c[:-1]]
    ts = dataset.RESEARCH_START_MS + np.arange(n, dtype=np.int64) * MIN
    fts = np.arange(ts[0], ts[-1] + 1, 8 * 3_600_000, dtype=np.int64)
    return {
        "ts": ts, "open": o, "close": c, "high": np.maximum(o, c) * 1.0005, "low": np.minimum(o, c) * 0.9995,
        "volume": rng.uniform(1, 5, n), "turnover": rng.uniform(1e5, 5e5, n),
        "oi": 5e4 + np.cumsum(rng.normal(0, 5, n)), "index_close": c * (1 + rng.normal(0, 1e-4, n)),
        "mark_close": c, "funding_last": np.full(n, 1e-4),
        "funding_ts": fts, "funding_rate": rng.normal(1e-4, 5e-5, len(fts)),
    }


def test_features_ignore_future_bars():
    g = _grid()
    t = 3000
    base = F.compute_features(g)
    g2 = {k: v.copy() for k, v in g.items()}
    for k in ("open", "close", "high", "low", "turnover", "oi", "index_close"):
        g2[k][t + 1:] *= 1.37
    moved = F.compute_features(g2)
    for name in base:
        a, b = base[name][: t + 1], moved[name][: t + 1]
        assert np.array_equal(np.isnan(a), np.isnan(b)), name
        assert np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=0, atol=1e-9), name


def test_targets_enter_next_open_and_exit_open_t_plus_1_plus_h():
    g = _grid()
    for h in F.HORIZONS:
        T = F.targets(g, h)
        t = 1234
        assert np.isclose(T["long"][t], g["open"][t + 1 + h] / g["open"][t + 1] - 1)
        assert np.isclose(T["mfe_long"][t], g["high"][t + 1: t + 1 + h].max() / g["open"][t + 1] - 1)
        assert np.isclose(T["mae_long"][t], g["low"][t + 1: t + 1 + h].min() / g["open"][t + 1] - 1)
        # bar t's own high/low never enter the window
        g2 = {k: v.copy() for k, v in g.items()}
        g2["high"][t] *= 2
        g2["low"][t] *= 0.5
        T2 = F.targets(g2, h)
        assert T2["mfe_long"][t] == T["mfe_long"][t] and T2["mae_long"][t] == T["mae_long"][t]


def test_funding_charged_only_when_holding_through_settlement():
    g = _grid()
    h = 30
    T = F.targets(g, h)
    i = int((g["funding_ts"][2] - g["ts"][0]) // MIN)  # settlement opens row i
    rate = g["funding_rate"][2]
    assert np.isclose(T["fund_long"][i - h], rate)   # enter row i-h+1, still open at row i
    assert np.isclose(T["fund_long"][i - 1], rate)   # enter row i exactly at settlement
    assert T["fund_long"][i - h - 1] == 0            # exits at open of row i -> not held
    assert T["fund_long"][i] == 0                    # enters after settlement


def test_bucket_cutoffs_use_previous_days_only():
    rng = np.random.default_rng(3)
    n_days = 40
    x = rng.normal(size=n_days * F.DAY_BARS)
    b = F.bucketize(x, n_days)
    x2 = x.copy()
    x2[35 * F.DAY_BARS + 700:] += 100  # later part of day 35 onwards
    b2 = F.bucketize(x2, n_days)
    assert np.array_equal(b[: 35 * F.DAY_BARS + 700], b2[: 35 * F.DAY_BARS + 700])
    assert (b[: 30 * F.DAY_BARS] == -1).all()


def test_oi_known_one_interval_after_stamp():
    src_ts = np.array([0, 300_000, 600_000], dtype=np.int64)
    val = np.array([1.0, 2.0, 3.0])
    q = np.array([299_999, 300_000, 599_999, 600_000])
    got = dataset._asof(src_ts, val, src_ts + dataset.OI_DELAY_MS, q)
    assert np.isnan(got[0]) and got[1] == 1.0 and got[2] == 1.0 and got[3] == 2.0
