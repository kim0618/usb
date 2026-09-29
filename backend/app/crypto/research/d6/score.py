"""LONG_SCORE: four equally weighted categories, each an ordinal 0 / 12 / 25.

The levels come from the contract, not from this file. Nothing here looks at an outcome, so
there is no curve to fit; a category is a lookup from a bucket label or a regime label to the
points the contract assigns it.

SHORT is disabled in V1. `short_score` is None, never 0: a zero would be indistinguishable from
a real SHORT score of zero and would quietly satisfy the separation rule for the wrong reason.
"""
from __future__ import annotations

from typing import Any

from .contract import Contract
from .features import volatility_label
from .model import BucketAssignment, CategoryScore, FeatureValues

OTHER = "other"
NEGATIVE = "negative"


def _bucket_points(levels: dict[str, Any], assignment: BucketAssignment) -> tuple[int, str]:
    label = assignment.label
    if label is not None and label in levels:
        return int(levels[label]), label
    return int(levels[OTHER]), OTHER if label is None else label


def _positioning_points(levels: dict[str, Any], assignment: BucketAssignment,
                        value: float) -> tuple[int, str]:
    """B1 first, then the plain sign test the contract adds for this category alone."""
    label = assignment.label
    if label is not None and label in levels:
        return int(levels[label]), label
    if NEGATIVE in levels and value < 0:
        return int(levels[NEGATIVE]), NEGATIVE
    return int(levels[OTHER]), OTHER if label is None else label


def _volatility_points(levels: dict[str, Any], label: str | None) -> tuple[int, str]:
    if label is None:
        return 0, "UNKNOWN"
    return int(levels[label]), label


def long_categories(contract: Contract, features: FeatureValues,
                    buckets: dict[str, BucketAssignment]) -> list[CategoryScore]:
    """One CategoryScore per contract component, in contract order."""
    values = features.as_dict()
    out: list[CategoryScore] = []
    for component in contract.score_components:
        name, source, levels = component["name"], component["input"], component["levels"]
        value = values[source]
        if source == "f_rv24h":
            label = volatility_label(value, contract.vol_low_below, contract.vol_high_above)
            points, level = _volatility_points(levels, label)
            detail = {"value": value, "label": label,
                      "low_below": contract.vol_low_below,
                      "high_above": contract.vol_high_above}
        else:
            assignment = buckets[source]
            if name == "positioning":
                points, level = _positioning_points(levels, assignment, value)
            else:
                points, level = _bucket_points(levels, assignment)
            detail = {"value": value, "bucket": assignment.label,
                      "cutoffs": assignment.cutoffs}
        out.append(CategoryScore(name=name, input=source, points=points,
                                 max_points=int(component["max"]), level=level, detail=detail))
    return out


def long_score(categories: list[CategoryScore]) -> int:
    return sum(c.points for c in categories)


def short_score(contract: Contract) -> None:
    """V1 has no SHORT score. Explicitly None so callers cannot mistake it for zero."""
    if contract.short_enabled:
        raise NotImplementedError("SHORT is disabled in V1; enabling it needs a new contract")
    return None


def mandatory_failures(contract: Contract, categories: list[CategoryScore]) -> list[str]:
    """Reason codes for every mandatory minimum the categories do not meet."""
    by_name = {c.name: c for c in categories}
    failures: list[str] = []
    for rule in contract.mandatory_minimums:
        category = by_name.get(rule["component"])
        if category is None:
            failures.append(f"{rule['id']}_{rule['component'].upper()}_MISSING")
        elif category.points < int(rule["min"]):
            failures.append(f"{rule['id']}_{rule['component'].upper()}_BELOW_MIN")
    return failures


def separation_ok(contract: Contract, long_points: int, short_points: int | None) -> bool:
    """With SHORT disabled the rule is vacuously satisfied, and that is recorded, not assumed."""
    if short_points is None:
        return True
    return (long_points - short_points) >= contract.score_separation_min
