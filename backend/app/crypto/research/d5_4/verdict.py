"""S1 to S9, read off the frozen contract, and the SURVIVE / WEAK / REJECT / INCONCLUSIVE call.

No rule is invented here. Each returns PASS, FAIL or NOT_EVALUABLE, and NOT_EVALUABLE is never
counted as a pass. The metric helpers come from the D6-C package so that a number reported here
means the same thing it meant there.
"""
from __future__ import annotations

from typing import Any

from ..d6c.metrics import NOT_MEANINGFUL

PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUABLE = "NOT_EVALUABLE"

SURVIVE = "SURVIVE"
WEAK = "WEAK"
REJECT = "REJECT"
INCONCLUSIVE = "INCONCLUSIVE"

MIN_TRADES = 100
MIN_JUDGEABLE_FOLDS = 6
MIN_FOLD_TRADES = 10
RATIO_MIN = 2.0
SINGLE_TRADE_SHARE_MAX = 0.20
PF_MIN = 1.3
MDD_MAX = 0.20
YEARS_REQUIRED = 5


def _rule(contract: dict, rule_id: str) -> str:
    for row in contract["verdict_rules"]["SURVIVE"]:
        if row["id"] == rule_id:
            return row["rule"]
    raise KeyError(rule_id)


def _row(contract: dict, rule_id: str, status: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"id": rule_id, "rule": _rule(contract, rule_id), "status": status, "detail": detail}


def evaluate(contract: dict, *, main: dict[str, Any], ci: dict[str, Any],
             folds: list[dict[str, Any]], years: list[dict[str, Any]],
             concentration: dict[str, Any], loyo: dict[str, float],
             scenarios: dict[str, float],
             sensitivity: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    mean, low = ci.get("mean"), ci.get("lo")
    if mean is None or low is None:
        status = NOT_EVALUABLE
    else:
        status = PASS if (mean > 0 and low > 0) else FAIL
    out.append(_row(contract, "S1", status,
                    {"net_bp_mean": mean, "ci95_low": low, "ci95_high": ci.get("hi")}))

    ratio = main.get("edge_to_cost_ratio")
    if ratio is None or ratio == NOT_MEANINGFUL:
        status = NOT_EVALUABLE
    else:
        status = PASS if ratio >= RATIO_MIN else FAIL
    out.append(_row(contract, "S2", status,
                    {"edge_to_cost_ratio": ratio, "required": RATIO_MIN,
                     "gross_bp_mean": main.get("gross_bp_mean"),
                     "net_bp_mean": main.get("average_trade_bp")}))

    judgeable = [f for f in folds if f["trades"] >= MIN_FOLD_TRADES]
    trades = main.get("trades", 0)
    years_present = len(years)
    ok = (trades >= MIN_TRADES and years_present >= YEARS_REQUIRED
          and len(judgeable) >= MIN_JUDGEABLE_FOLDS)
    out.append(_row(contract, "S3", PASS if ok else FAIL,
                    {"trades": trades, "trades_required": MIN_TRADES,
                     "years_present": years_present, "years_required": YEARS_REQUIRED,
                     "judgeable_folds": len(judgeable), "folds_required": MIN_JUDGEABLE_FOLDS,
                     "fold_trade_floor": MIN_FOLD_TRADES}))

    if not judgeable:
        out.append(_row(contract, "S4", NOT_EVALUABLE, {"judgeable_folds": 0}))
    else:
        gross_positive = sum(1 for f in judgeable if (f["gross_bp_mean"] or 0) > 0)
        net_positive = sum(1 for f in judgeable if (f["average_trade_bp"] or 0) > 0)
        ok = (gross_positive / len(judgeable) >= 2 / 3
              and net_positive / len(judgeable) >= 0.5)
        out.append(_row(contract, "S4", PASS if ok else FAIL,
                        {"judgeable_folds": len(judgeable), "gross_positive": gross_positive,
                         "net_positive": net_positive}))

    if not loyo:
        status = NOT_EVALUABLE
    else:
        status = PASS if all(value > 0 for value in loyo.values()) else FAIL
    out.append(_row(contract, "S5", status, {"leave_one_year_out_net_bp": loyo}))

    share = concentration.get("top_1_trade_share")
    if share is None or share == NOT_MEANINGFUL:
        status = NOT_EVALUABLE
    else:
        status = PASS if share <= SINGLE_TRADE_SHARE_MAX else FAIL
    out.append(_row(contract, "S6", status,
                    {"top_1_trade_share": share, "limit": SINGLE_TRADE_SHARE_MAX,
                     "top_1_trade_usdt": concentration.get("top_1_trade_usdt"),
                     "total_net_usdt": concentration.get("total_net_usdt")}))

    pf, mdd = main.get("profit_factor"), main.get("mdd")
    if pf is None or pf == NOT_MEANINGFUL or mdd is None:
        status = NOT_EVALUABLE
    else:
        pf_value = float("inf") if pf == "INF" else pf
        status = PASS if (pf_value > PF_MIN and mdd < MDD_MAX) else FAIL
    out.append(_row(contract, "S7", status,
                    {"profit_factor": pf, "mdd": mdd, "pf_required": PF_MIN,
                     "mdd_limit": MDD_MAX}))

    stress = scenarios.get("VIP0_STRESS")
    if stress is None:
        status = NOT_EVALUABLE
    else:
        status = PASS if stress > 0 else FAIL
    out.append(_row(contract, "S8", status, {"VIP0_STRESS_bp": stress}))

    arms = {name: block.get("average_trade_bp") for name, block in sensitivity.items()}
    if not arms or any(value is None for value in arms.values()):
        status = NOT_EVALUABLE
    else:
        status = PASS if all(value > 0 for value in arms.values()) else FAIL
    out.append(_row(contract, "S9", status, {"arm_net_bp": arms}))
    return out


def classify(rules: list[dict[str, Any]], trades: int) -> dict[str, Any]:
    """SURVIVE needs every rule; below that the S1 result decides WEAK against REJECT."""
    if trades < MIN_TRADES:
        return {"verdict": INCONCLUSIVE,
                "reason": f"{trades} OOS trades, below the {MIN_TRADES} the contract requires",
                "failed": [r["id"] for r in rules if r["status"] == FAIL],
                "not_evaluable": [r["id"] for r in rules if r["status"] == NOT_EVALUABLE]}
    failed = [r["id"] for r in rules if r["status"] == FAIL]
    unknown = [r["id"] for r in rules if r["status"] == NOT_EVALUABLE]
    s1 = next(r for r in rules if r["id"] == "S1")
    if not failed and not unknown:
        verdict = SURVIVE
    elif s1["status"] == PASS:
        verdict = WEAK
    else:
        verdict = REJECT
    return {"verdict": verdict, "failed": failed, "not_evaluable": unknown,
            "s1": s1["status"]}
