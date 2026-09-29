"""The 18 frozen D5 features, bucket normalisation, forward targets and regime labels.

Formulas are copied from CRYPTO_D5_EDGE_DISCOVERY_CONTRACT_V1 sections 4-6 and 10. Every feature
value at row t uses rows <= t only (bar t closed); every target starts at open[t+1].
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .dataset import MINUTE_MS

DAY_BARS = 1440
NORM_DAYS = 30
BUCKET_QUANTILES = (0.10, 0.30, 0.70, 0.90)
HORIZONS = (1, 5, 15, 30)
PRIMARY_HORIZONS = (5, 15, 30)

FEATURES = (
    ("A1", "trend_ret_60"), ("A2", "trend_ret_240"), ("A3", "trend_dist_sma1440"),
    ("B1", "mom_ret_1"), ("B2", "mom_ret_5"), ("B3", "mom_ret_15"),
    ("C1", "vol_rvol_15"), ("C2", "vol_rvol_60"), ("C3", "vol_signed_flow_15"),
    ("D1", "oi_chg_60"), ("D2", "oi_chg_240"), ("D3", "oi_chg_1440"),
    ("E1", "fund_last"), ("E2", "fund_sum_3"), ("E3", "fund_premium"),
    ("F1", "vola_rv_60"), ("F2", "vola_rv_ratio"), ("F3", "vola_range_15"),
)


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


def _rsum(x: np.ndarray, w: int) -> np.ndarray:
    """Trailing sum over rows t-w+1..t; NaN until a full window exists."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    out[w - 1:] = c[w:] - c[:-w]
    return out


def _rstd(x: np.ndarray, w: int) -> np.ndarray:
    x0 = np.nan_to_num(x)
    valid = _rsum(np.isfinite(x).astype(float), w) == w
    m = _rsum(x0, w) / w
    v = _rsum(x0 * x0, w) / w - m * m
    out = np.sqrt(np.clip(v, 0, None))
    out[~valid] = np.nan
    return out


def _rext(x: np.ndarray, w: int, fn) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[w - 1:] = fn(sliding_window_view(x, w), axis=1)
    return out


def _div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = a / b
    out[~np.isfinite(out)] = np.nan
    return out


