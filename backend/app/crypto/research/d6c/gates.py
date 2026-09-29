"""G1 to G11, read straight off the D6-A contract. No gate is invented here.

Each gate returns PASS, FAIL or NOT_EVALUABLE, and NOT_EVALUABLE never counts as PASS. The
overall verdict is PASS only when G1 to G10 all pass; G11 is a sanity note on the no-stop arm and
carries no authority over the verdict.
"""
from __future__ import annotations

from typing import Any

from .metrics import NOT_MEANINGFUL

PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUABLE = "NOT_EVALUABLE"


def _result(gate_id: str, rule: str, status: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"id": gate_id, "rule": rule, "status": status, "detail": detail}


def _rule(contract, gate_id: str) -> str:
    for gate in contract.strategy.doc["verdict_gates"]:
        if gate["id"] == gate_id:
            return gate["rule"]
    raise KeyError(gate_id)


def evaluate(contract, *, main: dict[str, Any], ci: dict[str, Any],
             folds: list[dict[str, Any]], years: list[dict[str, Any]],
             regimes: dict[str, Any], concentration: dict[str, Any],
             loyo: dict[str, float], scenarios: dict[str, float],
             sensitivity: dict[str, dict[str, Any]],
             nostop: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    R = lambda gid: _rule(contract, gid)  # noqa: E731

    # G1 net mean and CI lower bound both above zero -------------------------------------
    mean, lo = ci.get("mean"), ci.get("lo")
    if mean is None:
        status = NOT_EVALUABLE
    elif lo is None:
        status = NOT_EVALUABLE
    else:
        status = PASS if (mean > 0 and lo > 0) else FAIL
    out.append(_result("G1", R("G1"), status, {"net_bp_mean": mean, "ci95_low": lo,
                                               "ci95_high": ci.get("hi")}))

    # G2 edge to cost ratio ---------------------------------------------------------------
    ratio = main.get("edge_to_cost_ratio")
    if ratio is None or ratio == NOT_MEANINGFUL:
        status = NOT_EVALUABLE
    else:
        status = PASS if ratio >= 1.2 else FAIL
    out.append(_result("G2", R("G2"), status, {"edge_to_cost_ratio": ratio,
                                               "gross_bp_mean": main.get("gross_bp_mean"),
                                               "net_bp_mean": main.get("average_trade_bp")}))

    # G3 fold direction and net consistency -----------------------------------------------
    judgeable = [f for f in folds if f["judgeable"]]
    if not judgeable:
        out.append(_result("G3", R("G3"), NOT_EVALUABLE, {"judgeable_folds": 0}))
    else:
        gross_positive = sum(1 for f in judgeable if (f["gross_bp_mean"] or 0) > 0)
        net_positive = sum(1 for f in judgeable if (f["net_bp_mean"] or 0) > 0)
        ok = (gross_positive / len(judgeable) >= 2 / 3) and (net_positive / len(judgeable) >= 0.5)
        out.append(_result("G3", R("G3"), PASS if ok else FAIL,
                           {"judgeable_folds": len(judgeable),
                            "gross_positive": gross_positive, "net_positive": net_positive,
                            "gross_required": 2 / 3, "net_required": 0.5}))

    # G4 sample --------------------------------------------------------------------------
    trades = main.get("trades", 0)
    ok = trades >= 200 and len(judgeable) >= 6
    out.append(_result("G4", R("G4"), PASS if ok else FAIL,
                       {"trades": trades, "judgeable_folds": len(judgeable),
                        "trades_required": 200, "folds_required": 6}))

    # G5 year robustness -------------------------------------------------------------------
    loyo_ok = bool(loyo) and all(value > 0 for value in loyo.values())
    share = concentration.get("top_year_share")
    if not loyo:
        status = NOT_EVALUABLE
    elif share == NOT_MEANINGFUL:
        # The share cannot be read, but leave-one-year-out already failed if the total is
        # negative, so the gate has a determinate answer anyway.
        status = PASS if loyo_ok else FAIL
    else:
        status = PASS if (loyo_ok and share <= 0.40) else FAIL
    out.append(_result("G5", R("G5"), status,
                       {"leave_one_year_out_net_bp": loyo, "all_positive": loyo_ok,
                        "top_year": concentration.get("top_year"), "top_year_share": share,
                        "share_limit": 0.40}))

    # G6 single trade concentration ---------------------------------------------------------
    share = concentration.get("top_1_trade_share")
    if share == NOT_MEANINGFUL or share is None:
        status = NOT_EVALUABLE
    else:
        status = PASS if share <= 0.10 else FAIL
    out.append(_result("G6", R("G6"), status,
                       {"top_1_trade_share": share, "limit": 0.10,
                        "top_1_trade_usdt": concentration.get("top_1_trade_usdt"),
                        "total_net_usdt": concentration.get("total_net_usdt")}))

    # G7 cost stress -------------------------------------------------------------------------
    stress, plus = scenarios.get("VIP0_STRESS"), scenarios.get("TAKER_PLUS_20PCT")
    if stress is None or plus is None:
        status = NOT_EVALUABLE
    else:
        status = PASS if (stress > 0 and plus > 0) else FAIL
    out.append(_result("G7", R("G7"), status, {"VIP0_STRESS_bp": stress,
                                               "TAKER_PLUS_20PCT_bp": plus}))

    # G8 parameter sensitivity ----------------------------------------------------------------
    arms = {name: block.get("average_trade_bp") for name, block in sensitivity.items()}
    if len(arms) != 6 or any(value is None for value in arms.values()):
        status = NOT_EVALUABLE
    else:
        status = PASS if all(value > 0 for value in arms.values()) else FAIL
    out.append(_result("G8", R("G8"), status, {"arm_net_bp": arms, "arms_required": 6}))

    # G9 regime robustness ----------------------------------------------------------------------
    trend = {k: v["average_trade_bp"] for k, v in regimes["trend"].items() if v["trades"]}
    vol = {k: v["average_trade_bp"] for k, v in regimes["volatility"].items() if v["trades"]}
    session = {k: v["average_trade_bp"] for k, v in regimes["session"].items() if v["trades"]}
    if not trend or not vol or not session:
        status = NOT_EVALUABLE
    else:
        trend_ok = sum(1 for v in trend.values() if v > 0) >= 2
        vol_ok = all(vol.get(label, -1) > 0 for label in ("HIGH", "MID"))
        session_ok = sum(1 for v in session.values() if v > 0) >= 3
        status = PASS if (trend_ok and vol_ok and session_ok) else FAIL
    out.append(_result("G9", R("G9"), status,
                       {"trend_net_bp": trend, "volatility_net_bp": vol,
                        "session_net_bp": session,
                        "requires": "trend 2/3, volatility HIGH and MID both, session 3/4"}))

    # G10 curve quality --------------------------------------------------------------------------
    pf, mdd = main.get("profit_factor"), main.get("mdd")
    if pf is None or pf == NOT_MEANINGFUL or mdd is None:
        status = NOT_EVALUABLE
    else:
        pf_value = float("inf") if pf == "INF" else pf
        status = PASS if (pf_value > 1.3 and mdd < 0.15) else FAIL
    out.append(_result("G10", R("G10"), status,
                       {"profit_factor": pf, "mdd": mdd, "pf_required": 1.3,
                        "mdd_limit": 0.15}))

    # G11 sanity, never a pass condition ------------------------------------------------------
    nostop_mean = nostop.get("average_trade_bp")
    out.append(_result("G11", R("G11"), NOT_EVALUABLE,
                       {"note": "sanity check only; carries no authority over the verdict",
                        "nostop_net_bp_mean": nostop_mean,
                        "nostop_trades": nostop.get("trades"),
                        "research_reference": "D5.2 C1-EVENT-240m-LONG net +19.28bp (WEAK, N_eff 77)"}))
    return out


def verdict(gates: list[dict[str, Any]]) -> dict[str, Any]:
    """PASS only when G1 to G10 all pass. A NOT_EVALUABLE among them cannot be a PASS."""
    decisive = [g for g in gates if g["id"] != "G11"]
    failed = [g["id"] for g in decisive if g["status"] == FAIL]
    unknown = [g["id"] for g in decisive if g["status"] == NOT_EVALUABLE]
    if failed:
        final = "FAIL"
    elif unknown:
        final = "INCONCLUSIVE"
    else:
        final = "PASS"
    return {"verdict": final, "failed": failed, "not_evaluable": unknown,
            "d6d_authorization": "AUTHORIZED" if final == "PASS" else "NOT AUTHORIZED"}
