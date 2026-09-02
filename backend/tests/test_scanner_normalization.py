from math import isfinite

import pytest

from app.core.exceptions import DataError
from app.scanner.normalization import normalize_metrics


def raw(value: float) -> dict[str, float]:
    return {
        "rvol": max(0.0, value + 2.0),
        "relative_strength": value,
        "dollar_volume": max(1.0, (value + 2.0) * 1_000_000),
        "momentum": value / 2,
    }


@pytest.mark.parametrize("size", [1, 2, 7, 8, 9])
def test_normalization_handles_small_candidate_pools(size: int) -> None:
    values = {f"S{index:03d}": raw(float(index)) for index in range(size)}
    result = normalize_metrics(values, 5.0, 95.0)
    assert len(result) == size
    assert all(isfinite(metric) for item in result.values() for metric in item.values())


def test_normalization_handles_zero_variance() -> None:
    result = normalize_metrics({"AAA": raw(1.0), "BBB": raw(1.0)}, 5.0, 95.0)
    assert all(metric == 0.0 for item in result.values() for metric in item.values())


def test_extreme_heavy_tail_values_are_finite_and_winsorized() -> None:
    values = {f"S{index}": raw(float(index)) for index in range(10)}
    values["OUTLIER"] = {
        "rvol": 1e100,
        "relative_strength": 1e10,
        "dollar_volume": 1e200,
        "momentum": 1e10,
    }
    result = normalize_metrics(values, 5.0, 95.0)
    assert all(isfinite(metric) for item in result.values() for metric in item.values())
    assert max(abs(metric) for item in result.values() for metric in item.values()) < 10


def test_log_transformed_metric_rejects_negative_input() -> None:
    values = {"AAA": raw(1.0)}
    values["AAA"]["rvol"] = -0.1
    with pytest.raises(DataError, match="non-negative"):
        normalize_metrics(values, 5.0, 95.0)
