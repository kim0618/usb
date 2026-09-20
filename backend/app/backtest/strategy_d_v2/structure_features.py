"""The ten declared market-structure coordinates, and the rank frame they live in.

Every formula here is transcribed from ``d_v2a_rules_v1.json`` §representation.features and is
never re-derived; ``config`` checks the names and the lookback bound against the declaration on
load, and the test suite pins each formula against hand-computed values on a synthetic panel.

Two properties make this module point-in-time safe by construction rather than by inspection:

* every window is a backward slice ending at the session being described, so a row computed on
  a panel truncated at ``D`` equals the same row on the full panel (the truncation audit
  re-measures this on real data instead of trusting it);
* the raw coordinates are turned into per-date cross-sectional ranks, and a date's rank frame is
  built from that date's own eligible cross-section only.

Prices are split-normalized (``x(t) / F(t)``). Volume is *not*: it is the raw share count, which
is why every volume window stays inside the ``(D-60, D]`` protection window the universe rule
already guarantees (contract §4.1). No coordinate reads a label, a future bar or a future split.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
# V1's percentile rank is the declared scaling, reused rather than reimplemented (reuse matrix).
from app.backtest.strategy_d_analog.features import percentile_rank
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, V2ARules
from app.backtest.strategy_d_v2.models import (
    VECTOR_STATUS_ORDER, HardFail, VectorStatus,
)

#: Lookback of each coordinate in sessions, mirroring the declaration (checked against it).
FEATURE_LOOKBACK: Mapping[str, int] = {
    "return_5": 5, "return_20": 20, "return_60": 60, "dist_to_20d_high": 20,
    "position_in_60d_range": 60, "rv_20": 20, "atr_ratio_20_60": 60, "tr_today_ratio": 20,
    "rvol_today": 20, "dollar_volume_ratio_20_60": 60,
}
#: Coordinates whose declaration names an explicit zero-denominator guard.
ZERO_DENOMINATOR_GUARDED = ("dist_to_20d_high", "position_in_60d_range", "atr_ratio_20_60",
                            "tr_today_ratio", "rvol_today", "dollar_volume_ratio_20_60")


@dataclass(frozen=True)
class StructureFeatures:
    """Raw coordinates, their rank frame, and why every window without a vector has none."""

    names: tuple[str, ...]
    raw: dict[str, np.ndarray]            # (T, N) float64
    rank: dict[str, np.ndarray]           # (T, N) float64 in [0, 1], NaN where undefined
    rank_eligible_population: dict[str, np.ndarray]  # diagnostic frame, see ``build``
    defined: np.ndarray                   # (T, N) bool: all ten coordinates usable
    status: np.ndarray                    # (T, N) int8 index into VECTOR_STATUS_ORDER, -1 = OK
    zero_denominator: dict[str, np.ndarray]
    eligible: np.ndarray
    max_lookback: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.defined.shape

    def matrix(self, session_idx: np.ndarray, ticker_col: np.ndarray) -> np.ndarray:
        """``(n, 10)`` rank vectors for the given ``(session, ticker)`` pairs, coordinate order."""
        if session_idx.shape != ticker_col.shape:
            raise HardFail("R5", "structure matrix got mismatched session and ticker arrays")
        if session_idx.size == 0:
            return np.empty((0, len(self.names)), dtype=np.float64)
        return np.ascontiguousarray(
            np.stack([self.rank[name][session_idx, ticker_col] for name in self.names], axis=1))

    def status_counts(self) -> dict[str, int]:
        out = {s.value: int((self.status == i).sum()) for i, s in enumerate(VECTOR_STATUS_ORDER)}
        out[VectorStatus.OK.value] = int(self.defined.sum())
        return out

    def zero_denominator_counts(self) -> dict[str, dict[str, int]]:
        """How often each declared guard fired, panel-wide and among eligible windows.

        The panel-wide count is dominated by cells that have no bar at all - a NaN denominator
        is "not greater than zero" - so it says little on its own. The eligible count is the one
        that matters: it is the number of windows the study would otherwise have used.
        """
        return {name: {"panel": int(mask.sum()), "eligible": int((mask & self.eligible).sum())}
                for name, mask in sorted(self.zero_denominator.items())}


def _shifted_ratio(price: np.ndarray, lookback: int) -> np.ndarray:
    """``P(D)/P(D-lookback) - 1``; rows without the earlier session stay NaN."""
    out = np.full(price.shape, np.nan)
    if lookback < price.shape[0]:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[lookback:] = price[lookback:] / price[:-lookback] - 1.0
    return out


def _rolling(values: np.ndarray, length: int, reduce: Callable[[np.ndarray], np.ndarray], *,
             end_offset: int = 0) -> np.ndarray:
    """``reduce`` over ``[t - length + 1 - end_offset, t - end_offset]`` for every row ``t``.

    A plain backward loop, not a cumulative sum: the window is exactly the one the declaration
    names, and a cumulative sum would make the value depend on rows outside it.
    """
    out = np.full(values.shape, np.nan)
    first = length - 1 + end_offset
    for end in range(first, values.shape[0]):
        stop = end - end_offset + 1
        out[end] = reduce(values[stop - length:stop])
    return out


def true_range(high: np.ndarray, low: np.ndarray, price: np.ndarray) -> np.ndarray:
    """``TR(t) = max(H-L, |H - P(t-1)|, |L - P(t-1)|)``; row 0 has no previous close.

    ``np.maximum`` rather than ``np.fmax`` on purpose: a missing high, low or previous close must
    propagate as NaN and make the window undefined, not fall back to whichever leg still has a
    number. A silently completed true range is exactly the kind of value a gap would hide in.
    """
    previous = np.full(price.shape, np.nan)
    previous[1:] = price[:-1]
    with np.errstate(invalid="ignore"):
        out = np.maximum(high - low, np.maximum(np.abs(high - previous), np.abs(low - previous)))
    out[0] = np.nan
    return out


def _safe_divide(numerator: np.ndarray, denominator: np.ndarray,
                 invalid: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = numerator / denominator
    out[invalid] = np.nan
    return out


def compute_raw(panel: Panel) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    """The ten coordinates in their own units, their zero-denominator masks, and bar completeness.

    Rolling intermediates are released as soon as the coordinate that needs them is written. On
    a 501 x 6,340 panel each one is 25 MB, and a phase that rebuilds this on truncated panels
    while holding a reference run in memory pays the difference at its peak, not on average.
    """
    factor, _, _ = panel.split_arrays()
    price = panel.close / factor
    high = panel.high / factor
    low = panel.low / factor
    volume = panel.volume                       # raw share count, never split adjusted
    dollar_volume = panel.close * panel.volume  # raw close x raw volume, as declared

    raw: dict[str, np.ndarray] = {}
    zero: dict[str, np.ndarray] = {}

    # -- trend ---------------------------------------------------------------------------------
    raw["return_5"] = _shifted_ratio(price, 5)
    raw["return_20"] = _shifted_ratio(price, 20)
    raw["return_60"] = _shifted_ratio(price, 60)

    # -- position ------------------------------------------------------------------------------
    high_20 = _rolling(high, 20, lambda block: block.max(axis=0))
    zero["dist_to_20d_high"] = ~(high_20 > 0)
    raw["dist_to_20d_high"] = _safe_divide(price, high_20, zero["dist_to_20d_high"]) - 1.0
    del high_20

    high_60 = _rolling(high, 60, lambda block: block.max(axis=0))
    low_60 = _rolling(low, 60, lambda block: block.min(axis=0))
    span = high_60 - low_60
    zero["position_in_60d_range"] = ~(span > 0)
    raw["position_in_60d_range"] = _safe_divide(price - low_60, span,
                                                zero["position_in_60d_range"])
    del high_60, low_60, span

    # -- volatility ----------------------------------------------------------------------------
    with np.errstate(divide="ignore", invalid="ignore"):
        log_step = np.full(price.shape, np.nan)
        log_step[1:] = np.log(price[1:] / price[:-1])
    raw["rv_20"] = _rolling(log_step, 20, lambda block: block.std(axis=0, ddof=1))
    del log_step

    tr = true_range(high, low, price)
    atr_20 = _rolling(tr, 20, lambda block: block.mean(axis=0))
    atr_60 = _rolling(tr, 60, lambda block: block.mean(axis=0))
    zero["atr_ratio_20_60"] = ~(atr_60 > 0)
    raw["atr_ratio_20_60"] = _safe_divide(atr_20, atr_60, zero["atr_ratio_20_60"])
    zero["tr_today_ratio"] = ~(atr_20 > 0)
    raw["tr_today_ratio"] = _safe_divide(tr, atr_20, zero["tr_today_ratio"])
    del tr, atr_20, atr_60, price, high, low

    # -- volume --------------------------------------------------------------------------------
    prior_volume_20 = _rolling(volume, 20, lambda block: block.mean(axis=0), end_offset=1)
    zero["rvol_today"] = ~(prior_volume_20 > 0)
    raw["rvol_today"] = _safe_divide(volume, prior_volume_20, zero["rvol_today"])
    del prior_volume_20

    dollar_20 = _rolling(dollar_volume, 20, lambda block: block.mean(axis=0))
    dollar_60 = _rolling(dollar_volume, 60, lambda block: block.mean(axis=0))
    zero["dollar_volume_ratio_20_60"] = ~(dollar_60 > 0)
    raw["dollar_volume_ratio_20_60"] = _safe_divide(dollar_20, dollar_60,
                                                    zero["dollar_volume_ratio_20_60"])
    del dollar_20, dollar_60, dollar_volume

    if tuple(raw) != FEATURE_NAMES:
        raise HardFail("R1", f"computed coordinates {tuple(raw)} are not the declared ten")

    bar_complete = (np.isfinite(panel.open) & np.isfinite(panel.high) & np.isfinite(panel.low)
                    & np.isfinite(panel.close) & np.isfinite(panel.volume)
                    & (panel.close > 0) & (panel.high > 0) & (panel.low > 0)
                    & (panel.volume >= 0))
    return raw, zero, bar_complete


def vector_status(raw: Mapping[str, np.ndarray], zero: Mapping[str, np.ndarray],
                   eligible: np.ndarray, bar_complete: np.ndarray, max_lookback: int,
                   ) -> tuple[np.ndarray, np.ndarray]:
    """First-match-wins status per window, so the histogram partitions the panel."""
    status = np.full(eligible.shape, -1, dtype=np.int8)

    def fail(reason: VectorStatus, mask: np.ndarray) -> None:
        status[(status < 0) & mask] = VECTOR_STATUS_ORDER.index(reason)

    fail(VectorStatus.NOT_ELIGIBLE, ~eligible)
    too_early = np.zeros(eligible.shape, dtype=bool)
    too_early[:max_lookback] = True
    fail(VectorStatus.INSUFFICIENT_HISTORY, too_early)
    fail(VectorStatus.INCOMPLETE_BAR, ~bar_complete)
    any_zero = np.zeros(eligible.shape, dtype=bool)
    for mask in zero.values():
        any_zero |= mask
    fail(VectorStatus.ZERO_DENOMINATOR, any_zero)
    non_finite = np.zeros(eligible.shape, dtype=bool)
    for values in raw.values():
        non_finite |= ~np.isfinite(values)
    fail(VectorStatus.NON_FINITE, non_finite)
    return status, status < 0


def build(panel: Panel, eligible: np.ndarray, rules: V2ARules, *,
          diagnostic_frame: bool = True, retain_raw: bool = True) -> StructureFeatures:
    """Compute, validate and rank the ten coordinates for every ``(session, ticker)``.

    The rank frame of a date is built over the date's eligible names **that carry a complete
    vector**, so all ten coordinates share one denominator and a name the study cannot use does
    not shift the ranks of the names it can. The declaration states the population as the date's
    eligible universe and states separately that a window with any non-finite coordinate leaves
    both the query set and the library; this is the reading where those two sentences agree.
    The alternative frame - rank over every eligible name, coordinate by coordinate - is
    computed as well and reported, so the size of the difference is a measurement, not a claim.
    """
    if eligible.shape != panel.close.shape:
        raise HardFail("R5", f"eligibility mask {eligible.shape} does not match the panel")
    if tuple(rules.feature_names) != FEATURE_NAMES:
        raise HardFail("R1", "declared coordinate names drifted from the code constant")
    lookback = rules.max_lookback
    if dict(FEATURE_LOOKBACK) != {f.name: f.lookback for f in rules.features}:
        raise HardFail("R1", "declared coordinate lookbacks drifted from the code constant")

    raw, zero, bar_complete = compute_raw(panel)
    status, defined = vector_status(raw, zero, eligible, bar_complete, lookback)
    rank = {name: percentile_rank(values, defined) for name, values in raw.items()}
    # The alternative frame is a D1 diagnostic and costs ten more panel-sized arrays. A phase
    # that does not report it (D-V2A-2 and its audits) asks for it not to be built at all:
    # freeing an array later does not lower a process's peak resident size, not allocating does.
    diagnostic = ({name: percentile_rank(values, eligible) for name, values in raw.items()}
                  if diagnostic_frame else {})

    for name in FEATURE_NAMES:
        if not np.isfinite(rank[name][defined]).all():
            raise HardFail("R5", f"{name}: a defined window has a non-finite rank")
    if not retain_raw:
        raw = {}
    return StructureFeatures(FEATURE_NAMES, raw, rank, diagnostic, defined, status, dict(zero),
                             eligible, lookback)


def release(features: "StructureFeatures") -> None:
    """Drop every panel-sized array a phase is finished with.

    Called by D-V2A-2 between the search and the equivalence audits: the audits rebuild the
    coordinates from scratch on their own panels and need the space, and a cleared dict lets the
    allocator hand those pages back instead of growing the process.
    """
    features.raw.clear()
    features.rank.clear()
    features.rank_eligible_population.clear()
    features.zero_denominator.clear()


def rank_frame_difference(features: StructureFeatures) -> dict[str, float]:
    """Largest coordinate difference between the two rank frames, over defined windows only."""
    out: dict[str, float] = {}
    for name in features.names:
        primary = features.rank[name][features.defined]
        alternative = features.rank_eligible_population[name][features.defined]
        delta = np.abs(primary - alternative)
        out[name] = float(delta.max()) if delta.size else 0.0
    return out


def coordinate_lookback_check(names: Sequence[str], max_lookback: int) -> None:
    """R5: no coordinate may read further back than the declared bound."""
    worst = max(FEATURE_LOOKBACK[name] for name in names)
    if worst > max_lookback:
        raise HardFail("R5", f"coordinate lookback {worst} exceeds the declared bound {max_lookback}")
