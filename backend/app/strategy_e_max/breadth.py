"""E-MAX-M3 breadth exposure, exactly as M0 declared it.

Breadth never adds or removes a trade. It only scales an already replayed session by a fixed
multiplier: 1.5 when the 09:25 H5 rate over the PIT universe is at least the frozen threshold and
the universe has at least 100 rows, 1.0 otherwise. Scaling multiplies every position weight and
every scenario return by the same factor, so costs scale with notional.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from decimal import Decimal
from fractions import Fraction
from typing import Any

THRESHOLD = 0.030741
MIN_UNIVERSE_ROWS = 100
HIGH_MULTIPLIER = Fraction(3, 2)
BASE_MULTIPLIER = Fraction(1, 1)


def h5_rate(candidates: int, universe_rows: int) -> float | None:
    """Defined only for universes of at least 100 rows (the M0 coverage guard)."""
    if universe_rows < MIN_UNIVERSE_ROWS:
        return None
    return candidates / universe_rows


def multiplier(candidates: int, universe_rows: int) -> Fraction:
    rate = h5_rate(candidates, universe_rows)
    return HIGH_MULTIPLIER if rate is not None and rate >= THRESHOLD else BASE_MULTIPLIER


def scale(session: Mapping[str, Any], k: Fraction) -> dict[str, Any]:
    """The same session at ``k`` times its normalized exposure; trades and prices untouched."""
    out = copy.deepcopy(dict(session))
    out["breadth_multiplier"] = f"{k.numerator}/{k.denominator}"
    if k == 1:
        return out
    dk = Decimal(k.numerator) / Decimal(k.denominator)
    out["returns"] = {name: value * dk for name, value in session["returns"].items()}
    out["exposure"] = str(Fraction(session["exposure"]) * k)
    for record in out["records"]:
        w = Fraction(record["weight"]) * k
        record["weight"] = f"{w.numerator}/{w.denominator}"
    return out
