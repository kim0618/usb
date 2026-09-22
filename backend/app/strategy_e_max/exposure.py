"""E-MAX-M5 global exposure multiplier, composed on top of the B2 breadth multiplier.

``final session exposure = global multiplier x breadth multiplier``. Every position weight and
every scenario return (each already net of its round-trip cost) is multiplied by the global
multiplier, so costs scale with notional: levered net = exposure x (gross - cost). The trade set
is untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from decimal import Decimal
from fractions import Fraction
from typing import Any

MULTIPLIERS = {"E1": Fraction(1), "E15": Fraction(3, 2), "E20": Fraction(2)}
M0_IDS = {"E1": "L1", "E15": "L2", "E20": "L3"}
CATASTROPHIC = "CATASTROPHIC_SESSION_LOSS"


def apply_global(session: Mapping[str, Any], g: Fraction) -> dict[str, Any]:
    out = copy.deepcopy(dict(session))
    breadth = Fraction(session.get("breadth_multiplier", "1/1"))
    out["global_multiplier"] = f"{g.numerator}/{g.denominator}"
    final = breadth * g
    out["final_exposure_multiplier"] = f"{final.numerator}/{final.denominator}"
    if g == 1:
        return out
    dg = Decimal(g.numerator) / Decimal(g.denominator)
    out["returns"] = {name: value * dg for name, value in session["returns"].items()}
    out["exposure"] = str(Fraction(session["exposure"]) * g)
    for record in out["records"]:
        w = Fraction(record["weight"]) * g
        record["weight"] = f"{w.numerator}/{w.denominator}"
    return out


def catastrophic(sessions, scenario: str = "COST_10BP") -> list[str]:
    """Sessions whose levered return is <= -100% (equity would reach zero); never clipped."""
    return [s["session"] for s in sessions if s["returns"][scenario] <= Decimal(-1)]
