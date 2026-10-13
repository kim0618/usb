"""PTM-D1 tests (contract docs/crypto/personal_trading_method_discovery_v1/CONTRACT_PTM_D1_V1.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data, r1_train as R
from app.crypto.research.ptm_r1.states import NONE
from app.crypto.research.ptm_d1 import features as F, sim as S, study, entries as E

T0 = data.TRAIN_START_MS
MIN = 60_000
N = 6 * 1440


def prices(n=N, p=100.0):
    o = np.full(n, p)
    return data.Prices(ts=T0 + np.arange(n, dtype=np.int64) * MIN, open=o.copy(), high=o.copy(),
                       low=o.copy(), close=o.copy())


def set_bar(px, i, o=None, h=None, lo=None, c=None):
    o = px.open[i] if o is None else o
    c = o if c is None else c
    px.open[i], px.close[i] = o, c
    px.high[i] = max(o, c) if h is None else h
    px.low[i] = min(o, c) if lo is None else lo


def feats(px, D=1, H4=1, H1=1, M=1, V=0.01, PR=1, VR=1):
    n = len(px.open)
    st = {"D": np.full(n, D, np.int8), "H4": np.full(n, H4, np.int8), "H1": np.full(n, H1, np.int8),
          "M": np.full(n, M, np.int8)}
    d = st["D"]
    change = np.concatenate(([True], d[1:] != d[:-1]))
    starts = np.maximum.accumulate(np.where(change, np.arange(n), 0))
    return F.Features(st=st, V=np.full(n, V), z3=np.full(n, float(PR)), PR=np.full(n, PR, np.int8),
                      VR=np.full(n, VR, np.int8), pbz=np.zeros(n), d_start=starts, episode=np.cumsum(change),
                      fund=np.zeros(n), basis=np.zeros(n))


def ctx_for(px, **kw):
    f = feats(px, **kw)
    return S.Ctx(f=f, mk=R.make_market(px, [])), f


# --------------------------------------------------------------------------- features


def test_vol_unit_and_regimes():
    tr = np.array([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.5])
    v = F.vol_unit_by_day(tr)
    assert np.isnan(v[6]) and v[7] == pytest.approx(0.04) and v[8] == pytest.approx(0.05)
    vr = F.vol_regime_by_day(np.concatenate((np.full(30, 0.01), [0.02, 0.005])), days=30)
    assert vr[30] == 1 and vr[31] == 0 and vr[29] == NONE


def test_pullback_z_follows_episode_extreme():
    px = prices(10, 100.0)
    for i, p in enumerate([100, 110, 105, 100, 100, 90, 95, 100, 100, 100]):
        set_bar(px, i, o=p)
    d = np.array([1] * 5 + [-1] * 5, np.int8)
    V = np.full(10, 0.1)
    pb = F.pullback_z(px, d, V)
    assert pb[1] == pytest.approx(0.0) and pb[3] == pytest.approx((110 - 100) / 100 / 0.1)
    assert pb[6] == pytest.approx((95 - 90) / 95 / 0.1)


# --------------------------------------------------------------------------- fills and ordering


def test_level_hit_fills_at_level_or_gap_open():
    px = prices()
    set_bar(px, 105, lo=98.0)
    ctx, _ = ctx_for(px)
    p = S.make_path(ctx, 100, 1)
    k, fill = S.level_hit(p, 0.01)
    assert k == 5 and fill == pytest.approx(-0.01)
    set_bar(px, 105, o=97.0, lo=96.0)
    p = S.make_path(ctx, 100, 1)
    assert S.level_hit(p, 0.01)[1] == pytest.approx(-0.03)


def test_x1_exit_at_open_after_day_flip():
    px = prices()
    ctx, f = ctx_for(px)
    f.st["D"][2 * 1440 - 1:] = -1                       # completed day 2 is opposite
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P0", [])
    assert tr["exit_bar"] == 2 * 1440 and tr["kind"] == "THESIS"


def test_event_precedes_intrabar_stop_in_same_bar():
    px = prices()
    set_bar(px, 2 * 1440, lo=90.0)                       # stop would hit inside the exit bar
    ctx, f = ctx_for(px)
    f.st["D"][2 * 1440 - 1:] = -1
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P0", [("CATASTROPHIC", 0.0384)])
    assert tr["kind"] == "THESIS" and tr["gross"] == pytest.approx(0.0)


def test_x2_requires_h4_opposite_at_day_completion():
    px = prices()
    ctx, f = ctx_for(px)
    f.st["D"][2 * 1440 - 1:] = -1
    ctx.__post_init__()
    assert S.simulate(ctx, S.make_path(ctx, 100, 1), "X2", "P0", [])["kind"] == "CENSOR"
    f.st["H4"][2 * 1440 - 1:] = -1
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X2", "P0", [])
    assert tr["exit_bar"] == 2 * 1440 and tr["kind"] == "THESIS"


def test_x3_two_h4_blocks_opposite_at_loss_without_realign():
    px = prices()
    for i in range(101, N):
        set_bar(px, i, o=99.0)                           # at a loss from bar 101
    ctx, f = ctx_for(px)
    b1, b2 = 239 + 240 * 2, 239 + 240 * 3                # completed 4 h boundaries after entry 100
    f.st["H4"][b1:] = -1
    f.st["H1"][:] = -1
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X3", "P0", [])
    assert tr["exit_bar"] == b2 + 1 and tr["kind"] == "LOSS_EXIT"
    f.st["H1"][b1 + 60] = 1                              # H1 completion realigns between the blocks
    ctx.__post_init__()
    assert S.simulate(ctx, S.make_path(ctx, 100, 1), "X3", "P0", [])["exit_bar"] > b2 + 1
    # in profit: X3's H4 sequence does not act
    for i in range(101, N):
        set_bar(px, i, o=101.0)
    f.st["H1"][:] = -1
    ctx.__post_init__()
    assert S.simulate(ctx, S.make_path(ctx, 100, 1), "X3", "P0", [])["kind"] == "CENSOR"


def test_x4_adverse_expansion_after_failed_attempt():
    px = prices()                                        # V = 1 % -> attempt / expansion = 0.5 %
    for i in range(101, N):
        set_bar(px, i, o=99.5)                           # stays below entry
    set_bar(px, 110, lo=99.0)                            # adverse extreme -1 %
    set_bar(px, 120, h=99.6)                             # attempt: +0.6 % from the extreme, below entry
    set_bar(px, 130, lo=98.4)                            # expansion to -1.6 % (level -1.5 %)
    ctx, _ = ctx_for(px)
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X4", "P0", [])
    assert tr["exit_bar"] == 130 and tr["kind"] == "LOSS_EXIT" and tr["gross"] == pytest.approx(-0.015)
    set_bar(px, 125, h=100.0)                            # back at entry before the expansion -> reset
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X4", "P0", [])
    assert tr["kind"] == "CENSOR"


def test_x5_price_regime_flip_only_at_loss():
    px = prices()
    for i in range(101, N):
        set_bar(px, i, o=99.0)
    ctx, f = ctx_for(px)
    f.PR[300:] = -1
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X5", "P0", [])
    assert tr["exit_bar"] == 301 and tr["kind"] == "LOSS_EXIT"


def test_p3_trail_armed_at_one_v():
    px = prices()
    set_bar(px, 110, h=100.5)                            # MFE 0.5 % < 1 V: not armed
    set_bar(px, 120, lo=99.0)
    ctx, _ = ctx_for(px)
    assert S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P3", [])["kind"] == "CENSOR"
    set_bar(px, 130, h=102.0)                            # MFE 2 % -> armed, trail at 1 %
    for i in range(131, 140):
        set_bar(px, i, o=101.5)                          # above the trail level
    set_bar(px, 140, o=101.5, lo=100.5)
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P3", [])
    assert tr["exit_bar"] == 140 and tr["kind"] == "PROTECT" and tr["gross"] == pytest.approx(0.01)


def test_p1_h1_then_h4_without_new_mfe():
    px = prices()
    set_bar(px, 130, h=102.0)                            # armed at bar 130
    for i in range(131, N):
        set_bar(px, i, o=101.0)
    ctx, f = ctx_for(px)
    b1 = ((230 // 60) + 1) * 60 - 1                      # an H1 completion after arming
    f.st["H1"][b1:] = -1
    b2 = (b1 // 240 + 1) * 240 - 1                       # first H4 completion strictly after b1
    if b2 <= b1:
        b2 += 240
    f.st["H4"][b2:] = -1
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P1", [])
    assert tr["exit_bar"] == b2 + 1 and tr["kind"] == "PROTECT"
    set_bar(px, b2 - 10, h=103.0)                        # new MFE between H1 and H4 -> no exit at b2
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P1", [])
    assert tr["exit_bar"] != b2 + 1


def test_p2_mfe_stall_two_blocks():
    px = prices()
    set_bar(px, 130, h=102.0)
    for i in range(131, N):
        set_bar(px, i, o=101.0)
    ctx, f = ctx_for(px)
    f.st["H4"][:] = -1
    ctx.__post_init__()
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P2", [])
    k = tr["exit_bar"] - 100 - 1
    assert tr["kind"] == "PROTECT" and k >= 30 + 480 and (100 + k) % 240 == 239


def test_p4_realign_fail():
    px = prices()
    set_bar(px, 130, h=102.0)
    for i in range(131, N):
        set_bar(px, i, o=101.0)
    ctx, f = ctx_for(px)
    h4_opp_at = ((130 // 240) + 1) * 240 - 1
    f.st["H4"][h4_opp_at:] = -1
    f.st["H1"][:] = -1
    b_r = h4_opp_at + 60                                  # next H1 completion: H1 realigns
    f.st["H1"][b_r:b_r + 60] = 1
    ctx.__post_init__()                                  # H1 opposite again at b_r + 60
    tr = S.simulate(ctx, S.make_path(ctx, 100, 1), "X1", "P4", [])
    assert tr["exit_bar"] == b_r + 60 + 1 and tr["kind"] == "PROTECT"


def test_sequential_skips_while_busy_and_resim_flips():
    px = prices()
    ctx, f = ctx_for(px)
    f.st["D"][2 * 1440 - 1:] = -1
    ctx.__post_init__()
    ons = [(99, 1), (500, 1), (3000, 1)]
    tr = study.sequential(ctx, ons, "X1", "P0", [])
    assert [t["e"] for t in tr] == [100]                 # 500 is inside the open trade, 3000 hits the censor
    opp = study.mirror(ctx, tr)
    assert opp[0]["dir"] == -1 and opp[0]["exit_bar"] == tr[0]["exit_bar"]
    assert opp[0]["gross"] == pytest.approx(-tr[0]["gross"])


def test_entry_onsets_apply_no_trade():
    px = prices()
    f = feats(px, PR=-1)                                 # conflict: PR opposite to D
    assert E.onsets(f, "E1") == []
    f = feats(px)
    f.st["H1"][:] = -1
    f.st["H1"][1000:] = 1
    assert E.onsets(f, "E1") == [(1000, 1)]


def test_summary_and_gates():
    trades = [{"net": 0.02, "gross": 0.021, "mfe": 0.03, "mae": 0.0, "kind": "THESIS", "dir": 1, "giveback": 0.009,
               "giveback_ratio": 0.3, "recovered": False, "hold": 600, "funding": 0.0, "e": 100},
              {"net": -0.01, "gross": -0.009, "mfe": 0.0, "mae": 0.01, "kind": "LOSS_EXIT", "dir": -1,
               "giveback": 0.0, "giveback_ratio": None, "recovered": False, "hold": 60, "funding": 0.0, "e": 2000}]
    s = study.summarize(trades)
    assert s["n"] == 2 and s["pf"] == pytest.approx(2.0) and s["winner_loser_ratio"] == pytest.approx(2.0)
    assert s["mfe_capture"] == pytest.approx((0.021 - 0.009) / 2 / 0.015)


def test_readers_refuse_non_train():
    with pytest.raises(data.HoldoutGuard):
        data._open_zip(data.MONTHLY / "BTCUSDT-1m-2023-07.zip", "train", data.ReadLog())
