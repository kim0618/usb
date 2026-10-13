"""PTM-R1 V3 Stage 0 tests (contract docs/crypto/personal_trading_method_r1/CONTRACT_PTM_R1_V3.md)."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data, stage0, stage0_v3, states, v3
from app.crypto.research.ptm_r1.states import NONE

MIN = 60_000
T0 = data.TRAIN_START_MS


def vols(quote, taker):
    quote = np.asarray(quote, dtype=np.int64)
    taker = np.asarray(taker, dtype=np.int64)
    n = len(quote)
    return data.Volumes(ts=T0 + np.arange(n, dtype=np.int64) * MIN, quote=quote, taker=taker,
                        valid=np.ones(n, bool))


def random_vols(n, seed=7, day_bias=None):
    rng = np.random.default_rng(seed)
    quote = rng.integers(1, 10_000, n) * 2
    taker = quote // 2 + rng.integers(-3_000, 3_000, n)
    if day_bias is not None:                              # slow drift so days have direction
        taker = taker + (np.sin(np.arange(n) / 1440 / day_bias) * 400).astype(np.int64)
    return vols(quote, np.clip(taker, 0, quote))


def _st(rows):
    a = np.array(rows, dtype=np.int8)
    return {"M": a[:, 0], "H1": a[:, 1], "H4": a[:, 2], "D": a[:, 3]}


# ------------------------------------------------------------------ states


@pytest.mark.parametrize("name,block", [("H1", 60), ("H4", 240), ("D", 1440)])
def test_completed_blocks_brute_force(name, block):
    v = random_vols(4 * 1440 + 333, seed=block)
    got = v3.states_v3(v)[name]
    for t in range(0, len(got), 41):
        done = (t + 1) // block
        if done == 0:
            assert got[t] == NONE
            continue
        w = slice((done - 1) * block, done * block)
        assert got[t] == np.sign((2 * v.taker[w] - v.quote[w]).sum())


def test_daily_thesis_changes_only_at_utc_midnight():
    v = random_vols(6 * 1440, seed=4)
    d = v3.states_v3(v)["D"]
    changes = np.flatnonzero(d[1:] != d[:-1]) + 1
    assert all((c + 1) % 1440 == 0 for c in changes)    # boundary index closes 23:59


def test_rolling_10m_is_v1_lean():
    v = random_vols(3_000, seed=8)
    m = v3.states_v3(v)["M"]
    for t in range(9, 3_000, 97):
        w = slice(t - 9, t + 1)
        assert m[t] == np.sign((2 * v.taker[w] - v.quote[w]).sum())


# ------------------------------------------------------------------ families


def test_family_detection_v3():
    L, S = 1, -1
    rows = [
        (L, L, L, L),   # 0 series start -> F1 THESIS_START
        (S, L, L, L),   # 1 10M dip
        (L, L, L, L),   # 2 -> F1 M_REALIGN
        (S, S, L, L),   # 3 1H against
        (L, L, L, L),   # 4 -> F2
        (L, L, S, L),   # 5 4H against
        (L, S, S, L),   # 6
        (L, L, L, L),   # 7 -> F3
        (L, L, L, S),   # 8 thesis flips
        (L, L, L, L),   # 9 -> F1 THESIS_START
        (L, L, 0, L),   # 10 4H EVEN only
        (L, L, L, L),   # 11 -> UNCLASSIFIED_EVEN
    ]
    got = [(s.t, s.direction, s.family, s.tag) for s in v3.detect_setups(_st(rows))]
    assert got == [(0, 1, "F1", "THESIS_START"), (2, 1, "F1", "M_REALIGN"), (4, 1, "F2", "H1_REALIGN"),
                   (7, 1, "F3", "H4_REALIGN"), (9, 1, "F1", "THESIS_START"),
                   (11, 1, "UNCLASSIFIED_EVEN", "H4_EVEN")]


def test_neutral_or_undefined_thesis_gives_no_setup():
    rows = [(1, 1, 1, 0), (1, 1, 1, NONE), (-1, -1, -1, 0)]
    assert v3.detect_setups(_st(rows)) == []


@pytest.mark.parametrize("seed", [1, 2])
def test_long_short_symmetry(seed):
    v = random_vols(5 * 1440, seed=seed, day_bias=0.7)
    a, b = v3.states_v3(v), v3.states_v3(vols(v.quote, v.quote - v.taker))
    sa, sb = v3.detect_setups(a), v3.detect_setups(b)
    assert sa
    assert [(s.t, -s.direction, s.family, s.tag) for s in sa] == \
        [(s.t, s.direction, s.family, s.tag) for s in sb]
    ca, cb = v3.census(a), v3.census(b)
    for f in v3.FAMILIES:
        assert ca["families"][f]["LONG"]["raw"] == cb["families"][f]["SHORT"]["raw"]
        assert ca["families"][f]["LONG"]["survival"] == cb["families"][f]["SHORT"]["survival"]


# ------------------------------------------------------------------ warnings, invalidation


def test_warning_masks():
    rows = [(1, 1, -1, 1), (1, -1, -1, 1), (-1, -1, -1, 1), (-1, -1, 1, 1)]
    w = v3.warning_masks(_st(rows), 1)
    assert list(w["W1"]) == [True, True, True, False]
    assert list(w["W2"]) == [False, True, True, False]
    assert list(w["W3"]) == [False, False, True, False]
    assert list(v3.warning_masks(_st(rows), -1)["W1"]) == [False, False, False, True]


def _hand(n_days=6, flip_at=None, h4_dip=None):
    """LONG thesis from day 0, setup at minute 1440+600 (F1 after a 10M dip)."""
    n = n_days * 1440
    one = np.ones(n, dtype=np.int8)
    st = {k: one.copy() for k in ("M", "H1", "H4", "D")}
    st["M"][:1440 + 600] = -1                         # first FULL onset is the setup below
    st["D"][:5] = 0                                    # so the thesis episode has a known start
    if flip_at is not None:
        st["D"][flip_at:] = -1
    if h4_dip is not None:
        st["H4"][h4_dip[0]:h4_dip[1]] = -1
    return st


def test_i1_timing_survival_and_warning_sequence():
    t = 1440 + 600
    st = _hand(flip_at=3 * 1440 - 1, h4_dip=(t + 60, t + 180))
    c = v3.census(st)
    f = c["families"]["F1"]["LONG"]
    assert f["minutes_to_I1"]["p50"] == (3 * 1440 - 1) - t
    surv = f["survival"]
    alive = {h: surv[h]["thesis_alive"] for h in ("4h", "12h", "24h", "48h")}
    assert alive["24h"] == 1 and alive["48h"] == 0      # I1 after ~39 h
    seq = c["families"]["F1"]["warning_sequence"]
    assert seq["W1_before_I1"] >= 1 and seq["I1_while_W1_active"] == 0
    exc = c["H4_excursions"]
    assert exc["returned_to_thesis_dir"] >= 1


def test_thesis_duration_from_episodes():
    d = np.array([1] * 3 + [-1] * 5 + [1] * 2 + [0] * 4 + [-1] * 3, dtype=np.int8)
    assert v3.episodes(d) == [(3, 8, -1), (8, 10, 1)]   # edge runs and EVEN run excluded


def test_train_end_censoring():
    st = _hand(n_days=3)                                # no flip; 48 h passes the series end
    c = v3.census(st)
    s = c["families"]["F1"]["LONG"]["survival"]
    t, last = 1440 + 600, 3 * 1440 - 1
    for h, cell in s.items():
        hm = int(h[:-1]) * 60
        assert cell["eligible"] == (1 if t + hm <= last else 0)


# ------------------------------------------------------------------ dedup / overlap N


def test_dedup_counts():
    ep = np.array([1] * 10_000)
    s = [v3.SetupV3(0, 1, "F1", "x"), v3.SetupV3(30, -1, "F1", "x"), v3.SetupV3(60, 1, "F1", "x"),
         v3.SetupV3(70, 1, "F2", "x"), v3.SetupV3(3000, 1, "F1", "x")]
    assert v3.dedup_primary(s, ep) == [True, True, False, True, True]
    assert v3.dedup_48h(s, per_family=True) == [True, False, False, True, True]
    assert v3.dedup_48h(s, per_family=False) == [True, False, False, False, True]


# ------------------------------------------------------------------ leak, holdout, price, output


def test_future_leak_zero_on_states_setups_warnings():
    v = random_vols(6 * 1440, seed=12, day_bias=0.5)
    r = stage0_v3.leak_check(v, days=6, cut=3 * 1440 + 517)
    assert r["pass"], r


def test_holdout_and_validation_files_refused(tmp_path):
    log = data.ReadLog()
    for name in ("BTCUSDT-1m-2023-07.zip", "BTCUSDT-1m-2024-12.zip", "BTCUSDT-1m-2025-04.zip"):
        with pytest.raises(data.HoldoutGuard):
            data._open_zip(tmp_path / name, "train", log)
    with pytest.raises(data.HoldoutGuard):
        data.to_volumes([(data.TRAIN_END_MS, "1", "0.5")], T0, data.TRAIN_END_MS)
    assert data.TRAIN_MONTHS[-1] == "2023-06"


def _zip(rows, garbage):
    buf = io.StringIO()
    buf.write("open_time,open,high,low,close,volume,close_time,quote_volume,count,"
              "taker_buy_volume,taker_buy_quote_volume,ignore\n")
    for t, q, b in rows:
        px = "NOT_A_PRICE" if garbage else "100.0"
        buf.write(f"{t},{px},{px},{px},{px},1,{t + 59_999},{q},5,0.5,{b},0\n")
    return buf.getvalue().splitlines()


def test_garbage_price_columns_give_identical_census():
    v = random_vols(4 * 1440, seed=31, day_bias=0.6)
    rows = [(int(t), f"{q // 10**5}.{q % 10**5:05d}", f"{b // 10**5}.{b % 10**5:05d}")
            for t, q, b in zip(v.ts, v.quote, v.taker)]
    end = T0 + len(rows) * MIN
    good = data.to_volumes(data.volume_rows(_zip(rows, False)), T0, end)
    bad = data.to_volumes(data.volume_rows(_zip(rows, True)), T0, end)
    a = json.dumps(v3.census(v3.states_v3(good)), sort_keys=True)
    b = json.dumps(v3.census(v3.states_v3(bad)), sort_keys=True)
    assert a == b


def test_no_performance_fields_and_detector():
    c = v3.census(v3.states_v3(random_vols(5 * 1440, seed=5, day_bias=0.5)))
    assert stage0.forbidden_keys(c, tokens=stage0_v3.FORBIDDEN_V3) == []
    bad = {"net": 1, "max_loss": 2, "gross_x": 3, "mae": 4, "profit_bp": 5}
    assert len(stage0.forbidden_keys(bad, tokens=stage0_v3.FORBIDDEN_V3)) == 5


def test_deterministic_byte_equal():
    v = random_vols(5 * 1440, seed=6, day_bias=0.4)
    a = json.dumps(v3.census(v3.states_v3(v)), sort_keys=True).encode()
    b = json.dumps(v3.census(v3.states_v3(v)), sort_keys=True).encode()
    assert a == b


def test_verdict_rules():
    def fake(med, s12, s24, s48, n48, w48):
        fam = {"survival_pooled": {"12h": {"alive_share": s12}, "24h": {"alive_share": s24},
                                   "48h": {"alive_share": s48}},
               "overlap_adjusted_48h_N": n48, "I1_within_48h_share_pooled": w48}
        return {"thesis": {"duration": {"p50": med}},
                "families": {"F1": fam, "F2": fam, "F3": fam}}
    J = v3.judge
    assert J(fake(2880, .6, .4, .1, 300, .8), True, True)["verdict"] == "D_V3_RESEARCHABLE"
    assert J(fake(1000, .6, .4, .1, 300, .8), True, True)["verdict"] == "A_V3_THESIS_TOO_FAST"
    assert J(fake(2880, .39, .4, .1, 300, .8), True, True)["verdict"] == "A_V3_THESIS_TOO_FAST"
    assert J(fake(2880, .6, .4, .04, 300, .8), True, True)["verdict"] == "A_V3_THESIS_TOO_FAST"
    assert J(fake(2880, .6, .4, .1, 99, .8), True, True)["verdict"] == "C_V3_FAMILY_TOO_RARE"
    assert J(fake(6000, .9, .9, .9, 300, .2), True, True)["verdict"] == "B_V3_THESIS_TOO_SLOW"
    assert J(fake(6000, .9, .9, .9, 300, .3), True, True)["verdict"] == "D_V3_RESEARCHABLE"
    assert J(fake(2880, .6, .4, .1, 300, .8), True, False)["verdict"] == "E_CONTRACT_OR_DATA_PROBLEM"