def compute_features(g: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    C, O, H, L, TO = g["close"], g["open"], g["high"], g["low"], g["turnover"]
    OI, IDX = g["oi"], g["index_close"]
    lnC = np.log(C)
    r1 = lnC - _lag(lnC, 1)
    lnOI = np.log(OI)
    to_day = _rsum(TO, DAY_BARS)
    signed = np.sign(C - O) * TO
    to15 = _rsum(TO, 15)
    close_ms = g["ts"] + MINUTE_MS
    fidx = np.searchsorted(g["funding_ts"], close_ms, side="right") - 1
    fcum = np.concatenate([[0.0], np.cumsum(g["funding_rate"])])
    fsum3 = np.where(fidx >= 2, fcum[np.maximum(fidx + 1, 0)] - fcum[np.maximum(fidx - 2, 0)], np.nan)
    rv60 = _rstd(r1, 60)
    f = {
        "trend_ret_60": lnC - _lag(lnC, 60),
        "trend_ret_240": lnC - _lag(lnC, 240),
        "trend_dist_sma1440": lnC - np.log(_rsum(C, DAY_BARS) / DAY_BARS),
        "mom_ret_1": r1,
        "mom_ret_5": lnC - _lag(lnC, 5),
        "mom_ret_15": lnC - _lag(lnC, 15),
        "vol_rvol_15": _div(to15, to_day * 15 / DAY_BARS),
        "vol_rvol_60": _div(_rsum(TO, 60), to_day * 60 / DAY_BARS),
        "vol_signed_flow_15": _div(_rsum(signed, 15), to15),
        "oi_chg_60": lnOI - _lag(lnOI, 60),
        "oi_chg_240": lnOI - _lag(lnOI, 240),
        "oi_chg_1440": lnOI - _lag(lnOI, DAY_BARS),
        "fund_last": g["funding_last"].astype(float),
        "fund_sum_3": fsum3,
        "fund_premium": lnC - np.log(IDX),
        "vola_rv_60": rv60,
        "vola_rv_ratio": _div(rv60, _rstd(r1, DAY_BARS)),
        "vola_range_15": np.log(_rext(H, 15, np.max) / _rext(L, 15, np.min)),
    }
    for k, v in f.items():
        v[~np.isfinite(v)] = np.nan
    return f


def bucketize(x: np.ndarray, n_days: int) -> np.ndarray:
    """Contract section 5: per UTC day, cutoffs from the previous 30 days only. -1 = no bucket."""
    out = np.full(len(x), -1, dtype=np.int8)
    win = NORM_DAYS * DAY_BARS
    for d in range(NORM_DAYS, n_days):
        hist = x[(d - NORM_DAYS) * DAY_BARS: d * DAY_BARS]
        hist = hist[np.isfinite(hist)]
        if len(hist) < win * 0.5:
            continue
        cut = np.quantile(hist, BUCKET_QUANTILES)
        seg = x[d * DAY_BARS: (d + 1) * DAY_BARS]
        b = np.searchsorted(cut, seg, side="right").astype(np.int8)
        b[~np.isfinite(seg)] = -1
        out[d * DAY_BARS: d * DAY_BARS + len(seg)] = b
    return out


def targets(g: dict[str, np.ndarray], h: int) -> dict[str, np.ndarray]:
    """Forward outcome of a decision at the close of bar t: enter open[t+1], exit open[t+1+h]."""
    O, H, L = g["open"], g["high"], g["low"]
    n = len(O)
    E = np.full(n, np.nan)
    X = np.full(n, np.nan)
    E[: n - 1 - h] = O[1: n - h]
    X[: n - 1 - h] = O[1 + h:]
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    hi[: n - h] = np.max(sliding_window_view(H[1:], h), axis=1)[: n - h]
    lo[: n - h] = np.min(sliding_window_view(L[1:], h), axis=1)[: n - h]
    ratio = X / E
    # funding held through settlement T: ts[t+1] <= T < ts[t+1+h]  ->  t in [iT-h, iT-1]
    fund = np.zeros(n)
    iT = (g["funding_ts"] - g["ts"][0]) // MINUTE_MS
    for i, rate in zip(iT, g["funding_rate"]):
        if 0 < i <= n:  # the grid is gap-free, so settlement T opens row i
            fund[max(i - h, 0): i] += rate
    return {
        "long": ratio - 1, "short": 1 - ratio, "ratio": ratio,
        "mae_long": lo / E - 1, "mfe_long": hi / E - 1,
        "mae_short": 1 - hi / E, "mfe_short": 1 - lo / E,
        "fund_long": fund,
    }


def regime_labels(g: dict[str, np.ndarray], disc_mask: np.ndarray) -> dict[str, np.ndarray]:
    C = g["close"]
    lnC = np.log(C)
    r7 = lnC - _lag(lnC, 7 * DAY_BARS)
    trend = np.full(len(C), -1, dtype=np.int8)
    trend[r7 > 0.05] = 0      # BULL
    trend[r7 < -0.05] = 1     # BEAR
    trend[np.isfinite(r7) & (np.abs(r7) <= 0.05)] = 2  # SIDEWAYS
    rv = _rstd(lnC - _lag(lnC, 1), DAY_BARS)
    lo_cut, hi_cut = np.nanquantile(rv[disc_mask], [0.30, 0.70])
    vol = np.full(len(C), -1, dtype=np.int8)
    vol[rv > hi_cut] = 0      # HIGH
    vol[rv < lo_cut] = 2      # LOW
    vol[np.isfinite(rv) & (rv >= lo_cut) & (rv <= hi_cut)] = 1  # MID
    entry_ts = g["ts"] + MINUTE_MS  # entry bar t+1
    hour = (entry_ts // 3_600_000) % 24
    session = (hour // 6).astype(np.int8)
    year = (np.datetime64("1970-01-01") + entry_ts.astype("timedelta64[ms]")).astype("datetime64[Y]").astype(int) + 1970
    return {"trend": trend, "vol": vol, "session": session, "hour": hour.astype(np.int8),
            "year": (year - 2021).astype(np.int8), "vol_cutoffs": np.array([lo_cut, hi_cut])}
