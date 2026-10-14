"""C1 Binance venue recheck V1: adapter, matching and accounting guards (fast; no full-window run)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research import dataset
from app.crypto.research.c1_binance_recheck import data as BD
from app.crypto.research.c1_binance_recheck import recheck as RC
from app.crypto.research.c1_exit import e1
from app.crypto.research.d6c import execution as X

ROOT = Path(__file__).resolve().parents[3]
HAVE_ARCHIVE = (ROOT / BD.BIN).exists() or BD.BIN.exists()
needs_data = pytest.mark.skipif(not BD.CACHE.exists(), reason="Binance grid cache not built")


def test_match_is_one_to_one_and_nearest_first():
    a = np.array([10, 20, 30]); b = np.array([10, 21, 22, 40])
    assert RC.match(a, b, 0) == [(0, 0)]
    assert RC.match(a, b, 1) == [(0, 0), (1, 1)]
    m = RC.match(a, b, 10)
    assert len({i for i, _ in m}) == len(m) == len({j for _, j in m}) == 3
    assert (1, 1) in m and (2, 2) in m


def test_parity_class_thresholds():
    ts = np.arange(100, dtype=np.int64)
    c = np.zeros(100, bool); c[[5, 6, 30, 60]] = True
    same = RC.parity_table(c, c.copy(), ts, 0, 100)
    assert same["class"] == "EXACT_SAME" and same["bar_jaccard"] == 1.0
    sh = np.roll(c, 2)
    assert RC.parity_table(c, sh, ts, 0, 100)["class"] == "TIME_SHIFTED_SAME"
    other = np.zeros(100, bool); other[[90]] = True
    assert RC.parity_table(c, other, ts, 0, 100)["class"] == "VENUE_DIVERGENT"
    assert same["bybit_short"] == same["binance_short"] == same["direction_disagreement"] == 0


def test_c1_generic_uses_frozen_cutoff_and_strict_oi():
    n = 1440 * 32
    ts = dataset.RESEARCH_START_MS + np.arange(n, dtype=np.int64) * 60_000
    rng = np.random.default_rng(1)
    close = 30000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    oi = np.full(n, 100.0)                                   # flat OI: change == 0 is never C1
    g = {"ts": ts, "close": close, "oi": oi}
    B = np.arange(ts[0] + 300_000, ts[-1] + 60_001, 300_000, dtype=np.int64)
    st = RC.c1_generic(g, np.full(len(B), 1.0), np.full(len(B), 1.0), B)
    assert not st["c1"].any()
    assert RC.HIGH_CUT == e1.D52_VOL_CUTOFFS[1]


def test_fee_swap_is_scoped_and_only_taker():
    before = X.FEE_VERIFICATION
    with RC.fee(RC.BINANCE_TAKER):
        adopted = json.loads(X.FEE_VERIFICATION.read_text())["adopted"]
        assert adopted["taker_rate"] == "0.0005" and adopted["maker_rate"] == "0.0002"
    assert X.FEE_VERIFICATION == before
    with RC.fee(RC.BYBIT_TAKER):
        assert json.loads(X.FEE_VERIFICATION.read_text())["adopted"]["taker_rate"] == "0.00055"


@needs_data
def test_binance_grid_alignment_and_no_bybit_source():
    BD.SOURCES_READ.clear()
    g, qc = BD.load()
    assert np.array_equal(g["ts"], np.arange(dataset.RESEARCH_START_MS, dataset.RESEARCH_END_MS, 60_000))
    assert qc["perp_1m_missing"] == 0
    assert np.all(np.diff(g["funding_ts"]) > 0) and np.all(g["funding_ts"] % (8 * 3_600_000) == 0)
    assert int(g["funding_ts"].max()) < BD.FUNDING_END_MS
    for s in BD.SOURCES_READ:
        assert "BTCUSDT/historical" not in s


def test_source_reader_never_touches_bybit_tree(monkeypatch):
    BD.SOURCES_READ.clear()
    ft, fr, iv = BD.funding()
    assert set(iv.tolist()) == {8}
    assert all("d5_2/archive/binance" in s for s in BD.SOURCES_READ)
    assert not any("historical" in s for s in BD.SOURCES_READ)
    assert np.all(ft % 60_000 == 0)                        # calc_time +ms normalised to the minute


def test_oi_schema_is_btc_and_known_five_minutes_late():
    ot, ov = BD.open_interest()
    assert np.all(np.diff(ot) > 0) and np.all(ot % 300_000 == 0)
    assert 1e3 < np.median(ov) < 1e6                       # BTC units, not USDT value
    close_ms = np.array([ot[100] + 5 * 60_000 - 1, ot[100] + 5 * 60_000])
    v = dataset._asof(ot, ov, ot + dataset.OI_DELAY_MS, close_ms)
    assert v[0] == ov[99] and v[1] == ov[100]


@needs_data
def test_entry_timing_mfe_mae_and_stop_touch_on_binance_grid():
    g, _ = BD.load()
    ex = e1.Executor(g)
    e = int(np.searchsorted(g["ts"], e1.ms("2023-03-01T00:00:00Z")))
    with RC.fee(RC.BINANCE_TAKER):
        s = e1.Executor(g).run(e, e + 240)
    assert s.entry_ref == g["open"][e] and s.exit_ref == g["open"][e + 240]
    assert s.identity_ok
    assert abs(s.fee_bp - 5.0 * (1 + s.exit_fill / s.entry_fill)) < 0.05      # 0.05 % on both legs
    A, cum_at = RC.arrays(g, {"s1_bucket": np.zeros(len(g["ts"]), np.int8), "oi_chg": np.zeros(len(g["ts"])),
                              "rv24h": np.zeros(len(g["ts"])), "s1": np.zeros(len(g["ts"]))})
    rows = RC.fixed_rows([{"signal_id": 0, "t": e - 1, "e": e, "entry_ts": int(g["ts"][e]), "fill": s.entry_fill,
                           "sig": 0.01, "fold": 1}], A, ex, 240, "E0")
    hi, lo = g["high"][e:e + 240], g["low"][e:e + 240]
    assert abs(rows[0]["mfe_before_bp"] - (hi.max() / g["open"][e] - 1) * 1e4) < 1e-9
    assert abs(rows[0]["mae_before_bp"] - (lo.min() / g["open"][e] - 1) * 1e4) < 1e-9
    from app.crypto.research.c1_exit import e2
    w = e2.window(A, e)
    j, ref, k = e2.trig_b1(w, s.entry_fill, 0.0001)        # floor stop 1 %
    stop = s.entry_fill * 0.99
    hit = np.nonzero(w["mark_low"][:e2.MAX_HOLD] <= stop)[0]
    assert (k is None) == (len(hit) == 0 and w["open"][e2.MAX_HOLD] > stop)


@needs_data
def test_funding_only_settlements_inside_hold():
    g, _ = BD.load()
    t0 = int(np.searchsorted(g["ts"], e1.ms("2024-01-01T07:00:00Z")))   # entry 07:00, 4H hold spans 08:00
    from app.crypto.research import features as F
    T = F.targets(g, 240)
    j = int(np.searchsorted(g["funding_ts"], e1.ms("2024-01-01T08:00:00Z")))
    assert abs(T["fund_long"][t0 - 1] - g["funding_rate"][j]) < 1e-15
    t2 = int(np.searchsorted(g["ts"], e1.ms("2024-01-01T08:01:00Z")))   # entry after settlement, exit 12:01: none
    assert T["fund_long"][t2 - 1] == 0.0
