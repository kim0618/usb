"""GATE-D-AGG-SCREEN as a pure function: numbers in, verdict out.

The thresholds are parsed from the declaration's own wording (``d_agg_rules_v1.json`` ``gate``),
checked against the values the D0 contract prints, and never written as literals elsewhere. The
gate reads a mapping of statistics that were all computed before it runs; nothing it decides can
change how a statistic is made. This module opens no file and imports nothing from ``app``
except this package's rule loader types and ``HardFail``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from app.backtest.strategy_d_agg.models import HardFail

#: What the D0 contract prints (section 9). The parser must reproduce these exactly.
EXPECTED = {
    "H1": (150.0, 5000.0, 500.0, 250.0), "H3": (0.02, 2.0), "H4": 1.00, "H5": 1.00, "H6": 1.00,
    "H7": 0.05,
    "T1": (1.25, 1.10), "T2": (1.00, 0.95), "T3": (3.0, 2.0), "T4": (1.10, 1.00),
    "T5": (1.10, 1.00), "T6": (1.10, 1.00),
}


def _numbers(text: str) -> tuple[float, ...]:
    return tuple(float(x) for x in re.findall(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?", text))


def _after_last_operator(text: str) -> float:
    """The number that follows the last comparison operator: the threshold of that clause."""
    found = re.findall(r"(?:>=|<=|>|<)\s*(\d+(?:\.\d+)?)", text)
    if not found:
        raise HardFail("R1", f"no threshold in {text!r}")
    return float(found[-1])


@dataclass(frozen=True)
class GateSpec:
    h1: tuple[float, float, float, float]
    h3_share: float
    h3_multiple: float
    h7: float
    t1: tuple[float, float]
    t2: tuple[float, float]
    t3: tuple[float, float]
    t4: tuple[float, float]
    t5: tuple[float, float]
    t6: tuple[float, float]


def spec_from_rules(raw: Mapping[str, Any]) -> GateSpec:
    gate = raw["gate"]
    hard, tiered = gate["hard"], gate["tiered"]
    h1 = _numbers(hard["H1_sample"])
    h3 = _numbers(hard["H3_label_missingness"])
    h7 = _numbers(hard["H7_single_ticker"])
    parsed = {"H1": h1, "H3": h3[:2], "H4": _numbers(hard["H4_direction"])[0],
              "H5": _numbers(hard["H5_not_pure_volatility_tail"])[0],
              "H6": _numbers(hard["H6_not_pure_volatility_median"])[0], "H7": h7[0]}
    keys = {"T1": "T1_tail_lift", "T2": "T2_tail_lift_ci", "T3": "T3_block_stability",
            "T4": "T4_net_tail_lift", "T5": "T5_session_concentration",
            "T6": "T6_ticker_concentration"}
    for short, key in keys.items():
        parsed[short] = (_after_last_operator(tiered[key]["pass"]),
                         _after_last_operator(tiered[key]["floor"]))
    for name, want in EXPECTED.items():
        if parsed[name] != want:
            raise HardFail("R1", f"gate {name} parsed {parsed[name]} != declared {want}")
    return GateSpec(h1, h3[0], h3[1], h7[0], parsed["T1"], parsed["T2"], parsed["T3"],
                    parsed["T4"], parsed["T5"], parsed["T6"])


REQUIRED = ("evaluable_sessions", "valid_setup_rows", "unique_setup_tickers", "setup_up_events",
            "pit_violations", "setup_invalid_share", "universe_invalid_share", "TL", "NTL", "AG",
            "single_ticker_share", "TL_ci_low", "TL_ci_high", "blocks_tl_above_one",
            "leave_top5_sessions_TL", "leave_top10_tickers_TL")


def evaluate(m: Mapping[str, Any], spec: GateSpec) -> dict[str, Any]:
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        raise HardFail("R5", f"gate missing statistics {missing}")
    h2 = m["pit_violations"] == 0
    hard = {
        "H1": (m["evaluable_sessions"] >= spec.h1[0] and m["valid_setup_rows"] >= spec.h1[1]
               and m["unique_setup_tickers"] >= spec.h1[2] and m["setup_up_events"] >= spec.h1[3]),
        "H2": h2,
        "H3": (m["setup_invalid_share"] <= spec.h3_share
               and m["setup_invalid_share"] <= spec.h3_multiple * m["universe_invalid_share"]),
        "H4": m["TL"] > 1.00,
        "H5": m["NTL"] >= 1.00,
        "H6": m["AG"] >= 1.00,
        "H7": m["single_ticker_share"] <= spec.h7,
    }
    tiers = {
        "T1": (m["TL"] >= spec.t1[0], m["TL"] >= spec.t1[1]),
        "T2": (m["TL_ci_low"] > spec.t2[0], m["TL_ci_low"] > spec.t2[1]),
        "T3": (m["blocks_tl_above_one"] >= spec.t3[0], m["blocks_tl_above_one"] >= spec.t3[1]),
        "T4": (m["NTL"] >= spec.t4[0], m["NTL"] >= spec.t4[1]),
        "T5": (m["leave_top5_sessions_TL"] >= spec.t5[0], m["leave_top5_sessions_TL"] > spec.t5[1]),
        "T6": (m["leave_top10_tickers_TL"] >= spec.t6[0], m["leave_top10_tickers_TL"] > spec.t6[1]),
    }
    inverse = m["TL_ci_high"] < 1.00
    below_pass = [k for k, (p, _) in tiers.items() if not p]
    below_floor = [k for k, (_, f) in tiers.items() if not f]
    if not h2:
        verdict, reason = "D_AGG_SCREEN_FAIL", "H2 PIT violation: evaluation stops"
    elif inverse:
        verdict, reason = "INVERSE_EFFECT", "TL 95% CI upper bound < 1.00"
    elif not all(hard.values()):
        verdict, reason = "D_AGG_SCREEN_FAIL", f"hard conditions failed {[k for k, v in hard.items() if not v]}"
    elif not below_pass:
        verdict, reason = "D_AGG_SCREEN_PASS", "every H and every T at pass level"
    elif not below_floor and len(below_pass) == 1:
        verdict, reason = "D_AGG_SCREEN_BORDERLINE", f"exactly one T below pass level {below_pass}"
    else:
        verdict, reason = "D_AGG_SCREEN_FAIL", (f"T below pass {below_pass}, below floor {below_floor}")
    return {"verdict": verdict, "reason": reason, "hard": hard,
            "tiered": {k: {"pass": p, "floor": f} for k, (p, f) in tiers.items()},
            "below_pass": below_pass, "below_floor": below_floor, "inverse_effect": inverse,
            "counts_as_fail": verdict in ("D_AGG_SCREEN_FAIL", "INVERSE_EFFECT"),
            "secondary_cannot_overturn": True}
