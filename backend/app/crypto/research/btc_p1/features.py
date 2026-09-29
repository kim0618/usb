"""Six feature families, 34 columns, every one backward-looking at the decision bar's close.

The count is capped on purpose (contract section 11). Six studies of single-feature cells have
already shown that none of these axes carries a standalone edge, so the question here is whether
they carry information jointly about how large a move is coming. Widening the search until
something passes is the failure mode this cap exists to prevent.

Family assignment is recorded so that importance can be reported per family rather than per
column, which is the level at which the economics are arguable.
"""
from __future__ import annotations

import numpy as np

from . import windows as W

HOUR = 60
DAY = 1440

#: (column, family). Order is fixed and is the column order of the design matrix.
SPEC: tuple[tuple[str, str], ...] = (
    ("ret_5m_bp", "F1"), ("ret_15m_bp", "F1"), ("ret_30m_bp", "F1"), ("ret_1h_bp", "F1"),
    ("ret_2h_bp", "F1"), ("ret_4h_bp", "F1"),
    ("accel_15m_vs_1h", "F1"), ("accel_1h_vs_4h", "F1"),
    ("pos_in_range_4h", "F1"), ("pos_in_range_24h", "F1"),

    ("rv_1h_bp", "F2"), ("rv_4h_bp", "F2"), ("rv_24h_bp", "F2"),
    ("vol_ratio_1h_24h", "F2"), ("vol_ratio_4h_24h", "F2"), ("range_expansion_4h_24h", "F2"),

    ("oi_chg_1h", "F3"), ("oi_chg_4h", "F3"), ("oi_chg_24h", "F3"),
    ("oi_accel_1h_vs_4h", "F3"), ("oi_price_joint_1h", "F3"),

    ("basis_bp", "F4"), ("basis_z_24h", "F4"), ("basis_chg_1h", "F4"),
    ("basis_accel_1h_vs_4h", "F4"),

    ("funding_bp", "F5"), ("funding_trend_24h_bp", "F5"), ("funding_z_30d", "F5"),

    ("hour_sin", "F6"), ("hour_cos", "F6"), ("dow_sin", "F6"), ("dow_cos", "F6"),
    ("trend_regime_24h_bp", "F6"), ("vol_regime_ln", "F6"),
)

NAMES: tuple[str, ...] = tuple(name for name, _ in SPEC)
FAMILY: dict[str, str] = {name: fam for name, fam in SPEC}
FAMILIES: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6")
FAMILY_TITLE = {
    "F1": "price path / multi-horizon returns",
    "F2": "volatility state",
    "F3": "open interest / positioning",
    "F4": "basis / perp pressure",
    "F5": "funding",
    "F6": "time and regime context",
}

#: Longest lookback any column needs. Rows before this are dropped, not imputed.
WARMUP_MINUTES = 30 * DAY


def _safe_ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.full(len(a), np.nan)
    ok = np.isfinite(a) & np.isfinite(b) & (b > 0)
    out[ok] = a[ok] / b[ok]
    return out


def _bp(x: np.ndarray) -> np.ndarray:
    return x * 1e4


