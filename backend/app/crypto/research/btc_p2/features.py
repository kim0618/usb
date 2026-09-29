"""Seven families of directional features, deliberately stripped of volatility scale.

P1's whole predictive power came from volatility, and volatility has no direction: when the tape
gets violent, +1% and -1% both become likelier. A direction model fed raw returns would inherit
the same confusion, because a large return is mostly a statement about how volatile the market
is.

So the signed features here are divided by trailing realised volatility, and the level features
are expressed as a position inside the recent range. What survives is shape rather than size: how
the path got here, not how far it travelled. There is no volatility family at all.
"""
from __future__ import annotations

import numpy as np

from app.crypto.research.btc_p1 import windows as W

HOUR = 60
DAY = 1440
EPS = 1e-9

#: (column, family). Order is fixed and defines the design matrix.
D2_SPEC: tuple[tuple[str, str], ...] = (
    ("ret_z_5m", "F1"), ("ret_z_15m", "F1"), ("ret_z_1h", "F1"), ("ret_z_4h", "F1"),
    ("accel_z_15m_1h", "F1"), ("accel_z_1h_4h", "F1"),
    ("dist_from_high_4h_r", "F1"), ("dist_from_low_4h_r", "F1"),
    ("dist_from_high_24h_r", "F1"), ("dist_from_low_24h_r", "F1"),
    ("trend_agreement_1h_4h", "F1"),

    ("body_ratio_1h", "F2"), ("upper_wick_ratio_1h", "F2"), ("lower_wick_ratio_1h", "F2"),
    ("wick_asymmetry_1h", "F2"), ("close_location_1h", "F2"), ("consecutive_dir_15m", "F2"),

    ("oi_chg_5m", "F3"), ("oi_chg_15m", "F3"), ("oi_chg_1h", "F3"),
    ("oi_price_joint_5m", "F3"), ("oi_price_joint_15m", "F3"), ("oi_price_joint_1h", "F3"),

    ("basis_bp", "F4"), ("basis_chg_15m", "F4"), ("basis_chg_1h", "F4"),
    ("basis_accel_1h_4h", "F4"), ("price_basis_divergence", "F4"),

    ("funding_z_30d", "F5"), ("funding_x_trend", "F5"), ("funding_x_oi", "F5"),

    ("hour_sin", "F7"), ("hour_cos", "F7"), ("trend_regime_z_24h", "F7"),
)

EXTERNAL_SPEC: tuple[tuple[str, str], ...] = (
    ("bybit_minus_binance_z_15m", "F6"), ("bybit_minus_binance_z_1h", "F6"),
    ("bybit_minus_binance_z_5m", "F6"),
    ("spot_perp_ret_z_1h", "F6"), ("spot_perp_basis_bp", "F6"), ("spot_perp_basis_chg_1h", "F6"),
)

D2_NAMES: tuple[str, ...] = tuple(name for name, _ in D2_SPEC)
EXTERNAL_NAMES: tuple[str, ...] = tuple(name for name, _ in EXTERNAL_SPEC)
ALL_NAMES: tuple[str, ...] = D2_NAMES + EXTERNAL_NAMES

FAMILY: dict[str, str] = {name: family for name, family in D2_SPEC + EXTERNAL_SPEC}
FAMILIES = ("F1", "F2", "F3", "F4", "F5", "F6", "F7")
FAMILY_TITLE = {
    "F1": "price path and shape",
    "F2": "candle structure",
    "F3": "open interest crossed with price",
    "F4": "basis divergence",
    "F5": "funding context",
    "F6": "cross-market (Binance perp and spot)",
    "F7": "time and trend context",
}

D2_ONLY = "D2_ONLY"
WITH_EXTERNAL = "WITH_EXTERNAL"
FEATURE_SETS = {D2_ONLY: D2_NAMES, WITH_EXTERNAL: ALL_NAMES}

WARMUP_MINUTES = 30 * DAY


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    out = np.zeros(len(numerator))
    ok = np.isfinite(numerator) & np.isfinite(denominator) & (np.abs(denominator) > EPS)
    out[ok] = numerator[ok] / denominator[ok]
    return np.clip(out, -50.0, 50.0)


def _volatility_bp(returns: np.ndarray) -> np.ndarray:
    """Trailing 24h realised volatility in bp, the denominator that removes scale."""
    return W.rolling_std(np.nan_to_num(returns), DAY) * np.sqrt(DAY) * 1e4


