"""The C-4 context vector: raw coordinates and the point-in-time normalisation into [0,1].

Every coordinate reads sessions <= D only. Cross-sectional coordinates are ranked inside the
same session's base-eligible cross-section, which cannot see another date at all. The handful of
coordinates that have no usable cross-section - the market-wide SPY values, and a revenue growth
rate observed a few times a session - are ranked inside the pooled distribution of dates strictly
earlier than the row's own date. No mean, no standard deviation and no quantile is ever taken
over the full sample.

Nothing here reads a label, and ``labels4.py`` is never imported.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtest.strategy_c4_analog.context import CATEGORIES, CATEGORY_INDEX, EventContext
from app.backtest.strategy_c_selection.features import FeatureSet
from app.backtest.strategy_c_selection.panel import BENCHMARK, Panel

NEUTRAL = 0.5
MARKET_FEATURES = ("return_1d", "return_3d", "return_5d", "rvol_20", "volume_z_20",
                   "dollar_volume_change_5d", "atr_pct", "true_range_pct", "atr_expansion",
                   "rs_spy_1d", "rs_spy_5d")
OBV_FEATURES = ("obv_return_5", "obv_return_10", "obv_return_20", "obv_slope_5", "obv_slope_10",
                "price_obv_divergence_10")
VWAP_FEATURES = ("close_to_vwap_pct", "open_to_vwap_pct")
MARKET_CONTEXT_FEATURES = ("spy_return_1d", "spy_return_5d", "spy_atr_pct")
EVENT_FEATURES = ("event_present", *(f"event_type_{name}" for name in CATEGORIES),
                  "event_age_sessions", "event_count", "negative_risk_flag")
QUALITY_FEATURES = ("periodic_event_flag", "quality_observable_flag", "revenue_yoy",
                    "revenue_growth_bucket")


def percentile_rank(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """(average rank - 1)/(n - 1) inside each session's masked cross-section; ties averaged."""
    out = np.full(values.shape, np.nan)
    finite = np.isfinite(values)
    for i in range(values.shape[0]):
        row = mask[i] & finite[i]
        n = int(row.sum())
        if n == 0:
            continue
        if n == 1:
            out[i, row] = NEUTRAL
            continue
        v = values[i, row]
        order = np.argsort(v, kind="stable")
        sorted_v = v[order]
        left = np.searchsorted(sorted_v, sorted_v, side="left")
        right = np.searchsorted(sorted_v, sorted_v, side="right")
        average = (left + right - 1) / 2.0
        ranks = np.empty(n)
        ranks[order] = average
        out[i, row] = ranks / (n - 1)
    return out


def expanding_rank_series(values: np.ndarray, first: int) -> np.ndarray:
    """Rank of each date's value inside the pooled values of dates strictly earlier than it."""
    out = np.full(values.shape, np.nan)
    for t in range(first + 1, len(values)):
        if not np.isfinite(values[t]):
            continue
        past = values[first:t]
        past = np.sort(past[np.isfinite(past)])
        if past.size == 0:
            continue
        low = np.searchsorted(past, values[t], side="left")
        high = np.searchsorted(past, values[t], side="right")
        out[t] = ((low + high) / 2.0) / past.size
    return out


def expanding_rank_panel(values: np.ndarray, mask: np.ndarray, first: int) -> np.ndarray:
    """Same idea for a sparse panel coordinate: the pool is every earlier observed value."""
    out = np.full(values.shape, np.nan)
    pool: list[np.ndarray] = []
    sorted_pool = np.empty(0)
    for t in range(first, values.shape[0]):
        row = mask[t] & np.isfinite(values[t])
        if row.any() and sorted_pool.size:
            v = values[t, row]
            low = np.searchsorted(sorted_pool, v, side="left")
            high = np.searchsorted(sorted_pool, v, side="right")
            out[t, row] = ((low + high) / 2.0) / sorted_pool.size
        if row.any():
            pool.append(values[t, row])
            sorted_pool = np.sort(np.concatenate(pool))
    return out


def _rolling_sum(array: np.ndarray, window: int) -> np.ndarray:
    return pd.DataFrame(array).rolling(window, min_periods=window).sum().to_numpy()


@dataclass(frozen=True)
class RawFeatures:
    values: dict[str, np.ndarray]
    category: np.ndarray
    event_unknown: np.ndarray


