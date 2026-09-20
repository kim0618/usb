"""The five declared price features, and the volatility bucket N1 matches on.

These exist only to give the analog signal something honest to be compared against. D0
``initial_features`` keeps them out of the signal itself - the pattern path is the only signal
input - and lists exactly where they are allowed to appear: the N1 volatility bucket and the N2
baseline. Nothing here is ever read by ``signal.py``.

    return_W            P(D) / P(D-W) - 1
    return_1d           P(D) / P(D-1) - 1
    return_5d           P(D) / P(D-5) - 1
    realized_vol_W      std_ddof1 of ln(P(t)/P(t-1)) for t in D-W+1..D
    distance_to_W_high  P(D) / max(H(D-W+1..D)) - 1

Each is then replaced by its per-date percentile rank over that date's full eligible universe,
``(average rank - 1) / (n - 1)``, so a feature's scale and its cross-sectional drift cannot
matter. The same standardization gives N1 its volatility quintile.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_analog.models import HardFail

FEATURE_NAMES = ("return_W", "return_1d", "return_5d", "realized_vol_W", "distance_to_W_high")
#: D0 ``baseline_N1.matching``: quintile edges on the per-date percentile rank of ``rv_W``.
VOLATILITY_EDGES = (0.2, 0.4, 0.6, 0.8)
RETURN_1D_LOOKBACK = 1
RETURN_5D_LOOKBACK = 5


@dataclass(frozen=True)
class FeatureSet:
    """Per window length, the five features and their per-date standardized ranks, ``(T, N)``."""

    window: int
    raw: dict[str, np.ndarray]
    standardized: dict[str, np.ndarray]
    volatility_quintile: np.ndarray   # (T, N) int8, -1 where the feature is undefined
    finite: np.ndarray                # (T, N) bool: every one of the five features is finite

    def matrix(self, session_idx: np.ndarray, ticker_col: np.ndarray) -> np.ndarray:
        """``(n, 5)`` standardized features for the given ``(session, ticker)`` pairs."""
        return np.ascontiguousarray(
            np.stack([self.standardized[name][session_idx, ticker_col] for name in FEATURE_NAMES],
                     axis=1))

    def counts(self) -> dict[str, int]:
        return {"finite_rows": int(self.finite.sum()),
                "quintile_assigned": int((self.volatility_quintile >= 0).sum())}


def percentile_rank(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """``(average rank - 1) / (n - 1)`` per row over the masked, finite entries (D0 N2).

    Ties take the average rank, so two identical features standardize to the same number; a row
    with fewer than two usable names has no meaningful spread and yields NaN throughout.
    """
    out = np.full(values.shape, np.nan)
    usable = mask & np.isfinite(values)
    for row in range(values.shape[0]):
        columns = np.nonzero(usable[row])[0]
        if columns.size < 2:
            continue
        ranks = _average_rank(values[row, columns])
        out[row, columns] = (ranks - 1.0) / (columns.size - 1)
    return out


def _average_rank(values: np.ndarray) -> np.ndarray:
    """1-based ranks with ties averaged - the convention both the standardization and Spearman use."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.shape[0], dtype=np.float64)
    ranks[order] = np.arange(1, values.shape[0] + 1, dtype=np.float64)
    ordered = values[order]
    start = 0
    for index in range(1, ordered.shape[0] + 1):
        if index == ordered.shape[0] or ordered[index] != ordered[start]:
            if index - start > 1:
                ranks[order[start:index]] = (start + index + 1) / 2.0
            start = index
    return ranks


def _shifted_ratio(price: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full(price.shape, np.nan)
    if lookback < price.shape[0]:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[lookback:] = price[lookback:] / price[:-lookback] - 1.0
    return out


def build(panel: Panel, window: int, eligible: np.ndarray) -> FeatureSet:
    """Compute the five features for every ``(session, ticker)`` and standardize them per date."""
    if eligible.shape != panel.close.shape:
        raise HardFail("R5", f"eligibility mask {eligible.shape} does not match the panel")
    factor, _, _ = panel.split_arrays()
    price = panel.close / factor
    high = panel.high / factor

    raw = {"return_W": _shifted_ratio(price, window),
           "return_1d": _shifted_ratio(price, RETURN_1D_LOOKBACK),
           "return_5d": _shifted_ratio(price, RETURN_5D_LOOKBACK)}

    with np.errstate(divide="ignore", invalid="ignore"):
        log_step = np.full(price.shape, np.nan)
        log_step[1:] = np.log(price[1:] / price[:-1])
    volatility = np.full(price.shape, np.nan)
    rolling_high = np.full(price.shape, np.nan)
    for end in range(window, price.shape[0]):
        block = log_step[end - window + 1:end + 1]
        volatility[end] = block.std(axis=0, ddof=1)
        rolling_high[end] = high[end - window + 1:end + 1].max(axis=0)
    raw["realized_vol_W"] = volatility
    with np.errstate(divide="ignore", invalid="ignore"):
        raw["distance_to_W_high"] = price / rolling_high - 1.0

    standardized = {name: percentile_rank(values, eligible) for name, values in raw.items()}
    finite = np.ones(price.shape, dtype=bool)
    for name in FEATURE_NAMES:
        finite &= np.isfinite(standardized[name])

    quintile = np.full(price.shape, -1, dtype=np.int8)
    rank = standardized["realized_vol_W"]
    assigned = np.isfinite(rank)
    bucket = np.zeros(price.shape, dtype=np.int8)
    for edge in VOLATILITY_EDGES:
        bucket = bucket + (rank > edge)
    quintile[assigned] = bucket[assigned]
    return FeatureSet(window, raw, standardized, quintile, finite)


def horizons_share_features(windows: Sequence[int]) -> bool:
    """Features depend on the window length and never on the horizon or the representation."""
    return len(set(windows)) == 1