def build(grid: dict[str, np.ndarray],
          external: dict[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    close, high, low, open_ = grid["close"], grid["high"], grid["low"], grid["open"]
    ln_close = np.log(close)
    r1 = np.concatenate(([np.nan], np.diff(ln_close)))
    rv = _volatility_bp(r1)

    def ret_bp(minutes: int) -> np.ndarray:
        return (ln_close - W.shift(ln_close, minutes)) * 1e4

    def ret_z(minutes: int) -> np.ndarray:
        return _safe_divide(ret_bp(minutes), rv)

    out: dict[str, np.ndarray] = {}

    # --- F1: the shape of the path, with its size divided out ---------------------------
    z5, z15, z1h, z4h = ret_z(5), ret_z(15), ret_z(HOUR), ret_z(4 * HOUR)
    out["ret_z_5m"], out["ret_z_15m"] = z5, z15
    out["ret_z_1h"], out["ret_z_4h"] = z1h, z4h
    out["accel_z_15m_1h"] = z15 - z1h / 4.0
    out["accel_z_1h_4h"] = z1h - z4h / 4.0

    for label, window in (("4h", 4 * HOUR), ("24h", DAY)):
        top, bottom = W.rolling_max(high, window), W.rolling_min(low, window)
        span = top - bottom
        out[f"dist_from_high_{label}_r"] = _safe_divide(close - top, span)
        out[f"dist_from_low_{label}_r"] = _safe_divide(close - bottom, span)

    out["trend_agreement_1h_4h"] = np.sign(z1h) * np.sign(z4h)

    # --- F2: what the candles look like, as ratios rather than prices -------------------
    body = close - open_
    span_1m = high - low
    upper = high - np.maximum(open_, close)
    lower = np.minimum(open_, close) - low

    sum_span = W.rolling_mean(np.nan_to_num(span_1m), HOUR)
    out["body_ratio_1h"] = _safe_divide(W.rolling_mean(np.nan_to_num(body), HOUR), sum_span)
    upper_ratio = _safe_divide(W.rolling_mean(np.nan_to_num(upper), HOUR), sum_span)
    lower_ratio = _safe_divide(W.rolling_mean(np.nan_to_num(lower), HOUR), sum_span)
    out["upper_wick_ratio_1h"] = upper_ratio
    out["lower_wick_ratio_1h"] = lower_ratio
    out["wick_asymmetry_1h"] = _safe_divide(upper_ratio - lower_ratio,
                                            np.abs(upper_ratio) + np.abs(lower_ratio))
    out["close_location_1h"] = W.rolling_mean(
        np.nan_to_num(_safe_divide(close - low, span_1m)), HOUR)
    out["consecutive_dir_15m"] = W.rolling_mean(np.sign(np.nan_to_num(body)), 15)

    # --- F3: open interest only ever crossed with price ----------------------------------
    ln_oi = np.log(np.where(grid["oi"] > 0, grid["oi"], np.nan))
    for label, minutes, signed in (("5m", 5, z5), ("15m", 15, z15), ("1h", HOUR, z1h)):
        change = ln_oi - W.shift(ln_oi, minutes)
        out[f"oi_chg_{label}"] = change
        # Rising open interest into a rally is new longs; into a slide it is new shorts. The
        # sign of the move is what makes the same number mean opposite things.
        out[f"oi_price_joint_{label}"] = np.sign(signed) * change

    # --- F4: basis, as movement rather than level ----------------------------------------
    index = grid["index_close"]
    basis = np.full(len(close), np.nan)
    ok = np.isfinite(index) & (index > 0)
    basis[ok] = (grid["mark_close"][ok] - index[ok]) / index[ok] * 1e4
    out["basis_bp"] = basis
    change_15m = basis - W.shift(basis, 15)
    change_1h = basis - W.shift(basis, HOUR)
    out["basis_chg_15m"] = change_15m
    out["basis_chg_1h"] = change_1h
    out["basis_accel_1h_4h"] = change_1h - (basis - W.shift(basis, 4 * HOUR)) / 4.0
    out["price_basis_divergence"] = np.sign(z1h) * change_1h

    # --- F5: funding as context, never as a standalone signal ----------------------------
    funding = grid["funding_last"] * 1e4
    mean_30d = W.rolling_mean(np.nan_to_num(funding), 30 * DAY)
    std_30d = W.rolling_std(np.nan_to_num(funding), 30 * DAY)
    funding_z = _safe_divide(funding - mean_30d, std_30d)
    out["funding_z_30d"] = funding_z
    out["funding_x_trend"] = funding_z * np.sign(z4h)
    out["funding_x_oi"] = funding_z * np.nan_to_num(out["oi_chg_1h"])

    # --- F7: clock and trend, kept small -------------------------------------------------
    minutes = (grid["ts"] // 60_000).astype(np.int64)
    hour_fraction = (minutes % DAY) / DAY
    out["hour_sin"] = np.sin(2 * np.pi * hour_fraction)
    out["hour_cos"] = np.cos(2 * np.pi * hour_fraction)
    out["trend_regime_z_24h"] = ret_z(DAY)

    # --- F6: the other venues, if the archive is being used ------------------------------
    if external is not None:
        for label, window in (("5m", 5), ("15m", 15), ("1h", HOUR)):
            binance = np.log(external["um_close"])
            binance_ret = (binance - W.shift(binance, window)) * 1e4
            out[f"bybit_minus_binance_z_{label}"] = _safe_divide(ret_bp(window) - binance_ret, rv)
        spot = np.log(external["spot_close"])
        perp = np.log(external["um_close"])
        spot_ret = (spot - W.shift(spot, HOUR)) * 1e4
        perp_ret = (perp - W.shift(perp, HOUR)) * 1e4
        out["spot_perp_ret_z_1h"] = _safe_divide(spot_ret - perp_ret, rv)
        spot_basis = (external["um_close"] - external["spot_close"]) / external["spot_close"] * 1e4
        out["spot_perp_basis_bp"] = spot_basis
        out["spot_perp_basis_chg_1h"] = spot_basis - W.shift(spot_basis, HOUR)

    expected = set(ALL_NAMES if external is not None else D2_NAMES)
    if set(out) != expected:
        missing, extra = sorted(expected - set(out)), sorted(set(out) - expected)
        raise RuntimeError(f"feature set drift: missing={missing} extra={extra}")
    return out


def matrix(columns: dict[str, np.ndarray], rows: np.ndarray, feature_set: str) -> np.ndarray:
    return np.column_stack([columns[name][rows] for name in FEATURE_SETS[feature_set]])
