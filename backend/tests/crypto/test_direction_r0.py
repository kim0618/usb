"""Direction Classifier R0 tests (contract docs/crypto/direction_classifier_r0/CONTRACT_DIRECTION_R0_V1.md)."""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.ptm_r1 import data as PD
from app.crypto.research.positioning_r0 import data as MD
from app.crypto.research.direction_r0 import features as F, folds as FD, metrics as M

N_MIN = 6 * 1440


def synth(seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, N_MIN)))
    px = PD.Prices(ts=PD.TRAIN_START_MS + np.arange(N_MIN, dtype=np.int64) * 60_000, open=np.concatenate(([100], c[:-1])),
                   high=c * 1.0005, low=c * 0.9995, close=c)
    q = rng.integers(10 ** 8, 10 ** 9, N_MIN)
    vol = PD.Volumes(ts=px.ts, quote=q, taker=q // 2 + rng.integers(-10 ** 7, 10 ** 7, N_MIN), valid=np.ones(N_MIN, bool))
    n5 = N_MIN // 5
    arr = {f: np.full(MD.N5, np.nan) for f in MD.FIELDS}
    for f in MD.FIELDS:
        arr[f][:n5] = 1 + rng.random(n5)
    met = MD.Metrics(arr=arr, present=~np.isnan(arr["oi"]))
    funding = [(PD.TRAIN_START_MS + k * 8 * 3_600_000, 1e-4 * k) for k in range(N_MIN // 480)]
    basis = rng.normal(0, 1e-4, N_MIN)
    return px, vol, met, funding, basis


def test_no_feature_uses_data_after_h():
    px, vol, met, funding, basis = synth()
    ds = F.build(px, vol, met, funding, basis)
    k = 10
    H = int(ds.hour[k])
    cut = H * 60                                           # first minute after H
    px2 = PD.Prices(ts=px.ts, open=px.open.copy(), high=px.high.copy(), low=px.low.copy(), close=px.close.copy())
    px2.close[cut:] *= 1.5
    px2.high[cut:] *= 1.5
    px2.low[cut:] *= 1.5
    met2 = MD.Metrics(arr={f: a.copy() for f, a in met.arr.items()}, present=met.present)
    for f in MD.FIELDS:
        met2.arr[f][H * 12:] *= 3.0                        # rows stamped at or after H
    basis2 = basis.copy()
    basis2[cut:] = 1.0
    funding2 = [(t, r if t <= PD.TRAIN_START_MS + H * 3_600_000 else 9.9) for t, r in funding]
    ds2 = F.build(px2, vol, met2, funding2, basis2)
    np.testing.assert_array_equal(np.nan_to_num(ds.X[k], nan=-7), np.nan_to_num(ds2.X[k], nan=-7))


def test_metrics_row_used_is_h_minus_5m_and_label_after_h():
    px, vol, met, funding, basis = synth()
    ds = F.build(px, vol, met, funding, basis)
    k = 5
    H = int(ds.hour[k])
    oi = met.arr["oi"]
    assert ds.X[k, F.FEATURES.index("oi_chg_1h")] == pytest.approx(oi[H * 12 - 1] / oi[H * 12 - 13] - 1)
    e = H * 60 + 1
    assert ds.label_end_min["4h"][k] == e + 240
    assert ds.y["4h"][k] == np.sign(px.open[e + 240] / px.open[e] - 1)


def test_funding_last_is_at_or_before_h():
    px, vol, met, funding, basis = synth()
    ds = F.build(px, vol, met, funding, basis)
    i = F.FEATURES.index("funding_last")
    for k in range(0, len(ds.hour), 7):
        h_ms = PD.TRAIN_START_MS + int(ds.hour[k]) * 3_600_000
        expect = [r for t, r in funding if t <= h_ms][-1]
        assert ds.X[k, i] == pytest.approx(expect)


def test_folds_purge_embargo_and_order():
    blocks = FD.test_blocks()
    assert len(blocks) == 7 and all(a < b for a, b in blocks)
    assert all(blocks[i][1] == blocks[i + 1][0] for i in range(6))
    entry = np.arange(0, blocks[-1][1], 60)
    lend = entry + 240
    for tr, te in FD.folds(entry, lend):
        a = entry[te].min()
        assert lend[tr].max() <= a - FD.EMBARGO_MIN
        assert entry[tr].max() < a


def test_auc_and_classify():
    y = np.array([1, 1, -1, -1])
    assert M.auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 1.0
    assert M.auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    p = np.array([0.7, 0.58, 0.5, 0.42, 0.3])
    assert list(M.classify(p, 0.6)) == [1, 0, 0, 0, -1]
    assert list(M.classify(p)) == [1, 1, 1, -1, -1]
    assert M.balanced_accuracy(np.array([1, 1, -1, -1]), np.array([1, -1, -1, -1])) == pytest.approx(0.75)


def test_report_thresholds_and_bootstrap():
    rng = np.random.default_rng(1)
    y = np.where(rng.random(400) < 0.5, 1, -1)
    p = rng.random(400)
    r = M.report(y, p)
    assert set(r["thresholds"]) == {"0.55", "0.6", "0.65"}
    assert r["thresholds"]["0.65"]["coverage"] <= r["thresholds"]["0.55"]["coverage"]
    boot = M.DayBlockBootstrap(np.repeat(np.arange(40), 10), reps=200)
    ci = boot.ci(lambda a: a.mean(), np.ones(400))
    assert ci["ci95"] == [1.0, 1.0]


def test_train_guard_on_readers():
    with pytest.raises(PD.HoldoutGuard):
        PD._open_zip(PD.MONTHLY / "BTCUSDT-1m-2023-07.zip", "train", PD.ReadLog())
    with pytest.raises(MD.HoldoutGuard):
        MD.parse([[MD.HEADER, "2023-07-01 00:00:00,BTCUSDT,1,1,1,1,1,1"]])


def test_feature_families_fixed():
    assert len(F.FEATURES) == 20 and len(F.FAMILIES) == 6
    assert F.HORIZONS == {"4h": 240, "1h": 60}
    assert M.THRESHOLDS == (0.55, 0.60, 0.65)


# --------------------------------------------------------------------------- validation guards


def test_validation_reader_refuses_holdout(tmp_path):
    from app.crypto.research.direction_r0 import val_data as VD
    assert VD.MONTHS[0] == "2021-02" and VD.MONTHS[-1] == "2025-03"
    assert VD.LAST_DAY.isoformat() == "2025-03-31"
    assert VD.VAL_END_MS == 1_743_465_600_000          # 2025-04-01T00:00Z
    with pytest.raises(VD.HoldoutGuard):
        VD._open_month(PD.MONTHLY / "BTCUSDT-1m-2025-04.zip", PD.ReadLog())
    with pytest.raises(VD.HoldoutGuard):
        VD._idx(VD.VAL_END_MS)
    assert VD._idx(VD.VAL_END_MS - 60_000) == VD.N_MIN - 1


def test_validation_models_frozen_parameters():
    import importlib.util
    if importlib.util.find_spec("sklearn") is None:
        pytest.skip("sklearn only in the system interpreter")
    from app.crypto.research.direction_r0 import models as MO
    rf = MO.make("RF", n_jobs=1)
    assert (rf.n_estimators, rf.max_depth, rf.min_samples_leaf, rf.random_state, rf.n_jobs) == (300, 6, 200, 20261013, 1)
    gb = MO.make("GB")
    assert (gb.max_iter, gb.learning_rate, gb.max_depth, gb.min_samples_leaf, gb.l2_regularization) == (200, 0.05, 3, 200, 1.0)
