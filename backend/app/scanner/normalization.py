"""Deterministic cross-sectional normalization for Quant V0."""

from collections.abc import Mapping
from math import isfinite, log1p

import numpy as np

from app.core.exceptions import DataError


LOG_TRANSFORMED_METRICS = frozenset({"rvol", "dollar_volume"})


def normalize_metric(
    values: Mapping[str, float], lower_percentile: float, upper_percentile: float
) -> dict[str, float]:
    if not values:
        return {}
    symbols = sorted(values)
    if any(values[symbol] < 0 for symbol in symbols):
        raise DataError("Log-transformed metric must be non-negative")
    transformed = np.asarray(
        [log1p(values[symbol]) for symbol in symbols],
        dtype=np.float64,
    )
    if not np.isfinite(transformed).all():
        raise DataError("Normalization input contains NaN or infinity")
    lower, upper = np.percentile(transformed, [lower_percentile, upper_percentile])
    clipped = np.clip(transformed, lower, upper)
    standard_deviation = float(np.std(clipped))
    if standard_deviation == 0.0:
        normalized = np.zeros_like(clipped)
    else:
        normalized = (clipped - float(np.mean(clipped))) / standard_deviation
    result = {symbol: float(value) for symbol, value in zip(symbols, normalized, strict=True)}
    if not all(isfinite(value) for value in result.values()):
        raise DataError("Normalization produced NaN or infinity")
    return result


def normalize_metrics(
    raw_by_symbol: Mapping[str, Mapping[str, float]],
    lower_percentile: float,
    upper_percentile: float,
) -> dict[str, dict[str, float]]:
    if not raw_by_symbol:
        return {}
    metric_names = ("rvol", "relative_strength", "dollar_volume", "momentum")
    output = {symbol: {} for symbol in sorted(raw_by_symbol)}
    for metric in metric_names:
        raw_values = {symbol: values[metric] for symbol, values in raw_by_symbol.items()}
        if metric not in LOG_TRANSFORMED_METRICS:
            # normalize_metric applies log only to non-negative input, so signed metrics
            # use this local shift-free implementation through a sign sentinel wrapper.
            normalized = _normalize_signed(raw_values, lower_percentile, upper_percentile)
        else:
            normalized = normalize_metric(raw_values, lower_percentile, upper_percentile)
        for symbol, value in normalized.items():
            output[symbol][metric] = value
    return output


def _normalize_signed(
    values: Mapping[str, float], lower_percentile: float, upper_percentile: float
) -> dict[str, float]:
    symbols = sorted(values)
    data = np.asarray([values[symbol] for symbol in symbols], dtype=np.float64)
    if not np.isfinite(data).all():
        raise DataError("Normalization input contains NaN or infinity")
    lower, upper = np.percentile(data, [lower_percentile, upper_percentile])
    clipped = np.clip(data, lower, upper)
    standard_deviation = float(np.std(clipped))
    normalized = (
        np.zeros_like(clipped)
        if standard_deviation == 0.0
        else (clipped - float(np.mean(clipped))) / standard_deviation
    )
    return {symbol: float(value) for symbol, value in zip(symbols, normalized, strict=True)}
