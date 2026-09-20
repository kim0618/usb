"""S(q) and sigma(q): the forward distribution of a query's historical analogues.

This module is the signal path, and it is deliberately narrow. It sees the neighbours D2 already
chose, the horizon those neighbours were chosen for, and the label values of *those* neighbours -
nothing about the query beyond its date and window. It cannot reach the query's own future,
because it never receives it: ``evaluation.py`` holds that, and an import test forbids this file
from naming it (D3 §15).

D0 fixes both quantities and neither is re-derived here:

    S(q)      = median of neighbour excess_return_h over the accepted top_k
    sigma(q)  = A: mean rho over the accepted top_k
                B: -mean(d) / sqrt(W) over the accepted top_k

sigma is descriptive in V1 - never a gate input and never a filter - and its two forms are not
comparable across representations, so it is only ever read inside one TestId.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog import pit
from app.backtest.strategy_d_analog.models import HardFail

#: Descriptive spread of the neighbour distribution. D0 specifies ddof=1 for the only standard
#: deviation it defines (the N2 realized volatility feature), so the same convention is used.
STD_DDOF = 1
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)


class SignalStatus:
    """Outcomes of one query under one test. ``OK`` means a distribution was built."""

    OK = "OK"
    INSUFFICIENT_VALID_NEIGHBORS = "INSUFFICIENT_VALID_NEIGHBORS"


@dataclass(frozen=True)
class ForwardDistribution:
    """Per query, the aggregate of its analogues' realized excess returns and the two signals."""

    n_total: np.ndarray
    n_valid: np.ndarray
    signal: np.ndarray            # S(q)
    sigma: np.ndarray             # sigma(q)
    mean: np.ndarray
    median: np.ndarray
    std: np.ndarray
    positive_count: np.ndarray
    negative_count: np.ndarray
    zero_count: np.ndarray
    hit_rate: np.ndarray
    quantiles: dict[float, np.ndarray]
    status: np.ndarray            # int8 index into STATUS_ORDER

    def __len__(self) -> int:
        return int(self.n_total.shape[0])


STATUS_ORDER = (SignalStatus.OK, SignalStatus.INSUFFICIENT_VALID_NEIGHBORS)


def pad_by_query(values: np.ndarray, counts: np.ndarray, top_k: int) -> np.ndarray:
    """Lay ragged per-query neighbour values into ``(queries, top_k)``, padding with NaN.

    D2's library already applies label validity, so in practice every row is filled; the padded
    form is kept so a query that did lose a neighbour aggregates correctly instead of silently
    borrowing the next query's values.
    """
    if int(counts.sum()) != values.shape[0]:
        raise HardFail("R5", f"neighbour counts sum to {int(counts.sum())} but "
                             f"{values.shape[0]} values were given")
    if counts.size and int(counts.max()) > top_k:
        raise HardFail("R10", f"a query carries {int(counts.max())} neighbours for top_k {top_k}")
    out = np.full((counts.shape[0], top_k), np.nan, dtype=np.float64)
    if values.size == 0:
        return out
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    rows = np.repeat(np.arange(counts.shape[0]), counts)
    columns = np.arange(values.shape[0]) - np.repeat(starts, counts)
    out[rows, columns] = values
    return out


def sigma_of(representation: str, metric_value: np.ndarray, window: int) -> np.ndarray:
    """D0 ``signal.similarity_confidence``, one form per representation, applied row-wise."""
    with np.errstate(invalid="ignore"):
        mean_metric = np.nanmean(metric_value, axis=1)
    if representation == "A":
        return mean_metric                      # mean rho
    if representation == "B":
        return -mean_metric / np.sqrt(window)   # -mean(d) / sqrt(W)
    raise HardFail("R1", f"unknown representation {representation!r}")


def build(*, representation: str, window: int, horizon: int, query_end_idx: np.ndarray,
          neighbor_end_idx: np.ndarray, neighbor_excess: np.ndarray, metric_value: np.ndarray,
          counts: np.ndarray, top_k: int) -> ForwardDistribution:
    """Aggregate every query's analogues into a distribution, an S and a sigma.

    The embargo is re-checked here rather than trusted from D2: the label of a neighbour is a
    strictly later fact than the neighbour itself, so this is the first point at which a stale
    or mismatched parent artifact could actually leak the future into a signal (D3 §14).
    """
    if counts.shape != query_end_idx.shape:
        raise HardFail("R5", "per-query neighbour counts do not match the query list")
    limits = query_end_idx - window - horizon
    offenders = np.repeat(limits, counts) < neighbor_end_idx
    if offenders.any():
        first = int(np.nonzero(offenders)[0][0])
        view = pit.EmbargoView(int(np.repeat(query_end_idx, counts)[first]), window, horizon)
        view.assert_candidates(np.array([neighbor_end_idx[first]]), "label join")
        raise HardFail("R7", "embargo violated on a neighbour label join")

    excess = pad_by_query(neighbor_excess, counts, top_k)
    metric = pad_by_query(metric_value, counts, top_k)
    valid = np.isfinite(excess)
    n_valid = valid.sum(axis=1).astype(np.int16)
    n_total = counts.astype(np.int16)

    with np.errstate(invalid="ignore"):
        mean = np.nanmean(excess, axis=1)
        median = np.nanmedian(excess, axis=1)
        std = np.nanstd(excess, axis=1, ddof=STD_DDOF)
        quantiles = {q: np.nanquantile(excess, q, axis=1) for q in QUANTILES}
    positive = (excess > 0).sum(axis=1).astype(np.int16)
    negative = (excess < 0).sum(axis=1).astype(np.int16)
    zero = (excess == 0).sum(axis=1).astype(np.int16)
    with np.errstate(invalid="ignore", divide="ignore"):
        hit_rate = np.where(n_valid > 0, positive / np.maximum(n_valid, 1), np.nan)

    status = np.where(n_valid >= top_k, STATUS_ORDER.index(SignalStatus.OK),
                      STATUS_ORDER.index(SignalStatus.INSUFFICIENT_VALID_NEIGHBORS)).astype(np.int8)
    signal = np.where(n_valid >= top_k, median, np.nan)
    sigma = np.where(n_valid >= top_k, sigma_of(representation, metric, window), np.nan)
    return ForwardDistribution(n_total, n_valid, signal, sigma, mean, median, std, positive,
                               negative, zero, hit_rate, quantiles, status)


def assert_signal_is_the_declared_median(distribution: ForwardDistribution) -> None:
    """S(q) is the median of the same distribution that was summarised, not a second statistic."""
    usable = distribution.status == STATUS_ORDER.index(SignalStatus.OK)
    if not usable.any():
        return
    if not np.allclose(distribution.signal[usable], distribution.quantiles[0.50][usable],
                       rtol=0, atol=0, equal_nan=True):
        raise HardFail("R1", "S(q) disagrees with the median of its own distribution")


def status_counts(distribution: ForwardDistribution) -> dict[str, int]:
    return {name: int((distribution.status == index).sum())
            for index, name in enumerate(STATUS_ORDER)}
