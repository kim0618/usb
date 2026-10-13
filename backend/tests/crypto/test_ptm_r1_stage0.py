"""PTM-R1 Stage 0 tests.

Contract: docs/crypto/personal_trading_method_r1/CONTRACT_PTM_R1_V1.md
Stage 0 never parses a price and never reads beyond TRAIN except the volume-only parity files.
"""
from __future__ import annotations

import zipfile
from decimal import Decimal

import numpy as np
import pytest

from app.crypto.btc_context_ui_v1 import flow_horizon
from app.crypto.research.ptm_r1 import data, stage0, states
from app.crypto.research.ptm_r1.states import NONE, Setup

MIN = 60_000
T0 = data.TRAIN_START_MS


def vols(quote, taker, valid=None) -> data.Volumes:
    quote = np.asarray(quote, dtype=np.int64)
    taker = np.asarray(taker, dtype=np.int64)
    n = len(quote)
    return data.Volumes(ts=T0 + np.arange(n, dtype=np.int64) * MIN, quote=quote, taker=taker,
                        valid=np.ones(n, bool) if valid is None else np.asarray(valid, bool))


def random_vols(n, seed=7, ties=False):
    rng = np.random.default_rng(seed)
    quote = rng.integers(1, 10_000, n) * 2
    taker = quote // 2 + (0 if ties else 1) * rng.integers(-3_000, 3_000, n)
    taker = np.clip(taker, 0, quote)
    return vols(quote, taker)


# ---------------------------------------------------------------- aggregation = production


@pytest.mark.parametrize("minutes", [10, 60, 240, 1440])
def test_window_lean_matches_production_horizon_view(minutes):
    v = random_vols(3_000, seed=minutes)
    got = states.lean(v, minutes)
    rows = {int(t): (int(t), str(Decimal(int(q)) / 10 ** 5), str(Decimal(int(b)) / 10 ** 5))
            for t, q, b in zip(v.ts, v.quote, v.taker)}
    code = {"BUY": 1, "SELL": -1, "EVEN": 0, None: NONE}
    for t in [minutes - 2, minutes - 1, minutes, 1_500, 2_999]:
        end_ms = int(v.ts[t]) + MIN
        prod = flow_horizon.horizon_view(rows, end_ms, minutes)
        assert got[t] == code[prod["lean"]], (minutes, t)


@pytest.mark.parametrize("minutes", [10, 60, 240, 1440])
def test_window_lean_brute_force(minutes):
    v = random_vols(2_000, seed=minutes + 1)
    got = states.lean(v, minutes)
    for t in range(0, 2_000, 37):
        if t < minutes - 1:
            assert got[t] == NONE
            continue
        w = slice(t - minutes + 1, t + 1)
        buy = v.taker[w].sum()
        sell = (v.quote[w] - v.taker[w]).sum()
        assert got[t] == (1 if buy > sell else -1 if sell > buy else 0)


def test_exact_tie_is_even_and_missing_minute_is_none():
    v = vols([10, 10, 10], [5, 5, 5])
    assert list(states.lean(v, 2)) == [NONE, 0, 0]
    v = vols([10, 10, 10], [6, 5, 5], valid=[True, False, True])
    assert list(states.lean(v, 2)) == [NONE, NONE, NONE]
    v = vols([0, 0, 10], [0, 0, 6])
    assert list(states.lean(v, 2)) == [NONE, NONE, 1]


def test_fixed_point_parsing_is_exact():
    assert data.fixed("17151496.91820") == 1_715_149_691_820
    assert data.fixed("5") == 500_000
    with pytest.raises(ValueError):
        data.fixed("1.123456")


# ---------------------------------------------------------------- parity (real data)


@pytest.mark.skipif(not data.REST_COPY.exists() or not data.DAILY.exists(),
                    reason="archive not present")
def test_overlap_parity_31680_zero_diff():
    p = data.parity(data.ReadLog())
    assert p["overlap_minutes"] == 31_680
    assert p["differences"] == 0
    assert p["pass"]


# ---------------------------------------------------------------- no future leak, determinism


def test_no_future_leak():
    v = random_vols(4_000, seed=3)
    base = states.all_states(v)
    cut = 2_500
    q2, b2 = v.quote.copy(), v.taker.copy()
    b2[cut + 1:] = q2[cut + 1:]                       # every later minute all-buy
    after = states.all_states(vols(q2, b2))
    for k in base:
        assert np.array_equal(base[k][:cut + 1], after[k][:cut + 1])
    s_base = [s for s in states.detect_setups(base) if s.t <= cut]
    s_after = [s for s in states.detect_setups(after) if s.t <= cut]
    assert s_base == s_after


def test_deterministic_census():
    v = random_vols(6_000, seed=11)
    assert stage0.census(v) == stage0.census(v)


# ---------------------------------------------------------------- LONG/SHORT symmetry


def test_long_short_symmetry_by_mirroring_aggressor():
    v = random_vols(8_000, seed=5)
    m = vols(v.quote, v.quote - v.taker)              # buy <-> sell
    a, b = states.all_states(v), states.all_states(m)
    for k in a:
        defined = a[k] != NONE
        assert np.array_equal(a[k][defined], -b[k][defined])
        assert np.array_equal(defined, b[k] != NONE)
    sa = states.detect_setups(a)
    sb = states.detect_setups(b)
    assert [(s.t, -s.direction, s.family) for s in sa] == [(s.t, s.direction, s.family) for s in sb]


# ---------------------------------------------------------------- flips, families, dedup


def test_flip_detection():
    s = np.array([NONE, 1, 1, -1, 0, -1, 1, 1], dtype=np.int8)
    f = stage0._flips(s, days=1.0)
    assert f["changes"] == 4                            # 1>-1, -1>0, 0>-1, -1>1
    assert f["buy_sell_direct"] == 2


