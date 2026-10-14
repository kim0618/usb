"""HOT-EDGE R0 tests (contract docs/crypto/hot_coin_edge_r0/CONTRACT_HOT_EDGE_R0_V1.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.hot_edge_r0 import data as D, study as S
from app.crypto.research.ptm_r1 import r1_train as R


def grid(n_sym=3, n_bars=None, price=1.0, qv=1e5):
    n_bars = n_bars or D.N_BARS
    mk = lambda v: np.full((n_sym, n_bars), v, dtype=np.float32)   # noqa: E731
    g = D.Grid(symbols=[f"S{i}USDT" for i in range(n_sym)], open=mk(price), high=mk(price), low=mk(price),
               close=mk(price), qv=mk(qv), funding={f"S{i}USDT": (np.zeros(0, np.int64), np.zeros(0)) for i in range(n_sym)})
    return g


def test_rolling_sum_nan_and_window():
    x = np.array([[1, 2, np.nan, 4, 5, 6]], dtype=np.float32)
    r = S.rolling_sum(x, 2)
    assert np.isnan(r[0, 0]) and r[0, 1] == 3 and np.isnan(r[0, 2]) and np.isnan(r[0, 3]) and r[0, 4] == 9


def test_rank_desc_only_eligible():
    score = np.array([[3.0], [1.0], [2.0], [9.0]])
    elig = np.array([[True], [True], [True], [False]])
    r = S.rank_desc(score, elig)
    assert list(r[:, 0]) == [1, 3, 2, 0]


def test_outcomes_signed_return_funding_and_mae():
    g = grid(price=100.0)
    s, e = 1000, 1001
    g.open[0, e] = 100.0
    g.open[0, e + 48] = 103.0
    g.low[0, e + 5] = 98.0
    g.high[0, e + 10] = 105.0
    g.funding["S0USDT"] = (np.array([e + 1, e + 100]), np.array([0.001, 0.5]))
    o = S.outcomes(g, 0, s, 1, 48)
    assert o["gross"] == pytest.approx(0.03) and o["funding"] == pytest.approx(0.001)
    assert o["mae_h"] == pytest.approx(0.02) and o["mfe"] >= 0.05
    o2 = S.outcomes(g, 0, s, -1, 48)
    assert o2["gross"] == pytest.approx(-0.03) and o2["funding"] == pytest.approx(-0.001)
    assert S.net_cost(o, 0.004) == pytest.approx(0.03 - 0.004 - 0.001)
    assert S.outcomes(g, 0, D.N_BARS - 10, 1, 48) is None


def test_features_eligibility_gates_and_direction():
    g = grid(n_sym=2, qv=1e5)                      # qv24h = 288 * 1e5 = 28.8M >= 20M ; qv1h = 1.2M >= 0.5M
    g.qv[1, :] = 1e4                               # symbol 1 fails qv24h
    scans = np.array([S.LISTING_AGE + 48 * 10 - 1])
    g.close[0, scans[0]] = 1.02                    # +2 % over the last hour and day
    first = np.array([0, 0])
    f = S.scan_features(g, scans, first)
    assert f["elig"][0, 0] and not f["elig"][1, 0]
    sc, d = S.variant_score_dir(f, "V1")
    assert d[0, 0] == 1 and sc[0, 0] == pytest.approx(0.0, abs=1e-6)
    # listing age: a symbol first seen 10 days before the scan is not eligible
    f2 = S.scan_features(g, scans, np.array([scans[0] - 10 * 288, 0]))
    assert not f2["elig"][0, 0]


def test_build_events_top_ranks_and_random_rest():
    g = grid(n_sym=25, qv=1e5)
    s = S.LISTING_AGE + 48 * 5 - 1
    scans = np.array([s])
    for i in range(25):
        g.high[i, s - 5] = 1.0 + 0.01 * (25 - i)       # range rank = i + 1
        g.close[i, s] = 1.001                           # positive 1 h change
    f = S.scan_features(g, scans, np.zeros(25, dtype=int))
    hot = S.build_events(g, f, scans, "V1", 1, 5)
    assert sorted(e["rank"] for e in hot) == [1, 2, 3, 4, 5] and all(e["dir"] == 1 for e in hot)
    rest = S.build_events(g, f, scans, "V1", 21, 10 ** 6, rng=np.random.default_rng(0), per_scan=3)
    assert len(rest) == 3 and all(e["rank"] >= 21 for e in rest)


def test_verdict_gates():
    cell = {"n": 400, "symbols": 40, "breakeven_cost_rt": 0.01,
            "by_cost": {"BASE": {"net_mean": 0.005, "inference": {"ci95": [0.001, 0.009], "p": 0.01}}}}
    rnd = {"n": 400, "paired_hot_minus_random": {"ci95": [0.001, 0.006], "mean": 0.004, "p": 0.01}}
    dose = {"top5": {"net_mean_base": 0.005}, "rank6_20": {"net_mean_base": 0.002}, "rank21plus": {"net_mean_base": 0.0}}
    conc = {"top5pct_events_share_of_positive_gross": 0.3}
    years = {"2021": {"net_mean_base": 0.004}, "2022": {"net_mean_base": 0.006}, "2023H1": {"net_mean_base": -0.001}}
    v, d, gates = S.verdict(cell, rnd, dose, conc, years, holm_p=0.03)
    assert v.startswith("C") and d == "CONTINUATION" and all(gates["C"].values())
    cell["by_cost"]["BASE"]["net_mean"] = -0.005
    cell["by_cost"]["BASE"]["inference"]["ci95"] = [-0.009, -0.001]
    cell["breakeven_cost_rt"] = -0.01
    v, d, _ = S.verdict(cell, rnd, dose, conc, years, holm_p=0.03)
    assert d == "NONE" and v.startswith("A")          # random/dose/years were in the wrong sign


def test_reader_refuses_non_train_month(tmp_path):
    with pytest.raises(D.HoldoutGuard):
        D._lines(tmp_path / "ETHUSDT-5m-2023-07.zip", D._K, D.ReadLog())