def build(grid: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """One column per name in SPEC, aligned to the 1m grid."""
    close, high, low = grid["close"], grid["high"], grid["low"]
    ln_close = np.log(close)
    r1 = np.concatenate(([np.nan], np.diff(ln_close)))

    out: dict[str, np.ndarray] = {}

    def ret(minutes: int) -> np.ndarray:
        return _bp(ln_close - W.shift(ln_close, minutes))

    # --- F1: where price has been, and how fast ---------------------------------------
    out["ret_5m_bp"] = ret(5)
    out["ret_15m_bp"] = ret(15)
    out["ret_30m_bp"] = ret(30)
    out["ret_1h_bp"] = ret(HOUR)
    out["ret_2h_bp"] = ret(2 * HOUR)
    out["ret_4h_bp"] = ret(4 * HOUR)
    # Acceleration: recent pace against the pace implied by the longer window.
    out["accel_15m_vs_1h"] = out["ret_15m_bp"] - out["ret_1h_bp"] / 4.0
    out["accel_1h_vs_4h"] = out["ret_1h_bp"] - out["ret_4h_bp"] / 4.0

    for label, w in (("4h", 4 * HOUR), ("24h", DAY)):
        hi, lo = W.rolling_max(high, w), W.rolling_min(low, w)
        span = hi - lo
        pos = np.full(len(close), np.nan)
        ok = np.isfinite(span) & (span > 0)
        pos[ok] = (close[ok] - lo[ok]) / span[ok]
        out[f"pos_in_range_{label}"] = pos

    # --- F2: how violent the tape is, and whether that is changing ----------------------
    rv_1h = _bp(W.rolling_std(np.nan_to_num(r1), HOUR) * np.sqrt(HOUR))
    rv_4h = _bp(W.rolling_std(np.nan_to_num(r1), 4 * HOUR) * np.sqrt(4 * HOUR))
    rv_24h = _bp(W.rolling_std(np.nan_to_num(r1), DAY) * np.sqrt(DAY))
    out["rv_1h_bp"], out["rv_4h_bp"], out["rv_24h_bp"] = rv_1h, rv_4h, rv_24h
    out["vol_ratio_1h_24h"] = _safe_ratio(rv_1h, rv_24h)
    out["vol_ratio_4h_24h"] = _safe_ratio(rv_4h, rv_24h)
    range_4h = W.rolling_max(high, 4 * HOUR) - W.rolling_min(low, 4 * HOUR)
    range_24h = W.rolling_max(high, DAY) - W.rolling_min(low, DAY)
    out["range_expansion_4h_24h"] = _safe_ratio(range_4h, range_24h)

    # --- F3: whether positions are being built or unwound -------------------------------
    ln_oi = np.log(np.where(grid["oi"] > 0, grid["oi"], np.nan))
    oi_1h = ln_oi - W.shift(ln_oi, HOUR)
    oi_4h = ln_oi - W.shift(ln_oi, 4 * HOUR)
    out["oi_chg_1h"], out["oi_chg_4h"] = oi_1h, oi_4h
    out["oi_chg_24h"] = ln_oi - W.shift(ln_oi, DAY)
    out["oi_accel_1h_vs_4h"] = oi_1h - oi_4h / 4.0
    # Direction matters: OI rising into a rally is new longs, rising into a slide is new shorts.
    out["oi_price_joint_1h"] = np.sign(out["ret_1h_bp"]) * oi_1h

    # --- F4: what perp traders are paying to hold ---------------------------------------
    basis = np.full(len(close), np.nan)
    idx = grid["index_close"]
    ok = np.isfinite(idx) & (idx > 0)
    basis[ok] = (grid["mark_close"][ok] - idx[ok]) / idx[ok] * 1e4
    out["basis_bp"] = basis
    b_mean, b_std = W.rolling_mean(np.nan_to_num(basis), DAY), W.rolling_std(np.nan_to_num(basis), DAY)
    z = np.full(len(close), np.nan)
    okz = np.isfinite(b_std) & (b_std > 1e-9)
    z[okz] = (basis[okz] - b_mean[okz]) / b_std[okz]
    out["basis_z_24h"] = z
    b_1h = basis - W.shift(basis, HOUR)
    out["basis_chg_1h"] = b_1h
    out["basis_accel_1h_vs_4h"] = b_1h - (basis - W.shift(basis, 4 * HOUR)) / 4.0

    # --- F5: funding, as context and never as a standalone signal -----------------------
    funding = grid["funding_last"] * 1e4
    out["funding_bp"] = funding
    out["funding_trend_24h_bp"] = funding - W.rolling_mean(np.nan_to_num(funding), DAY)
    f_mean = W.rolling_mean(np.nan_to_num(funding), 30 * DAY)
    f_std = W.rolling_std(np.nan_to_num(funding), 30 * DAY)
    fz = np.full(len(close), np.nan)
    okf = np.isfinite(f_std) & (f_std > 1e-9)
    fz[okf] = (funding[okf] - f_mean[okf]) / f_std[okf]
    out["funding_z_30d"] = fz

    # --- F6: clock and regime, continuous rather than chopped into cells ----------------
    minutes = (grid["ts"] // 60_000).astype(np.int64)
    hour_frac = (minutes % DAY) / DAY
    out["hour_sin"] = np.sin(2 * np.pi * hour_frac)
    out["hour_cos"] = np.cos(2 * np.pi * hour_frac)
    # 1970-01-01 was a Thursday; the offset makes the cycle line up with the week, though only
    # its periodicity is used.
    dow_frac = ((minutes // DAY + 4) % 7) / 7.0
    out["dow_sin"] = np.sin(2 * np.pi * dow_frac)
    out["dow_cos"] = np.cos(2 * np.pi * dow_frac)
    out["trend_regime_24h_bp"] = ret(DAY)
    rv_30d = W.rolling_mean(np.nan_to_num(rv_24h), 30 * DAY)
    out["vol_regime_ln"] = np.log(_safe_ratio(rv_24h, rv_30d))

    missing = set(NAMES) - set(out)
    extra = set(out) - set(NAMES)
    if missing or extra:
        raise RuntimeError(f"feature set drift: missing={sorted(missing)} extra={sorted(extra)}")
    return out


def matrix(columns: dict[str, np.ndarray], rows: np.ndarray) -> np.ndarray:
    """Design matrix in the fixed SPEC order, one row per decision index."""
    return np.column_stack([columns[name][rows] for name in NAMES])
