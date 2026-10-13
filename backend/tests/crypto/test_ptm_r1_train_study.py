"""PTM-R1 V3 TRAIN study tests (contract CONTRACT_PTM_R1_V3_TRAIN_STUDY.md)."""
from __future__ import annotations

import json
import zipfile

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data, r1_train as R, v3
from app.crypto.research.ptm_r1.states import MAX_HOLD_MIN, next_true

T0 = data.TRAIN_START_MS
MIN = 60_000


def market(n=4000, p=100.0, funding=()):
    o = np.full(n, p)
    px = data.Prices(ts=T0 + np.arange(n, dtype=np.int64) * MIN, open=o.copy(), high=o + 0.05,
                     low=o - 0.05, close=o.copy())
    return R.make_market(px, [(T0 + i * MIN, r) for i, r in funding])


def bar(mk, k, o=None, h=None, lo=None):
    if o is not None:
        mk.px.open[k] = o
    if h is not None:
        mk.px.high[k] = h
    if lo is not None:
        mk.px.low[k] = lo


# ------------------------------------------------------------------ execution


def test_entry_is_next_minute_open_and_earlier_prices_do_not_matter():
    mk = market()
    bar(mk, 10, o=101.0, h=101.0, lo=101.0)
    a = R.simulate(mk, 11, 1, 20, [])
    bar(mk, 10, o=50.0, h=50.0, lo=50.0)                # the setup minute itself
    b = R.simulate(mk, 11, 1, 20, [])
    assert a == b
    assert a["gross"] == 0.0                             # entry open[11] = exit open[20] = 100


def test_prices_after_exit_do_not_matter():
    mk = market()
    a = R.simulate(mk, 11, 1, 20, [0.005])
    bar(mk, 25, o=10.0, h=10.0, lo=10.0)
    assert R.simulate(mk, 11, 1, 20, [0.005]) == a


def test_fixed_stop_fill_at_level_and_gap_fill_at_open():
    mk = market()
    bar(mk, 14, lo=99.4)
    t = R.simulate(mk, 11, 1, 100, [0.005, R.s_cat(20)], cat=R.s_cat(20))
    assert t["kind"] == "FIXED_STOP" and t["exit_bar"] == 14
    assert t["gross"] == pytest.approx(-0.005)
    mk2 = market()
    bar(mk2, 14, o=99.0, h=99.1, lo=98.9)
    t2 = R.simulate(mk2, 11, 1, 100, [0.005, R.s_cat(20)], cat=R.s_cat(20))
    assert t2["gross"] == pytest.approx(-0.01)           # opened through the level


def test_catastrophic_stop_and_short_side():
    mk = market()
    bar(mk, 30, h=101.9)                                  # SHORT adverse 1.9 % > 1.84 %
    t = R.simulate(mk, 11, -1, 2000, [R.s_cat(20)], cat=R.s_cat(20))
    assert t["kind"] == "CATASTROPHIC" and t["gross"] == pytest.approx(-0.0184)
    assert R.s_cat(20) == pytest.approx(0.0184) and R.s_cat(10) == pytest.approx(0.0384)
    a3 = R.policy_stops(20)["A3"]
    assert min(a3) == R.s_cat(20)                         # A3 2.0 % is non-operative at 20x


def test_mfe_mae_and_adverse_first_inside_stop_bar():
    mk = market()
    bar(mk, 12, h=100.8)                                  # +0.8 % favourable
    bar(mk, 15, h=101.5, lo=99.0)                        # same bar: new high and stop
    t = R.simulate(mk, 11, 1, 100, [0.005, R.s_cat(20)], cat=R.s_cat(20))
    assert t["exit_bar"] == 15
    assert t["mfe"] == pytest.approx(0.008)               # 101.5 ignored (adverse first)
    assert t["mae"] == pytest.approx(0.005)
    assert t["ambiguous"] is True
    s = R.summarize([t])
    assert s["giveback_abs_mean"] == pytest.approx(0.013)
    assert s["giveback_pct_median"] == pytest.approx(0.013 / 0.008)


