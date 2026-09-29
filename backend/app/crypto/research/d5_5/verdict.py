"""S1 to S10, read off the frozen contract.

S10 is the one that carries this study's question. D5.4 found no direction information at the
event instant; if waiting for confirmation supplies any, fewer than half the trades should end at
the structural stop. A stop share near 50% would say the coin flip simply moved later.
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

RATIO_MIN = 2.0
SINGLE_TRADE_SHARE_MAX = 0.20
PF_MIN = 1.3
MDD_MAX = 0.20
STOP_SHARE_MAX = 0.50


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
             scenarios: dict[str, float], sensitivity: dict[str, dict[str, Any]],
             run, stop_share: float | None) -> list[dict[str, Any]]:
    gate = contract["sample_gate"]
    out: list[dict[str, Any]] = []

    mean, low = ci.get("mean"), ci.get("lo")
    status = NOT_EVALUABLE if (mean is None or low is None) else \
        (PASS if (mean > 0 and low > 0) else FAIL)
    out.append(_row(contract, "S1", status,
                    {"net_bp_mean": mean, "ci95_low": low, "ci95_high": ci.get("hi")}))

    ratio = main.get("edge_to_cost_ratio")
    status = NOT_EVALUABLE if (ratio is None or ratio == NOT_MEANINGFUL) else \
        (PASS if ratio >= RATIO_MIN else FAIL)
    out.append(_row(contract, "S2", status,
                    {"edge_to_cost_ratio": ratio, "required": RATIO_MIN,
                     "gross_bp_mean": main.get("gross_bp_mean"),
                     "net_bp_mean": main.get("average_trade_bp")}))

    judgeable = [f for f in folds if f["trades"] >= gate["min_fold_trades"]]
    trades = main.get("trades", 0)
    ok = (run.events_in_oos >= gate["min_events"]
          and trades >= gate["min_confirmed_trades"]
          and len(years) >= gate["min_years"]
          and len(judgeable) >= gate["min_evaluable_folds"])
    out.append(_row(contract, "S3", PASS if ok else FAIL,
                    {"events": run.events_in_oos, "events_required": gate["min_events"],
                     "trades": trades, "trades_required": gate["min_confirmed_trades"],
                     "years_present": len(years), "years_required": gate["min_years"],
                     "judgeable_folds": len(judgeable),
                     "folds_required": gate["min_evaluable_folds"]}))

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

    status = NOT_EVALUABLE if not loyo else \
        (PASS if all(value > 0 for value in loyo.values()) else FAIL)
    out.append(_row(contract, "S5", status, {"leave_one_year_out_net_bp": loyo}))

    share = concentration.get("top_1_trade_share")
    status = NOT_EVALUABLE if (share is None or share == NOT_MEANINGFUL) else \
        (PASS if share <= SINGLE_TRADE_SHARE_MAX else FAIL)
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
    status = NOT_EVALUABLE if stress is None else (PASS if stress > 0 else FAIL)
    out.append(_row(contract, "S8", status, {"VIP0_STRESS_bp": stress}))

    arms = {name: block.get("average_trade_bp") for name, block in sensitivity.items()}
    status = NOT_EVALUABLE if (not arms or any(v is None for v in arms.values())) else \
        (PASS if all(v > 0 for v in arms.values()) else FAIL)
    out.append(_row(contract, "S9", status, {"arm_net_bp": arms}))

    status = NOT_EVALUABLE if stop_share is None else \
        (PASS if stop_share < STOP_SHARE_MAX else FAIL)
    out.append(_row(contract, "S10", status,
                    {"stop_share": stop_share, "limit": STOP_SHARE_MAX,
                     "note": "the coin-flip test: confirmation must do better than chance at "
                             "surviving its own structural stop"}))
    return out


def classify(rules: list[dict[str, Any]], trades: int, contract: dict) -> dict[str, Any]:
    floor = contract["sample_gate"]["min_confirmed_trades"]
    failed = [r["id"] for r in rules if r["status"] == FAIL]
    unknown = [r["id"] for r in rules if r["status"] == NOT_EVALUABLE]
    if trades < floor:
        return {"verdict": INCONCLUSIVE,
                "reason": f"{trades} confirmed trades, below the {floor} the contract requires",
                "failed": failed, "not_evaluable": unknown}
    s1 = next(r for r in rules if r["id"] == "S1")
    if not failed and not unknown:
        verdict = SURVIVE
    elif s1["status"] == PASS:
        verdict = WEAK
    else:
        verdict = REJECT
    return {"verdict": verdict, "failed": failed, "not_evaluable": unknown,
            "s1": s1["status"]}
