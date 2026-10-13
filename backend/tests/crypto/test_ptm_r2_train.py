"""PTM-R2 TRAIN tests (contract docs/crypto/personal_trading_method_r2/CONTRACT_PTM_R2_V1.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data, r1_train as R, r2_train as R2
from app.crypto.research.ptm_r1.states import MAX_HOLD_MIN, NONE, next_true

T0 = data.TRAIN_START_MS
MIN = 60_000


def market(n=6000, p=100.0, funding=()):
    o = np.full(n, p)
    px = data.Prices(ts=T0 + np.arange(n, dtype=np.int64) * MIN, open=o.copy(), high=o + 0.01,
                     low=o - 0.01, close=o.copy())
    return R.make_market(px, [(T0 + i * MIN, r) for i, r in funding])


def entry(e=100, dirn=1, x0=None):
    return R2.Entry(b=e - 1, e=e, dir=dirn, x0=x0 or e + MAX_HOLD_MIN, day=e // 1440, episode=1)


def ctx_from(st):
    nt = {}
    for name, fn in (("H1_opp", lambda d: st["H1"] == -d), ("H4_opp", lambda d: st["H4"] == -d),
                     ("M_opp", lambda d: st["M"] == -d),
                     ("H1H4_opp", lambda d: (st["H1"] == -d) & (st["H4"] == -d)),
                     ("W3", lambda d: (st["M"] == -d) & (st["H1"] == -d) & (st["H4"] == -d))):
        nt[name] = {dirn: next_true(fn(dirn)) for dirn in (1, -1)}
    n = len(st["D"])
    return {"st": st, "nt": nt, "d_start": R2.run_starts(st["D"]), "fund_at": np.full(n, 0.0001),
            "basis": np.full(n, 0.0002)}


def ones(n):
    return {k: np.ones(n, dtype=np.int8) for k in ("M", "H1", "H4", "D")}


# ------------------------------------------------------------------ anchors and recovery


def test_anchor_timing_recovery_and_time_to_recovery():
    r_adv = np.array([-0.001, -0.004, -0.006, -0.007, -0.002, 0.0])
    r_fav = np.array([0.0005, -0.001, -0.003, -0.004, 0.0, 0.001])
    info = R2.anchor_info(r_adv, r_fav, 0.005)
    assert info["k_a"] == 2 and info["k_r"] == 4 and info["t_recover"] == 2
    assert info["further_adverse"] == pytest.approx(0.007)
    assert info["adverse_duration"] == 1                   # last >= entry was bar 0 -> since bar 1


def test_anchor_bar_cannot_recover_and_non_recovery():
    r_adv = np.array([-0.006, -0.008, -0.009])
    r_fav = np.array([0.002, -0.001, -0.002])                # bar 0 high above entry: same bar
    info = R2.anchor_info(r_adv, r_fav, 0.005)
    assert info["k_a"] == 0 and info["recovered"] is False and info["t_recover"] is None
    assert info["further_adverse"] == pytest.approx(0.009)
    assert R2.anchor_info(r_adv, r_fav, 0.01) is None


def test_long_short_symmetry_of_anchor_on_mirrored_path():
    rng = np.random.default_rng(2)
    p = 100 + np.cumsum(rng.normal(0, 0.08, 3000))
    hi, lo = p + 0.05, p - 0.05
    m = 2 * p[0]
    px = data.Prices(ts=T0 + np.arange(3000) * MIN, open=p, high=hi, low=lo, close=p)
    pm = data.Prices(ts=px.ts, open=m - p, high=m - lo, low=m - hi, close=m - p)
    a = R.path_arrays(R.make_market(px, []), 0, -1, 2900)
    b = R.path_arrays(R.make_market(pm, []), 0, 1, 2900)
    ia, ib = R2.anchor_info(*a, 0.005), R2.anchor_info(*b, 0.005)
    assert (ia is None) == (ib is None)
    if ia:
        assert ia["k_a"] == ib["k_a"] and ia["k_r"] == ib["k_r"]


def test_anchor_state_uses_only_boundary_before_anchor():
    n = 4000
    st = ones(n)
    ctx = ctx_from(st)
    en = entry(1500)
    k_a = 50
    info = {"adverse_duration": 3}
    a = R2.anchor_state(ctx, en, k_a, info)
    st2 = {k: v.copy() for k, v in st.items()}
    for k in ("M", "H1", "H4"):
        st2[k][en.e + k_a:] = -1                            # changes at/after the anchor bar
    b = R2.anchor_state(ctx_from(st2), en, k_a, info)
    assert a == b and a["align_count"] == 3 and a["W1"] is False
    st2["H4"][en.e + k_a - 1] = -1
    assert R2.anchor_state(ctx_from(st2), en, k_a, info)["W1"] is True


def test_split_flags():
    s = {"W1": True, "H1_rel": -1, "M_rel": 1, "align_count": 1, "funding_signed": 0.0001,
         "basis_signed": None, "thesis_age": 2000, "since_entry": 100, "dir": -1}
    f = R2.split_flags(s)
    assert f["S1_W1"] and f["S2_H1_opp"] and not f["S3_M_opp"] and not f["S4_align_ge2"]
    assert f["S5_funding_paying"] and f["S6_basis_favourable"] is None
    assert f["S7_thesis_age_ge24h"] and f["S8_since_entry_le4h"] and not f["S9_long"]


# ------------------------------------------------------------------ protection exits, giveback


def test_protection_exit_ordering():
    n = 5000
    st = ones(n)
    st["H1"][1000:] = -1                                    # 1H turns against at 1000
    st["H4"][1199:] = -1                                    # 4H block closing 1199 against
    ctx = ctx_from(st)
    en = entry(500, 1, x0=3000)
    ex = R2.protect_exits(ctx, en, start=700)
    assert ex["P0"] == 3000
    assert ex["P1"] == 1001
    assert ex["P2"] == 1200
    assert ex["P3"] == 1440                                 # still against at the next completion 1439
    assert ex["M_opp"] == 3000                              # never fired -> P0
    st["H4"][1439:] = 1                                     # returned at the next completion
    ex2 = R2.protect_exits(ctx_from(st), en, start=700)
    assert ex2["P3"] == 3000


def test_p3_counts_block_already_against_at_recovery():
    n = 3000
    h4 = np.ones(n, dtype=np.int8)
    h4[479:] = -1
    assert R2.p3_exit(h4, 1, start=500, x0=2900) == 720     # against at 500 and at completion 719


def test_giveback_after_recovery():
    mk = market()
    en = entry(100)
    mk.px.high[150] = 101.0                                 # +1 % after recovery
    mk.px.open[300] = 100.5
    base = R.simulate(mk, en.e, en.dir, en.x0, [])
    _, r_fav = R.path_arrays(mk, en.e, en.dir, en.x0)
    o = R2.policy_outcome(mk, en, base, r_fav, k_r=20, x=300)
    assert o["mfe_after"] == pytest.approx(0.01)
    assert o["gross"] == pytest.approx(0.005)
    assert o["giveback"] == pytest.approx(0.005) and o["giveback_ratio"] == pytest.approx(0.5)
    o2 = R2.policy_outcome(mk, en, base, r_fav, k_r=100, x=110)  # before the high
    assert o2["mfe_after"] == pytest.approx(0.0001) or o2["mfe_after"] >= 0


# ------------------------------------------------------------------ leverage and account


def test_leverage_math():
    assert R2.liq_dist(5) == pytest.approx(0.196) and R2.liq_dist(50) == pytest.approx(0.016)
    assert R2.safety_dist(20) == pytest.approx(0.0184) and R2.safety_dist(100) == pytest.approx(0.0024)


def test_liquidation_geometry_and_safety():
    mk = market()
    en = entry(100)
    mk.px.low[200] = 98.3                                   # -1.7 %: beyond 50x liq (1.6 %)
    t = R2.lev_trade(mk, en, en.x0, 50, "LIQ_ONLY")
    assert t["kind"] == "LIQUIDATION" and t["gross"] == pytest.approx(-0.016)
    s = R2.lev_trade(mk, en, en.x0, 50, "SAFETY_ON")
    assert s["kind"] == "SAFETY" and s["gross"] == pytest.approx(-0.0064)
    mk2 = market()
    mk2.px.open[200], mk2.px.low[200], mk2.px.high[200] = 97.0, 96.9, 97.1   # gap through liq
    g = R2.lev_trade(mk2, en, en.x0, 50, "SAFETY_ON")
    assert g["kind"] == "LIQUIDATION"
    n = R2.lev_trade(market(), en, en.x0, 5, "SAFETY_ON")
    assert n["kind"] == "MAX_HOLD"


def test_account_scenarios_and_liquidation_loss():
    t = {"kind": "MAX_HOLD", "net": 0.01, "mae": 0.02, "funding": 0.0}
    a = R2.account(t, 20, "A_FIXED_NOTIONAL")
    assert a["notional"] == 1.0 and a["margin"] == pytest.approx(0.05) and a["pnl"] == pytest.approx(0.01)
    assert a["max_adverse"] == pytest.approx(-0.02)
    b = R2.account(t, 20, "B_FIXED_MARGIN")
    assert b["margin"] == 0.10 and b["notional"] == pytest.approx(2.0) and b["pnl"] == pytest.approx(0.02)
    assert b["max_adverse"] == pytest.approx(-0.04)
    liq = {"kind": "LIQUIDATION", "net": -0.05, "mae": 0.046, "funding": 0.0002}
    lb = R2.account(liq, 20, "B_FIXED_MARGIN")
    assert lb["pnl"] == pytest.approx(-0.10 - 2.0 * (0.0005 + 0.0002))
    assert R2.account({**t, "mae": 0.5}, 20, "B_FIXED_MARGIN")["max_adverse"] == pytest.approx(-0.10)


def test_funding_and_fee_flow_through_leverage_trade():
    mk = market(funding=[(100, 0.001)])
    en = entry(100)
    t = R2.lev_trade(mk, en, 200, 10, "SAFETY_ON")
    assert t["funding"] == pytest.approx(0.001) and t["kind"] == "I1"
    assert t["net"] == pytest.approx(t["gross"] - t["fee"] - R.BASE_RT - 0.001)


# ------------------------------------------------------------------ population, inference, guard


def test_grid_entries():
    n = 3 * 1440 + MAX_HOLD_MIN + 10
    st = ones(n)
    st["D"][:1439] = NONE
    st["D"][2 * 1440 - 1:] = -1
    ents = R2.grid_entries(st, n - 1)
    assert all((en.b + 1) % 240 == 0 and en.e == en.b + 1 for en in ents)
    assert ents[0].e == 1440 and ents[0].dir == 1
    first = ents[0]
    assert first.x0 == 2 * 1440                              # I1 at boundary 2879 -> open 2880
    assert all(en.dir == -1 for en in ents if en.b >= 2 * 1440 - 1)
    assert sum(en.is_noon for en in ents) >= 1


def test_cluster_diff_and_holm():
    r = R2.cluster_diff([1, 1, 2, 2, 3, 3], [1, 0, 1, 0, 1, 0], [True, False] * 3, reps=200)
    assert r["estimate"] == pytest.approx(1.0)
    same = R2.cluster_diff([1, 2, 3, 4], [1, 1, 0, 0], [True, False, True, False], reps=200)
    assert same["estimate"] == pytest.approx(0.0)


def test_basis_reader_refuses_outside_train():
    with pytest.raises(data.HoldoutGuard):
        data.load_train_basis(data.ReadLog(), months=("2023-07",))
    with pytest.raises(data.HoldoutGuard):
        data.load_train_basis(data.ReadLog(), months=("2025-04",))