def _st(rows):
    """rows: list of (M, H1, H4, D) per minute."""
    a = np.array(rows, dtype=np.int8)
    return {"M": a[:, 0], "H1": a[:, 1], "H4": a[:, 2], "D": a[:, 3]}


def test_family_detection():
    L = 1
    rows = [
        (L, L, L, L),          # 0 start: F1 (series start)
        (-1, L, L, L),         # 1 M pullback
        (L, L, L, L),          # 2 -> F2
        (-1, -1, L, L),        # 3 H1 opposite
        (L, -1, L, L),         # 4
        (L, L, L, L),          # 5 -> F3
        (L, L, -1, L),         # 6 big direction off
        (L, L, L, L),          # 7 -> F1
        (0, L, L, L),          # 8 M only EVEN
        (L, L, L, L),          # 9 -> UNCLASSIFIED_EVEN
        (L, 0, L, L),          # 10 H1 only EVEN
        (L, L, L, L),          # 11 -> UNCLASSIFIED_EVEN
    ]
    got = [(s.t, s.direction, s.family) for s in states.detect_setups(_st(rows))]
    assert got == [(0, 1, "F1"), (2, 1, "F2"), (5, 1, "F3"), (7, 1, "F1"),
                   (9, 1, "UNCLASSIFIED_EVEN"), (11, 1, "UNCLASSIFIED_EVEN")]


def test_short_family_detection_and_no_trade():
    S = -1
    rows = [(S, S, S, S), (1, S, S, S), (S, S, S, S), (S, S, 1, S), (1, 1, 1, S)]
    got = [(s.t, s.direction, s.family) for s in states.detect_setups(_st(rows))]
    assert got == [(0, -1, "F1"), (2, -1, "F2")]
    bd = states.big_direction(_st(rows))
    assert list(bd) == [-1, -1, -1, 0, 0]               # D != H4 is NO_TRADE


def test_episode_dedup():
    regime = np.array([1] * 6000 + [2] * 100)
    s = [Setup(0, 1, "F2"), Setup(100, 1, "F2"), Setup(200, 1, "F1"), Setup(3000, 1, "F2"),
         Setup(6010, 1, "F2")]
    assert states.dedup_primary(s, regime) == [True, False, True, True, True]
    assert states.dedup_one_position(s) == [True, False, False, True, True]


def test_invalidation_masks_and_next_true():
    rows = [(1, 1, 1, 1), (1, -1, 1, 1), (1, -1, -1, 1), (1, 1, 1, -1)]
    mk = states.invalidation_masks(_st(rows), 1)
    assert list(mk["I1"]) == [False, True, True, False]
    assert list(mk["I2"]) == [False, False, True, False]
    assert list(mk["I3"]) == [False, False, True, False]
    assert list(mk["I4"]) == [False, False, True, True]
    assert list(states.next_true(mk["I2"])) == [2, 2, 2, 4]


# ---------------------------------------------------------------- holdout protection


def test_train_constants_and_months():
    assert data.TRAIN_MONTHS[0] == "2021-02" and data.TRAIN_MONTHS[-1] == "2023-06"
    assert len(data.TRAIN_MONTHS) == 29
    assert data.TRAIN_END_MS == 1_688_169_600_000       # 2023-07-01T00:00Z


def test_guard_refuses_validation_and_holdout_files(tmp_path):
    log = data.ReadLog()
    for name in ("BTCUSDT-1m-2023-07.zip", "BTCUSDT-1m-2025-04.zip", "BTCUSDT-1m-2026-09-23.zip"):
        with pytest.raises(data.HoldoutGuard):
            data._open_zip(tmp_path / name, "train", log)
    with pytest.raises(data.HoldoutGuard):
        data._open_zip(tmp_path / "BTCUSDT-1m-2025-04.zip", "parity", log)
    assert log.entries == []


def test_rows_after_train_end_are_refused():
    with pytest.raises(data.HoldoutGuard):
        data.to_volumes([(data.TRAIN_END_MS, "1", "0.5")], T0, data.TRAIN_END_MS)


def test_price_columns_are_never_parsed(tmp_path, monkeypatch):
    path = tmp_path / "BTCUSDT-1m-2021-02.zip"
    line = f"{T0},NOT_A_PRICE,x,y,z,1.0,{T0 + 59_999},100.5,10,0.6,60.25,0"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("k.csv", "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                            "taker_buy_volume,taker_buy_quote_volume,ignore\n" + line + "\n")
    monkeypatch.setattr(data, "MONTHLY", tmp_path)
    log = data.ReadLog()
    rows = data.volume_rows(data._open_zip(path, "train", log))
    assert rows == [(T0, "100.5", "60.25")]
    assert [e["role"] for e in log.entries] == ["train"]


def test_stage0_reads_only_train_and_parity(monkeypatch):
    opened = []
    real = data._open_zip

    def spy(path, role, log):
        opened.append((path.name, role))
        raise StopIteration                              # stop before any heavy work

    monkeypatch.setattr(data, "_open_zip", spy)
    with pytest.raises(StopIteration):
        data.load_train(data.ReadLog())
    assert opened == [("BTCUSDT-1m-2021-02.zip", "train")]
    assert real is not spy


# ---------------------------------------------------------------- no performance fields


def test_census_output_has_no_performance_fields():
    out = stage0.census(random_vols(6_000, seed=21))
    assert stage0.forbidden_keys(out) == []


def test_forbidden_key_detector_catches_performance_names():
    bad = {"a": {"mfe_bp": 1, "net_pnl": 2, "win_rate": 3, "pf": 4, "gross_return": 5}}
    assert len(stage0.forbidden_keys(bad)) == 5
