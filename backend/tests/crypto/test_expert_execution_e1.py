"""E1 maker adverse selection: reference-quote alignment, signing, staleness, leakage, fees,
clustering and market-row cleaning. Synthetic data only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.crypto.research.expert_execution import e1 as E
from app.crypto.research.expert_execution import market as M

S = 10**9
T0 = int(pd.Timestamp("2021-05-19 10:00:00", tz="UTC").value)


def _quotes(rows):
    """rows: (seconds from T0, bid, ask[, bid_size, ask_size])"""
    return pd.DataFrame({"ts": [T0 + int(r[0] * S) for r in rows], "bid": [r[1] for r in rows],
                         "ask": [r[2] for r in rows], "bid_size": [r[3] if len(r) > 3 else 100 for r in rows],
                         "ask_size": [r[4] if len(r) > 4 else 100 for r in rows]})


def _fill(sec, side, px, role="MAKER", commission=-0.00025, qty=10, t_abs=None):
    return pd.DataFrame({"seq": [0], "orderid": ["o"], "unit": [f"o|{role}"], "cluster": [0],
                         "t": [t_abs if t_abs is not None else T0 + int(sec * S)], "side_sign": [side],
                         "lastqty": [qty], "lastpx": [px], "commission": [commission],
                         "fee_bp": [-commission * 1e4], "value_xbt": [1.0], "role": [role], "act": ["ADD"]})


EMPTY_TR = pd.DataFrame({"ts": np.array([], np.int64)})


def test_reference_quote_is_strictly_before_fill():
    q = _quotes([(-1, 100.0, 101.0), (0, 90.0, 91.0), (1, 100.0, 101.0)])   # quote AT t reflects the fill
    m = E.fill_metrics(_fill(0, 1, 100.0), q, EMPTY_TR)
    assert m.mid0.iloc[0] == 100.5 and bool(m.valid0.iloc[0])


def test_midpoint_and_side_signed_moves():
    q = _quotes([(-1, 100.0, 101.0), (0.5, 99.0, 100.0), (4, 102.0, 103.0)])
    buy = E.fill_metrics(_fill(0, 1, 100.0), q, EMPTY_TR).iloc[0]
    sell = E.fill_metrics(_fill(0, -1, 101.0), q, EMPTY_TR).iloc[0]
    # buy maker at bid 100 with mid 100.5: +0.5 spread capture; after 1s mid 99.5 -> adverse -1.0
    assert buy.spread_raw == pytest.approx(0.5) and buy.move_raw_1s == pytest.approx(-1.0)
    assert buy.move_bp_1s == pytest.approx(-1.0 / 100.5 * 1e4)
    assert buy.svf_raw_1s == pytest.approx(99.5 - 100.0)
    assert buy.move_raw_5s == pytest.approx(102.5 - 100.5)             # favorable for the buyer
    # sell at ask 101: +0.5 capture; mid falls to 99.5 after 1s -> favorable +1.0 for the seller
    assert sell.spread_raw == pytest.approx(0.5) and sell.move_raw_1s == pytest.approx(1.0)
    assert sell.move_raw_5s == pytest.approx(-2.0)
    assert buy.net_bp_1s == pytest.approx(2.5 + buy.spread_bp + buy.move_bp_1s)


def test_role_mapping_uses_liquidity_indicator_not_ordtype():
    r = E.role_of(pd.Series(["AddedLiquidity", "RemovedLiquidity", "", None]))
    assert r.tolist() == ["MAKER", "TAKER", "UNKNOWN", "UNKNOWN"]
    assert E.action_group("REVERSE_TO_LONG") == "REVERSE" and E.action_group("ADD_SHORT") == "ADD"


def test_missing_and_stale_reference_quote_invalidate_fill():
    none = E.fill_metrics(_fill(0, 1, 100.0), _quotes([(5, 100.0, 101.0)]), EMPTY_TR).iloc[0]
    assert not none.valid0 and np.isnan(none.move_bp_60s)
    stale = E.fill_metrics(_fill(0, 1, 100.0), _quotes([(-61, 100.0, 101.0)]), EMPTY_TR).iloc[0]
    assert not stale.valid0
    fresh = E.fill_metrics(_fill(0, 1, 100.0), _quotes([(-59, 100.0, 101.0)]), EMPTY_TR).iloc[0]
    assert fresh.valid0


def test_stale_future_quote_only_blanks_that_horizon():
    q = _quotes([(-1, 100.0, 101.0), (2, 100.5, 101.5)])   # nothing after t+2s
    r = E.fill_metrics(_fill(0, 1, 100.0), q, EMPTY_TR).iloc[0]
    assert r.valid0 and np.isfinite(r.move_bp_60s)           # age at t+60 = 58s
    assert np.isnan(r.move_bp_300s)                         # age 298s > 60s


def test_crossed_quote_is_invalid():
    r = E.fill_metrics(_fill(0, 1, 100.0), _quotes([(-1, 101.0, 101.0)]), EMPTY_TR).iloc[0]
    assert not r.valid0


def test_regime_features_ignore_the_future():
    rng = np.random.default_rng(3)
    secs = np.arange(-4000, 1000, 0.5)
    mid = 100 + np.cumsum(rng.normal(0, 0.05, len(secs)))
    q = _quotes([(s, m - 0.25, m + 0.25) for s, m in zip(secs, mid)])
    tr = pd.DataFrame({"ts": T0 + (np.arange(-120, 120, 0.25) * S).astype(np.int64)})
    f = _fill(0.3, 1, 100.0)
    base = E.fill_metrics(f, q, tr).iloc[0]
    q2, tr2 = q.copy(), tr.copy()
    later = q2.ts >= f.t.iloc[0]
    q2.loc[later, ["bid", "ask"]] += 50.0
    tr2 = pd.concat([tr2[tr2.ts < f.t.iloc[0]], pd.DataFrame({"ts": [f.t.iloc[0] + 1]})])
    alt = E.fill_metrics(f, q2, tr2.sort_values("ts")).iloc[0]
    for c in ("mid0", "spread_bp", "vol60", "ret60", "trend_z", "trades_60s", "depth_ratio"):
        assert (base[c] == alt[c]) or (np.isnan(base[c]) and np.isnan(alt[c])), c
    assert base.move_bp_60s != alt.move_bp_60s                 # the target does move
    assert base.trades_60s == 240 and np.isfinite(base.vol60)


def test_fee_mapping_historical_and_bybit_reference():
    r = E.fill_metrics(_fill(0, 1, 100.0, commission=-0.0001), _quotes([(-1, 100.0, 101.0)]), EMPTY_TR).iloc[0]
    assert r.fee_bp == pytest.approx(1.0)                       # 2021 maker rebate -0.01% -> +1bp
    t = E.fill_metrics(_fill(0, 1, 101.0, role="TAKER", commission=0.00075), _quotes([(-1, 100.0, 101.0)]), EMPTY_TR).iloc[0]
    assert t.fee_bp == pytest.approx(-7.5) and t.spread_raw == pytest.approx(-0.5)
    bb = E.bybit_rates()
    assert bb["maker"] == pytest.approx(0.0002) and bb["taker"] == pytest.approx(0.00055)


def test_year_boundary_window_uses_next_day_quotes():
    d0 = pd.DataFrame({"ts": [int(pd.Timestamp("2018-12-31 23:59:58", tz="UTC").value)], "bid": [100.0],
                       "ask": [101.0], "bid_size": [1], "ask_size": [1]})
    d1 = pd.DataFrame({"ts": [int(pd.Timestamp("2019-01-01 00:00:30", tz="UTC").value)], "bid": [110.0],
                       "ask": [111.0], "bid_size": [1], "ask_size": [1]})
    lo = int(pd.Timestamp("2018-12-31", tz="UTC").value) - E.PRE_WINDOW_NS
    hi = int(pd.Timestamp("2018-12-31", tz="UTC").value) + E.DAY_NS + E.POST_WINDOW_NS
    q = E.window([None, d0, d1], lo, hi)
    t = int(pd.Timestamp("2018-12-31 23:59:59.5", tz="UTC").value)
    r = E.fill_metrics(_fill(0, 1, 100.0, t_abs=t), q, EMPTY_TR).iloc[0]
    assert r.mid0 == 100.5 and r.mid_60s == 110.5 and r.year == 2018


def test_duplicate_and_same_timestamp_market_rows():
    ts = np.array([1, 1, 2, 2, 3], np.int64)
    bid = np.array([10., 10., 11., 12., 13.])
    ask = bid + 1
    cols, st = M.clean_quotes(ts, bid, ask, np.ones(5, np.int64), np.ones(5, np.int64))
    assert st["exact_duplicates"] == 1 and st["same_ts_superseded"] == 1
    assert cols["ts"].tolist() == [1, 2, 3] and cols["bid"].tolist() == [10., 12., 13.]   # last row of ts=2 wins
    cols2, st2 = M.clean_quotes(np.array([2, 1], np.int64), np.array([5., 4.]), np.array([6., 5.]),
                                np.ones(2, np.int64), np.ones(2, np.int64))
    assert st2["nonmonotonic_steps"] == 1 and cols2["ts"].tolist() == [1, 2]


def test_cluster_bootstrap_ratio_and_clustering():
    x = np.array([1.0, 1.0, -1.0, 5.0])
    w = np.array([1.0, 1.0, 2.0, 2.0])
    cl = np.array([0, 0, 1, 1])
    est, se, lo, hi = E.cluster_boot(x, w, cl, b=500, seed=1)
    assert est == pytest.approx((1 + 1 - 2 + 10) / 6)
    assert lo <= est <= hi and se > 0
    assert E.cluster_boot(x, w, cl, b=500, seed=1) == (est, se, lo, hi)      # reproducible
    # one cluster only: bootstrap cannot vary
    assert E.cluster_boot(x, w, np.zeros(4, int), b=200, seed=1)[1] == pytest.approx(0.0)


def test_order_level_units_equal_weight():
    g = pd.DataFrame({"x": [2.0, 4.0, -10.0], "value_xbt": [1.0, 3.0, 1.0], "cluster": [0, 0, 1],
                      "unit_id": [0, 0, 1]})
    d = E.describe(g, "x")
    assert d["vw_mean"] == pytest.approx((2 + 12 - 10) / 5)
    assert d["order_mean"] == pytest.approx(((2 + 12) / 4 + -10) / 2)
    assert d["n_orders"] == 2 and d["n_fills"] == 3
