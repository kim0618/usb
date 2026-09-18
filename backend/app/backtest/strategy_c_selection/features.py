"""Point-in-time C-M features, eligibility and candidate flags on a ``Panel``.

Every array is (sessions, tickers). The value in row i reads rows <= i only: shifts are
backward (``shift(+k)``), rolling windows end at the row, and split information enters only as
``F(t)`` (splits executed <= t). ``compute`` has no notion of "today"; the PIT audit proves the
property by recomputing on ``truncate(panel, D)`` and comparing row D.

Nothing here reads a label, and ``labels.py`` is never imported.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtest.strategy_c_selection.panel import BENCHMARK, Panel
from app.backtest.strategy_c_selection.rules import OPERATORS, SelectionRules, Variant

BASE_WINDOW = 20
BASE_SEASONING = 60
ATR_WINDOW = 14
YEAR_WINDOW = 252
RETURN_WINDOWS = (1, 3, 5)

FEATURE_COLUMNS = (
    "close", "return_1d", "return_3d", "return_5d", "volume", "rvol_20", "volume_z_20",
    "dollar_volume", "adv20_dollar", "dollar_volume_change", "atr_pct", "rs_spy_1d", "rs_spy_3d",
    "rs_spy_5d", "distance_52w_high", "breakout_52w", "split_flag", "reverse_split_flag",
    "ca_suspect", "price_bucket", "atr_bucket", "adv20_bucket")


@dataclass(frozen=True)
class FeatureSet:
    values: dict[str, np.ndarray]
    member: np.ndarray
    has_bar: np.ndarray
    base_history: np.ndarray
    m2_history: np.ndarray
    hard_filter: np.ndarray
    ca_excluded: np.ndarray

    def eligible(self, variant: Variant) -> np.ndarray:
        history = self.base_history if variant.history == "base" else self.m2_history
        return self.member & self.has_bar & history & self.hard_filter & ~self.ca_excluded

    def candidates(self, variant: Variant) -> np.ndarray:
        mask = self.eligible(variant)
        for condition in variant.conditions:
            value = self.values[condition.feature]
            with np.errstate(invalid="ignore"):  # a NaN feature compares False: never a candidate
                mask = mask & OPERATORS[condition.operator](value, condition.threshold)
        return mask


def _frame(array: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(array)


def _bucket(values: np.ndarray, edges: tuple[float, ...]) -> np.ndarray:
    out = np.searchsorted(np.asarray(edges), values, side="right").astype(float) - 1.0
    out[np.isnan(values) | (out < 0)] = np.nan
    return out


def compute(panel: Panel, rules: SelectionRules) -> FeatureSet:
    factor, split_count, reverse_count = panel.split_arrays()
    close_raw, volume_raw = panel.close, panel.volume
    p = close_raw / factor
    h = panel.high / factor
    lo = panel.low / factor
    v = volume_raw * factor
    t, n = p.shape
    has_bar = ~np.isnan(close_raw)

    pf, vf = _frame(p), _frame(v)
    values: dict[str, np.ndarray] = {"close": close_raw, "volume": volume_raw}
    spy = panel.column(BENCHMARK)
    for k in RETURN_WINDOWS:
        ret = (pf / pf.shift(k) - 1.0).to_numpy()
        values[f"return_{k}d"] = ret
        values[f"rs_spy_{k}d"] = ret - ret[:, [spy]]

    prior_v = vf.shift(1).rolling(BASE_WINDOW, min_periods=BASE_WINDOW)
    mean_v, std_v = prior_v.mean().to_numpy(), prior_v.std(ddof=1).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        values["rvol_20"] = np.where(mean_v > 0, v / mean_v, np.nan)
        values["volume_z_20"] = np.where(std_v > 0, (v - mean_v) / std_v, np.nan)
    dollar = close_raw * volume_raw
    adv20 = _frame(dollar).shift(1).rolling(BASE_WINDOW, min_periods=BASE_WINDOW).mean().to_numpy()
    values["dollar_volume"] = dollar
    values["adv20_dollar"] = adv20
    with np.errstate(divide="ignore", invalid="ignore"):
        values["dollar_volume_change"] = np.where(adv20 > 0, dollar / adv20 - 1.0, np.nan)

    prev_p = pf.shift(1).to_numpy()
    true_range = np.fmax(h, prev_p) - np.fmin(lo, prev_p)
    true_range[np.isnan(h) | np.isnan(lo) | np.isnan(prev_p)] = np.nan
    atr_prior = _frame(true_range).shift(1).rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        values["atr_pct"] = atr_prior / prev_p

    hf = _frame(h)
    year_high = hf.rolling(YEAR_WINDOW, min_periods=rules.m2_min_bars).max().to_numpy()
    prior_year_high = hf.shift(1).rolling(YEAR_WINDOW - 1, min_periods=rules.m2_min_bars - 1).max().to_numpy()
    bars_in_year = _frame(has_bar.astype(float)).rolling(YEAR_WINDOW, min_periods=1).sum().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        values["distance_52w_high"] = p / year_high - 1.0
        values["breakout_52w"] = np.where(np.isnan(prior_year_high) | np.isnan(p), np.nan,
                                          (p > prior_year_high).astype(float))

    lagged_count = _frame(split_count).shift(BASE_WINDOW).to_numpy()
    lagged_reverse = _frame(reverse_count).shift(BASE_WINDOW).to_numpy()
    split_flag = np.nan_to_num(split_count - lagged_count, nan=1.0) > 0
    reverse_flag = np.nan_to_num(reverse_count - lagged_reverse, nan=1.0) > 0
    values["split_flag"] = split_flag.astype(float)
    values["reverse_split_flag"] = reverse_flag.astype(float)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = p / prev_p
    jump = ((ratio >= rules.ca_ratio) | (ratio <= 1.0 / rules.ca_ratio)).astype(float)
    jump[np.isnan(ratio)] = np.nan
    ca_window = _frame(jump).rolling(BASE_WINDOW, min_periods=BASE_WINDOW).max().to_numpy()
    ca_suspect = np.nan_to_num(ca_window, nan=1.0) > 0
    values["ca_suspect"] = ca_suspect.astype(float)

    values["price_bucket"] = _bucket(close_raw, rules.price_edges)
    values["atr_bucket"] = _bucket(values["atr_pct"], rules.atr_edges)
    values["adv20_bucket"] = _bucket(adv20, rules.adv_edges)

    index = np.arange(t)[:, None]
    first_bar = np.where(has_bar.any(axis=0), has_bar.argmax(axis=0), t)[None, :]
    all_prior_bars = _frame(has_bar.astype(float)).rolling(BASE_WINDOW + 1, min_periods=BASE_WINDOW + 1) \
        .min().to_numpy()
    base_history = (np.nan_to_num(all_prior_bars, nan=0.0) > 0) & (first_bar <= index - BASE_SEASONING)
    m2_history = base_history & (first_bar <= index - (YEAR_WINDOW - 1)) & (bars_in_year >= rules.m2_min_bars) \
        & (index >= YEAR_WINDOW - 1)
    with np.errstate(invalid="ignore"):
        hard_filter = (close_raw >= rules.min_close) & (adv20 >= rules.min_adv20_dollar)
    member = panel.membership().copy()
    member[:, spy] = False
    ca_excluded = split_flag | ca_suspect
    return FeatureSet(values, member, has_bar, base_history, m2_history, hard_filter, ca_excluded)