def test_max_hold_and_i1_exit():
    n = 6000
    mk = market(n)
    d = np.ones(n, dtype=np.int8)
    d[1439 + 1440:] = -1                                   # thesis flips at the close of day 2
    nxt = {1: next_true(d == -1), -1: next_true(d == 1)}
    s = v3.SetupV3(t=100, direction=1, family="F1", tag="x")
    assert R.i1_exit(nxt, s, mk.last) == 1439 + 1440 + 1   # open after the boundary
    s2 = v3.SetupV3(t=2900, direction=-1, family="F1", tag="x")
    nxt2 = {1: next_true(np.zeros(n, bool)), -1: next_true(np.zeros(n, bool))}
    assert R.i1_exit(nxt2, s2, mk.last) == 2901 + MAX_HOLD_MIN
    pol = R.run_policies(mk, [s], nxt, 20)
    assert pol["C"][0]["kind"] == "I1" and pol["C"][0]["exit_bar"] == 2880
    assert pol["B"][0]["kind"] == "MAX_HOLD" and pol["B"][0]["exit_bar"] == 101 + MAX_HOLD_MIN
    assert {p: pol[p][0]["e"] for p in pol} == {p: 101 for p in pol}   # paired: same entry


def test_funding_settlement_inclusive_and_signed():
    mk = market(funding=[(11, 0.001), (50, 0.002), (60, 0.005)])
    t = R.simulate(mk, 11, 1, 50, [])                     # settlements at 11 and 50 counted
    assert t["settlements"] == 2
    assert t["funding"] == pytest.approx(0.003)
    ts = R.simulate(mk, 11, -1, 50, [])
    assert ts["funding"] == pytest.approx(-0.003)          # SHORT receives a positive rate


def test_fee_spread_and_net():
    mk = market()
    bar(mk, 50, o=101.0)
    t = R.simulate(mk, 11, 1, 50, [])
    assert t["gross"] == pytest.approx(0.01)
    assert t["fee"] == pytest.approx(0.0005 * (1 + 1.01))
    assert t["net"] == pytest.approx(0.01 - 0.0005 * 2.01 - R.BASE_RT)
    st = R.simulate(mk, 11, 1, 50, [], R.STRESS_FEE_SIDE, R.STRESS_RT)
    assert st["net"] < t["net"]


def test_loss_then_recovery():
    r_adv = np.array([-0.001, -0.006, -0.002, 0.0])
    r_fav = np.array([0.0, -0.004, 0.0005, 0.0012])
    assert R.recovery(r_adv, r_fav, 0.0, 0.001) == {"reached": True, "gross": True, "net": True}
    assert R.recovery(r_adv, r_fav, 0.005, 0.002) == {"reached": True, "gross": True, "net": False}
    assert R.recovery(r_adv, r_fav, 0.01, 0.001)["reached"] is False


def test_fixed_stop_false_exit_counts_recovery_after_stop():
    n = 6000
    mk = market(n)
    bar(mk, 20, lo=99.4)                                  # A1 stop
    bar(mk, 300, h=100.2)                                 # recovers above entry later
    d = np.ones(n, dtype=np.int8)
    nxt = {1: next_true(d == -1), -1: next_true(d == 1)}
    s = v3.SetupV3(t=10, direction=1, family="F1", tag="x")
    pol = R.run_policies(mk, [s], nxt, 20)
    um = R.user_metrics(mk, [s], pol)
    fe = um["fixed_stop_false_exit"]["A1"]
    assert fe["fixed_stop_exits"] == 1 and fe["later_recovered_entry"] == 1
    assert fe["later_recovered_net_be"] == 1               # 100.2 >= 100 * (1 + 0.001...)
    assert um["hold_value"]["C_minus_A1"]["n"] == 1


def test_long_short_symmetry_on_mirrored_path():
    rng = np.random.default_rng(3)
    n = 4000
    p = 100 + np.cumsum(rng.normal(0, 0.05, n))
    hi, lo = p + 0.03, p - 0.03
    px = data.Prices(ts=T0 + np.arange(n) * MIN, open=p, high=hi, low=lo, close=p)
    m = 2 * p[11]
    px_m = data.Prices(ts=px.ts, open=m - p, high=m - lo, low=m - hi, close=m - p)
    a = R.simulate(R.make_market(px, []), 11, -1, 3000, [0.005, R.s_cat(20)], cat=R.s_cat(20))
    b = R.simulate(R.make_market(px_m, []), 11, 1, 3000, [0.005, R.s_cat(20)], cat=R.s_cat(20))
    for k in ("exit_bar", "kind", "hold", "t_mfe", "t_mae"):
        assert a[k] == b[k]
    for k in ("gross", "mfe", "mae"):
        assert a[k] == pytest.approx(b[k], abs=1e-12)


