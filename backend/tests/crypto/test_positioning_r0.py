"""Positioning R0 tests (contract docs/crypto/positioning_r0/CONTRACT_POSITIONING_R0_V1.md)."""
from __future__ import annotations

import json
import zipfile

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data as PD
from app.crypto.research.ptm_r1.states import NONE
from app.crypto.research.positioning_r0 import data as MD, stage0 as S0, stage1 as S1, states as S

H = MD.HEADER


def row(t: str, oi="100", g="1.5", ta="1.2", tp="1.1"):
    return f"{t},BTCUSDT,{oi},{float(oi) * 10},{ta},{tp},{g},0.9"


def test_dedup_identical_and_conflict_excluded():
    m = MD.parse([[H, row("2021-02-01 00:00:00"), row("2021-02-01 00:00:00"),
                   row("2021-02-01 00:05:00"), row("2021-02-01 00:05:00", oi="101")]])
    q = m.quality
    assert q["duplicate_identical"] == 1 and q["duplicate_conflicting_rows"] == 1
    assert q["conflicting_timestamps_excluded"] == 1
    assert m.present[0] and not m.present[1] and m.arr["oi"][0] == 100


def test_missing_not_interpolated_and_blank_values():
    m = MD.parse([[H, row("2021-02-01 00:00:00"), row("2021-02-01 00:15:00", ta='""', tp="")]])
    assert np.isnan(m.arr["oi"][1]) and np.isnan(m.arr["oi"][2]) and not m.present[1]
    assert m.present[3] and np.isnan(m.arr["ta"][3]) and np.isnan(m.arr["tp"][3]) and m.arr["g"][3] == 1.5
    assert m.quality["missing_slots"] == MD.N5 - 2 and m.quality["gaps"]["count"] >= 1


def test_train_boundary_guard(tmp_path):
    with pytest.raises(MD.HoldoutGuard):
        MD.parse([[H, row("2023-07-01 00:00:00")]])
    p = tmp_path / "BTCUSDT-metrics-2023-07-01.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("x.csv", H)
    with pytest.raises(MD.HoldoutGuard):
        MD.open_day(p, PD.ReadLog())


def test_reader_is_binance_only():
    assert "binance/futures/um/daily/metrics/BTCUSDT" in str(MD.METRICS)
    assert "bybit" not in str(MD.METRICS).lower() and "BTCUSDT/historical" not in str(MD.METRICS)
    assert "binance/futures/um" in str(PD.MONTHLY) and "binance/futures/um" in str(PD.FUNDING)


def test_oi_change_windows_and_ratio_states():
    oi = np.array([100.0, 110.0, 99.0, np.nan, 120.0])
    assert S.oi_change(oi, 1)[1] == pytest.approx(0.1) and np.isnan(S.oi_change(oi, 1)[0])
    assert np.isnan(S.oi_change(oi, 1)[3]) and S.oi_change(oi, 2)[2] == pytest.approx(-0.01)
    st = S.ratio_state(np.array([1.2, 0.8, 1.0, np.nan]))
    assert list(st) == [1, -1, 0, NONE]
    assert S.OI_WINDOWS == {"O1": 1, "O2": 12, "O3": 48, "O4": 288}


def _hourly(price, oi_dir, flow, G=None, TA=None, TP=None):
    n = len(price)
    f = lambda v: np.array(v if v is not None else [1] * n, dtype=np.int8)   # noqa: E731
    reg = np.full(n, -1, dtype=np.int8)
    p, o = np.array(price, dtype=np.int8), np.array(oi_dir, dtype=np.int8)
    reg[(p == 1) & (o == 1)], reg[(p == 1) & (o == -1)] = 0, 1
    reg[(p == -1) & (o == 1)], reg[(p == -1) & (o == -1)] = 2, 3
    return S.Hourly(h=np.arange(1, n + 1), j5=np.arange(1, n + 1) * 12 - 1, mclose=np.arange(1, n + 1) * 60 - 1,
                    O={}, G=f(G), TA=f(TA), TP=f(TP), price=p, ret1h=p * 0.01, flow=np.array(flow, dtype=np.int8),
                    oi_dir=o, regime=reg, raw={})


