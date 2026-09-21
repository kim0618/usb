"""D-AGG-4 pure evaluation: noise reproduction, primary statistics, GATE-D-AGG-BACKTEST.

No file access. Numbers in, numbers out. The gate reads a finished mapping of statistics; nothing
it decides can change how a statistic was made. There is no BORDERLINE state (one-time borderline
decision); a statistically marginal result is FAIL; INCONCLUSIVE_TECHNICAL exists only for an
execution that could not produce the statistics.
"""

from collections.abc import Callable, Mapping, Sequence
import re
from typing import Any

import numpy as np

from app.backtest.strategy_d_agg.models import HardFail

#: D3 noise record (D_AGG_TRADING_CONTROL_CONTRACT_V1 section 4.1), printed to 6 decimals.
D3_NOISE = {"retained_sessions": 221, "valid_setup_trades": 6619, "sd_delta_session": 0.014155,
            "se_block": 0.000886}
#: Declared in D-AGG-4 before any backtest statistic existed: half a unit of the printed precision.
NOISE_TOLERANCE = 5e-7
NOISE_SEED = 2026092100
PRIMARY_SEED = 2026092103
EXPECTED_P = {"P1": (150.0, 5.0, 5000.0, 0.02, 2.0), "P7": 0.05}


def noise(delta: np.ndarray, draws: np.ndarray) -> dict[str, float]:
    dm = delta - delta.mean()
    rep = dm[draws].mean(axis=1)
    sd = float(np.std(dm, ddof=1))
    return {"sd_delta_session": sd, "se_block": float(np.std(rep, ddof=1)),
            "se_iid": sd / float(np.sqrt(delta.size))}


def noise_reproduction(measured: Mapping[str, float], retained: int, trades: int) -> dict[str, Any]:
    checks = {
        "retained_sessions": retained == D3_NOISE["retained_sessions"],
        "valid_setup_trades": trades == D3_NOISE["valid_setup_trades"],
        "sd_delta_session": abs(measured["sd_delta_session"] - D3_NOISE["sd_delta_session"]) <= NOISE_TOLERANCE,
        "se_block": abs(measured["se_block"] - D3_NOISE["se_block"]) <= NOISE_TOLERANCE,
    }
    return {"expected": dict(D3_NOISE), "reproduced": {**{k: float(v) for k, v in measured.items()},
                                                       "retained_sessions": retained, "valid_setup_trades": trades},
            "abs_error": {k: abs(measured[k] - D3_NOISE[k]) for k in ("sd_delta_session", "se_block")},
            "tolerance": NOISE_TOLERANCE, "checks": checks, "match": all(checks.values())}


def percentile_ci(values: np.ndarray) -> tuple[float, float]:
    low, high = np.quantile(values, [0.025, 0.975])
    return float(low), float(high)


def top_positions(values: np.ndarray, count: int) -> np.ndarray:
    order = np.lexsort((np.arange(values.size), -values))
    return np.sort(order[:count])


def spec_from_contract(raw: Mapping[str, Any]) -> dict[str, Any]:
    cond = raw["backtest_gate"]["conditions"]
    p1 = tuple(float(x) for x in re.findall(r"(?:>=|<=|x)\s*(\d+(?:\.\d+)?)", cond["P1_validity"]))
    # P1 text: sessions >= 150, dropped <= 5, trades >= 5000, share <= 0.02, <= 2 x
    p1 = tuple(v for v in p1)
    p7 = float(re.findall(r"<=\s*(\d+(?:\.\d+)?)", cond["P7_concentration"])[0])
    parsed = {"P1": p1[:5], "P7": p7}
    if parsed != EXPECTED_P:
        raise HardFail("R1", f"backtest gate parsed {parsed} != declared {EXPECTED_P}")
    if raw["backtest_gate"].get("no_borderline") is None or raw["backtest_gate"]["secondary_cannot_overturn"] is not True:
        raise HardFail("R1", "backtest gate lost its no-borderline / secondary rules")
    return parsed


REQUIRED = ("pit_violations", "retained_sessions", "dropped_sessions", "valid_setup_trades",
            "setup_no_trade_share", "control_no_trade_share", "mean_setup_net", "mean_delta",
            "delta_ci_low", "blocks_delta_positive", "delta_without_top5_sessions",
            "delta_without_top10_tickers", "single_ticker_positive_share")


def evaluate(m: Mapping[str, Any], spec: Mapping[str, Any], *, technical_failure: str | None = None) -> dict[str, Any]:
    if technical_failure:
        return {"verdict": "D_AGG_BACKTEST_INCONCLUSIVE_TECHNICAL", "reason": technical_failure,
                "conditions": {}, "secondary_cannot_overturn": True}
    missing = [k for k in REQUIRED if k not in m]
    if missing:
        raise HardFail("R5", f"gate missing {missing}")
    s150, d5, t5000, share, mult = spec["P1"]
    cond = {
        "P1": (m["pit_violations"] == 0 and m["retained_sessions"] >= s150 and m["dropped_sessions"] <= d5
               and m["valid_setup_trades"] >= t5000 and m["setup_no_trade_share"] <= share
               and m["setup_no_trade_share"] <= mult * m["control_no_trade_share"]),
        "P2": m["mean_setup_net"] > 0,
        "P3": m["mean_delta"] > 0,
        "P4": m["delta_ci_low"] > 0,
        "P5": m["blocks_delta_positive"] >= 3,
        "P6": m["delta_without_top5_sessions"] > 0 and m["delta_without_top10_tickers"] > 0,
        "P7": m["single_ticker_positive_share"] <= spec["P7"],
    }
    passed = all(cond.values())
    return {"verdict": "D_AGG_BACKTEST_PASS" if passed else "D_AGG_BACKTEST_FAIL",
            "reason": "P1..P7 all hold" if passed else f"failed {[k for k, v in cond.items() if not v]}",
            "conditions": cond, "secondary_cannot_overturn": True}