# ------------------------------------------------------------------ inference, controls


def test_bootstrap_constant_and_paired_zero_and_holm():
    bs = R.Bootstrap(30, reps=200)
    days = np.arange(30).repeat(2)
    boot = bs.ratio(days, np.full(60, 0.01))
    assert np.allclose(boot, 0.01)
    r = bs.infer(days, np.full(60, 0.01), ref=(days, np.full(60, 0.01)))
    assert r["mean"] == 0 and np.allclose(r["ci95"], 0)
    h = R.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert h == {"a": 0.03, "c": 0.06, "b": 0.06}


def test_cluster_inference_counts_clusters():
    r = R.cluster_infer([1, 1, 2, 2, 3], [0.1, 0.1, -0.1, -0.1, 0.0], reps=100)
    assert r["clusters"] == 3 and r["mean"] == pytest.approx(0.0)


def test_random_control_is_deterministic_and_respects_pool():
    n = 3 * 1440 + MAX_HOLD_MIN + 10
    mk = market(n)
    d = np.where(np.arange(n) % 2 == 0, 1, -1).astype(np.int8)
    st = {"D": d}
    raw = [v3.SetupV3(t=0, direction=1, family="F1", tag="x")]
    nxt = {1: next_true(d == -1), -1: next_true(d == 1)}
    counts = {(f, s): 3 for f in R.STUDIED for s in (1, -1)}
    a = R.random_control(mk, st, raw, nxt, counts)
    b = R.random_control(mk, st, raw, nxt, counts)
    assert a == b
    for fam, trades in a.items():
        assert len(trades) == 6
        for t in trades:
            assert d[t["t"]] == t["dir"] and t["t"] != 0
            assert t["t"] + 1 + MAX_HOLD_MIN <= mk.last


def test_shifted_control_rolls_states():
    rng = np.random.default_rng(1)
    n = 40 * 1440
    q = rng.integers(1, 1000, n) * 2
    b = np.clip(q // 2 + rng.integers(-300, 300, n), 0, q)
    vol = data.Volumes(ts=T0 + np.arange(n) * MIN, quote=q, taker=b, valid=np.ones(n, bool))
    st = v3.states_v3(vol)
    mk = market(n)
    out = R.shifted_control(mk, st, 7)
    sh = {k: np.roll(v, 7 * 1440) for k, v in st.items()}
    exp = [s for s in v3.detect_setups(sh)]
    assert sum(len(v) for v in out.values()) <= len(exp)
    for fam, trades in out.items():
        for t in trades:
            assert sh["D"][t["t"]] == t["dir"]


# ------------------------------------------------------------------ holdout guard, determinism


def test_price_and_funding_readers_refuse_outside_train(tmp_path, monkeypatch):
    log = data.ReadLog()
    with pytest.raises(data.HoldoutGuard):
        data.load_train_prices(log, months=("2023-07",))
    with pytest.raises(data.HoldoutGuard):
        data.load_train_funding(log, months=("2025-04",))
    path = tmp_path / "BTCUSDT-fundingRate-2021-02.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("f.csv", f"calc_time,funding_interval_hours,last_funding_rate\n"
                            f"{data.TRAIN_END_MS},8,0.0001\n")
    monkeypatch.setattr(data, "FUNDING", tmp_path)
    with pytest.raises(data.HoldoutGuard):
        data.load_train_funding(data.ReadLog(), months=("2021-02",))


def test_summary_deterministic_bytes():
    mk = market()
    bar(mk, 40, h=100.9, lo=99.2)
    tr = [R.simulate(mk, 11, d, 300, [0.005, R.s_cat(20)], cat=R.s_cat(20)) for d in (1, -1)]
    a = json.dumps(R.summarize(tr), sort_keys=True)
    b = json.dumps(R.summarize(tr), sort_keys=True)
    assert a == b