def test_price_oi_regimes_and_candidates():
    hr = _hourly(price=[1, 1, -1, -1, 1], oi_dir=[1, -1, 1, -1, 1], flow=[1, 1, -1, -1, 1])
    assert list(hr.regime) == [0, 1, 2, 3, 0]
    lo, sh = S.conditions(hr, "P1")
    assert list(lo) == [True, False, False, False, True] and list(sh) == [False, False, True, False, False]
    lo, sh = S.conditions(hr, "P3")                 # BUY + OI down -> SHORT ; SELL + OI down -> LONG
    assert list(sh) == [False, True, False, False, False] and list(lo) == [False, False, False, True, False]
    assert S.onsets(hr, "P1") == [(0, 1), (2, -1), (4, 1)]


def test_divergence_and_8state():
    hr = _hourly(price=[1] * 4, oi_dir=[1] * 4, flow=[1] * 4, G=[1, -1, 1, NONE], TA=[1, -1, -1, 1], TP=[-1, 1, 1, 1])
    lo, sh = S.conditions(hr, "P2")
    assert list(lo) == [False, True, False, False] and list(sh) == [True, False, False, False]
    lo, sh = S.conditions(hr, "P4")
    assert list(lo) == [False, True, True, False] and list(sh) == [True, False, False, False]
    c = S0.code8(hr)
    assert list(c) == [6, 1, 5, -1] and S0.label8(6) == "GL_TAL_TPS"


def test_dedup_4h_and_overlap_48h():
    ev = [(0, 1), (2, 1), (3, -1), (4, 1), (50, 1), (60, -1)]
    assert S.dedup_4h(ev) == [(0, 1), (3, -1), (4, 1), (50, 1), (60, -1)]
    assert S.overlap_48h(S.dedup_4h(ev)) == [(0, 1), (50, 1)]


def test_no_future_leak_in_timing():
    hr = _hourly(price=[1, 1], oi_dir=[1, 1], flow=[1, 1])
    k = 1                                           # boundary H = 2 h after TRAIN start
    assert hr.j5[k] * 300 + 300 <= hr.h[k] * 3600   # metrics stamp + 5 min <= H
    assert (hr.mclose[k] + 1) * 60 == hr.h[k] * 3600  # last price minute closes at H
    assert S.entry_minute(hr, k) * 60 == hr.h[k] * 3600 + 60   # entry one minute after H


def test_freeze_check(tmp_path, monkeypatch):
    st0 = tmp_path / "s0.json"
    st0.write_text(json.dumps({"selected": ["P1", "P3"]}))
    monkeypatch.setattr(S1, "STAGE0", st0)
    assert S1.check_freeze() == S1.frozen_from_contract() == ["P1", "P3"]
    st0.write_text(json.dumps({"selected": ["P1", "P2", "P3"]}))
    with pytest.raises(RuntimeError):
        S1.check_freeze()


def test_stage0_output_has_no_outcome_keys():
    assert S0.forbidden_keys({"a": {"net_mean": 1}}) == ["a.net_mean"]
    assert S0.forbidden_keys({"freq": {"A": 0.2}, "read_log": [{"x": "return"}]}) == []


def test_matched_control_pool_rules():
    n = 40
    hr = _hourly(price=[1, -1] * 20, oi_dir=[1] * n, flow=[1] * n)
    tab = {("1h", 1): {"net": np.arange(n, dtype=float)}, ("1h", -1): {"net": -np.arange(n, dtype=float)}}
    cond = np.zeros(n, dtype=bool)
    cond[0] = True
    month = np.array([1] * 20 + [2] * 20)
    out = S1.matched(tab, "1h", [(0, 1)], hr, cond, hr.price, None, month, seed=1)
    # same month (indices < 20), trade with the price move (even indices), not the event hour itself
    pool = [k for k in range(2, 20, 2)]
    assert min(pool) <= out[0] <= max(pool)


def test_bootstrap_cluster_and_overlap_n_present():
    from app.crypto.research.ptm_r1 import r1_train as R
    boot = R.Bootstrap(30, reps=200)
    days = np.repeat(np.arange(30), 2)
    r = boot.infer(days, np.ones(60))
    assert r["mean"] == 1.0
    assert R.cluster_infer(np.repeat(np.arange(10), 6), np.ones(60))["clusters"] == 10