def build_raw(panel: Panel, vw: np.ndarray, c_features: FeatureSet,
              event: EventContext) -> RawFeatures:
    """Every declared coordinate in its own units, before normalisation."""
    factor, _, _ = panel.split_arrays()
    p = panel.close / factor
    high = panel.high / factor
    low = panel.low / factor
    volume = panel.volume * factor
    frame_p = pd.DataFrame(p)
    values: dict[str, np.ndarray] = {}

    for name in ("return_1d", "return_3d", "return_5d", "rvol_20", "volume_z_20", "atr_pct",
                 "rs_spy_1d", "rs_spy_5d"):
        values[name] = c_features.values[name]

    dollar = panel.close * panel.volume
    adv20 = c_features.values["adv20_dollar"]
    mean_dollar_5 = pd.DataFrame(dollar).rolling(5, min_periods=5).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        values["dollar_volume_change_5d"] = np.where(adv20 > 0, mean_dollar_5 / adv20 - 1.0, np.nan)

    previous = frame_p.shift(1).to_numpy()
    true_range = np.fmax(high, previous) - np.fmin(low, previous)
    true_range[np.isnan(high) | np.isnan(low) | np.isnan(previous)] = np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        values["true_range_pct"] = true_range / previous
        values["atr_expansion"] = np.where(c_features.values["atr_pct"] > 0,
                                           values["true_range_pct"] / c_features.values["atr_pct"],
                                           np.nan)

    change = p - previous
    step = np.sign(np.nan_to_num(change, nan=0.0)) * np.nan_to_num(volume, nan=0.0)
    obv = np.cumsum(step, axis=0)
    filled_volume = np.nan_to_num(volume, nan=0.0)
    rows = np.arange(panel.shape[0])[:, None].astype(float)
    weighted = obv * rows
    for k in (5, 10, 20):
        traded = _rolling_sum(filled_volume, k)
        shifted = pd.DataFrame(obv).shift(k).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            values[f"obv_return_{k}"] = np.where(traded > 0, (obv - shifted) / traded, np.nan)
    for k in (5, 10):
        total = _rolling_sum(obv, k)
        weight = _rolling_sum(weighted, k) - (rows - k + 1) * total
        sxx = k * (k * k - 1) / 12.0
        slope = (weight - ((k - 1) / 2.0) * total) / sxx
        mean_volume = _rolling_sum(filled_volume, k) / k
        with np.errstate(divide="ignore", invalid="ignore"):
            values[f"obv_slope_{k}"] = np.where(mean_volume > 0, slope / mean_volume, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        values["return_10d_helper"] = (frame_p / frame_p.shift(10) - 1.0).to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        values["close_to_vwap_pct"] = np.where(vw > 0, panel.close / vw - 1.0, np.nan)
        values["open_to_vwap_pct"] = np.where(vw > 0, panel.open / vw - 1.0, np.nan)

    spy = panel.column(BENCHMARK)
    for name, source in (("spy_return_1d", "return_1d"), ("spy_return_5d", "return_5d"),
                         ("spy_atr_pct", "atr_pct")):
        column = c_features.values[source][:, spy]
        values[name] = np.repeat(column[:, None], panel.shape[1], axis=1)

    values["event_present"] = event.event_present
    values["event_age_sessions"] = event.event_age
    values["event_count"] = event.event_count
    values["negative_risk_flag"] = event.negative_risk
    values["periodic_event_flag"] = event.periodic_flag
    values["quality_observable_flag"] = event.quality_observable
    values["revenue_yoy"] = event.revenue_yoy
    values["revenue_growth_bucket"] = event.revenue_bucket
    return RawFeatures(values, event.category, event.unknown)


def normalize(raw: RawFeatures, mask: np.ndarray, first_idx: int, *,
              include_vwap: bool, log=lambda _m: None) -> dict[str, np.ndarray]:
    """Every declared coordinate in [0,1]; missing values take the declared neutral 0.5."""
    out: dict[str, np.ndarray] = {}
    cross_sectional = list(MARKET_FEATURES) + ["event_age_sessions", "event_count"]
    cross_sectional += [name for name in OBV_FEATURES if name != "price_obv_divergence_10"]
    if include_vwap:
        cross_sectional += list(VWAP_FEATURES)
    for name in cross_sectional:
        out[name] = percentile_rank(raw.values[name], mask)
    log("cross-sectional ranks done")

    price_rank = percentile_rank(raw.values["return_10d_helper"], mask)
    divergence = (price_rank - out["obv_return_10"] + 1.0) / 2.0
    out["price_obv_divergence_10"] = percentile_rank(divergence, mask)

    for name in MARKET_CONTEXT_FEATURES:
        series = raw.values[name][:, 0]
        ranked = expanding_rank_series(series, first_idx)
        out[name] = np.repeat(ranked[:, None], raw.values[name].shape[1], axis=1)
    log("market context ranks done")

    observable = np.isfinite(raw.values["revenue_yoy"])
    out["revenue_yoy"] = expanding_rank_panel(raw.values["revenue_yoy"], mask & observable, first_idx)
    out["revenue_growth_bucket"] = raw.values["revenue_growth_bucket"]

    for name in ("event_present", "negative_risk_flag", "periodic_event_flag",
                 "quality_observable_flag"):
        out[name] = raw.values[name]

    for position, category in enumerate(CATEGORIES):
        out[f"event_type_{category}"] = (raw.category == position).astype(np.float64)

    for name, array in out.items():
        out[name] = np.where(np.isfinite(array), array, NEUTRAL).astype(np.float32)
    return out


def matrix(normalized: dict[str, np.ndarray], names: Sequence[str],
           rows_i: np.ndarray, rows_j: np.ndarray) -> np.ndarray:
    return np.stack([normalized[name][rows_i, rows_j] for name in names], axis=1).astype(np.float32)
