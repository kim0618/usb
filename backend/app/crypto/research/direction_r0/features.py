"""Features and labels on the hourly grid (contract sections 1 and 2). Pure numpy, no sklearn.

Minute index t = minute starting at TRAIN_START + t min; its close is known at the end of minute t.
Boundary H (hour i) = TRAIN_START + i h; the last minute closed at H is m = 60 i - 1.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ptm_r1 import data as PD
from ..positioning_r0 import data as MD

FAMILIES = {
    "A_price": ("ret_1h", "ret_4h", "ret_24h", "ret_72h"),
    "B_vol": ("rv_4h", "rv_24h", "range_24h"),
    "C_flow": ("flow_1h", "flow_4h", "flow_24h"),
    "D_oi": ("oi_chg_1h", "oi_chg_4h", "oi_chg_24h"),
    "E_positioning": ("log_g", "log_ta", "log_tp", "log_g_chg_24h"),
    "F_funding_basis": ("funding_last", "basis_now", "basis_mean_4h"),
}
FEATURES = tuple(f for fam in FAMILIES.values() for f in fam)
HORIZONS = {"4h": 240, "1h": 60}
MAX_LOOKBACK_MIN = 72 * 60


@dataclass
class Dataset:
    hour: np.ndarray        # i
    X: np.ndarray           # [n, 20]
    y: dict                 # horizon -> int8 (+1 LONG, -1 SHORT, 0 dropped)
    label_end_min: dict     # horizon -> minute index of the exit open
    day: np.ndarray


def _trailing_sum(x: np.ndarray, w: int) -> np.ndarray:
    c = np.concatenate(([0.0], np.cumsum(x, dtype=float)))
    out = np.full(len(x), np.nan)
    out[w - 1:] = c[w:] - c[:-w]
    return out


def _trailing_max(x, w):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    out = np.full(len(x), np.nan)
    out[w - 1:] = sw(x, w).max(axis=1)
    return out


def _trailing_min(x, w):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    out = np.full(len(x), np.nan)
    out[w - 1:] = sw(x, w).min(axis=1)
    return out


def build(px: PD.Prices, vol, metrics: MD.Metrics, funding: list[tuple[int, float]], basis: np.ndarray) -> Dataset:
    n_min = len(px.close)
    lc = np.log(px.close)
    r1 = np.concatenate(([np.nan], np.diff(lc)))
    r1sq = np.where(np.isnan(r1), 0.0, r1 ** 2)
    quote = vol.quote.astype(float)
    net = (2 * vol.taker - vol.quote).astype(float)
    hours = np.arange(MAX_LOOKBACK_MIN // 60 + 1, n_min // 60)
    m = hours * 60 - 1                                   # last closed minute at H
    feats: dict[str, np.ndarray] = {}
    for name, w in (("ret_1h", 60), ("ret_4h", 240), ("ret_24h", 1440), ("ret_72h", 4320)):
        feats[name] = lc[m] - lc[m - w]
    for name, w in (("rv_4h", 240), ("rv_24h", 1440)):
        feats[name] = np.sqrt(_trailing_sum(r1sq, w)[m])
    feats["range_24h"] = (_trailing_max(px.high, 1440)[m] - _trailing_min(px.low, 1440)[m]) / px.close[m]
    for name, w in (("flow_1h", 60), ("flow_4h", 240), ("flow_24h", 1440)):
        feats[name] = _trailing_sum(net, w)[m] / _trailing_sum(quote, w)[m]
    j5 = hours * 12 - 1                                  # metrics row stamped H - 5 min
    oi = metrics.arr["oi"]
    for name, k in (("oi_chg_1h", 12), ("oi_chg_4h", 48), ("oi_chg_24h", 288)):
        feats[name] = oi[j5] / oi[j5 - k] - 1.0
    lg = np.log(metrics.arr["g"])
    feats["log_g"] = lg[j5]
    feats["log_ta"] = np.log(metrics.arr["ta"])[j5]
    feats["log_tp"] = np.log(metrics.arr["tp"])[j5]
    feats["log_g_chg_24h"] = lg[j5] - lg[j5 - 288]
    f_ms = np.array([t for t, _ in funding], dtype=np.int64)
    f_rate = np.array([r for _, r in funding])
    h_ms = PD.TRAIN_START_MS + hours.astype(np.int64) * 3_600_000
    pos = np.searchsorted(f_ms, h_ms, side="right") - 1                       # calc_time <= H
    feats["funding_last"] = np.where(pos >= 0, f_rate[np.maximum(pos, 0)], np.nan)
    feats["basis_now"] = basis[m]
    bz = np.where(np.isnan(basis), 0.0, basis)
    cnt = _trailing_sum((~np.isnan(basis)).astype(float), 240)[m]
    feats["basis_mean_4h"] = np.where(cnt > 0, _trailing_sum(bz, 240)[m] / np.maximum(cnt, 1), np.nan)
    X = np.column_stack([feats[f] for f in FEATURES])
    X[~np.isfinite(X)] = np.nan
    y, lend = {}, {}
    e = hours * 60 + 1
    for hn, hm in HORIZONS.items():
        ok = e + hm < n_min
        ret = np.full(len(hours), np.nan)
        ret[ok] = px.open[e[ok] + hm] / px.open[e[ok]] - 1.0
        y[hn] = np.where(np.isnan(ret), 0, np.sign(ret)).astype(np.int8)
        lend[hn] = e + hm
    return Dataset(hour=hours, X=X, y=y, label_end_min=lend, day=(hours * 60) // 1440)
