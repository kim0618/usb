"""E-MAX-M1 candidate orderings, exactly as M0 declared them.

Every function orders one session's sealed H5 candidates using only their sealed 09:25 H5 inputs.
Ties always fall back to canonical symbol ascending. Nothing here reads an outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

R0, R1, R2, R3 = "R0", "R1", "R2", "R3"
RULES = (R0, R1, R2, R3)
COMPOSITE_FEATURES = ("premarket_rvol", "return_0900_0925", "position_in_premarket_range")


def descending_average_ranks(values: Mapping[str, float]) -> dict[str, float]:
    """1 = largest; tied values share the average of the positions they occupy."""
    ordered = sorted(values, key=lambda symbol: (-values[symbol], symbol))
    ranks: dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and values[ordered[j + 1]] == values[ordered[i]]:
            j += 1
        average = (i + 1 + j + 1) / 2.0
        for symbol in ordered[i:j + 1]:
            ranks[symbol] = average
        i = j + 1
    return ranks


def order(rule: str, candidates: Sequence[str],
          features: Mapping[str, Mapping[str, float]]) -> tuple[str, ...]:
    """The rule's priority order over ``candidates``; ``features[symbol][name]`` are sealed values."""
    symbols = sorted(candidates)
    if rule == R0:
        return tuple(symbols)
    if rule == R1:
        return tuple(sorted(symbols, key=lambda s: (-features[s]["premarket_rvol"], s)))
    if rule == R2:
        return tuple(sorted(symbols, key=lambda s: (-features[s]["return_0900_0925"], s)))
    if rule == R3:
        ranks = [descending_average_ranks({s: features[s][name] for s in symbols})
                 for name in COMPOSITE_FEATURES]
        score = {s: sum(r[s] for r in ranks) for s in symbols}
        return tuple(sorted(symbols, key=lambda s: (score[s], s)))
    raise ValueError(f"{rule} is not an E-MAX-M1 ranking")


def select(rule: str, candidates: Sequence[str], features: Mapping[str, Mapping[str, float]],
           capacity: int = 3) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(selected, not selected) in priority order; no replacement."""
    ranked = order(rule, candidates, features)
    return ranked[:capacity], ranked[capacity:]
