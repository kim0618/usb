"""The confirmation gate, evaluated against the numbers frozen in the declaration.

Two rules make this gate different from E1's. The sample floor is not lowered when the data is
short - the verdict becomes INCONCLUSIVE and the shortfall is reported. And a PASS is impossible
without a final holdout: when one cannot be constructed, the best available outcome is
``CONFIRMATION_PASS_BUT_HOLDOUT_INSUFFICIENT``, which does not promote H5.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.backtest.strategy_e1_h5_confirm.config import ConfirmRules

PASS = "PASS"
INCONCLUSIVE = "INCONCLUSIVE"
FAIL = "FAIL"
HOLDOUT_SHORT = "CONFIRMATION_PASS_BUT_HOLDOUT_INSUFFICIENT"


def _majority_positive(rows: Sequence[Mapping[str, Any]], key: str, minimum_rows: int) -> bool:
    usable = [r for r in rows if (r.get("h5_rows") or 0) >= minimum_rows
              and r.get(key) is not None]
    if not usable:
        return False
    return sum(1 for r in usable if r[key] > 0) * 2 > len(usable)


def evaluate(block: Mapping[str, Any], rules: ConfirmRules, *,
             holdout_available: bool) -> dict[str, Any]:
    thresholds = rules.gate
    matched = block.get("matched_primary") or {}
    rows = int(matched.get("matched_rows") or 0)
    bootstrap = (block.get("bootstrap") or {}).get("session_cluster") or {}
    extremes = {r["removal"]: r for r in block.get("extreme_removal") or []}
    top1pct = extremes.get("drop_top_1pct", {})
    concentration = block.get("concentration") or {}
    cost = block.get("cost_stress") or {}
    monthly = block.get("monthly") or []

    def check(name: str, observed: Any, ok: bool) -> dict[str, Any]:
        return {"id": name, "required": thresholds.get(name), "observed": observed, "ok": bool(ok)}

    months_positive = sum(1 for m in monthly if (m.get("mean_lift") or 0) > 0)
    criteria = [
        check("confirmation_rows", rows, rows >= thresholds["confirmation_rows"]),
        check("matched_mean_lift", matched.get("mean_lift"),
              (matched.get("mean_lift") or -1) >= thresholds["matched_mean_lift"]),
        check("matched_median_lift", matched.get("median_lift"),
              (matched.get("median_lift") if matched.get("median_lift") is not None else -1)
              >= thresholds["matched_median_lift"]),
        check("matched_win_rate_lift", matched.get("win_rate_lift"),
              (matched.get("win_rate_lift") if matched.get("win_rate_lift") is not None else -1)
              >= thresholds["matched_win_rate_lift"]),
        check("matched_mean_lift_after_top1pct_removed", top1pct.get("mean_lift"),
              (top1pct.get("mean_lift") if top1pct.get("mean_lift") is not None else -1)
              >= thresholds["matched_mean_lift_after_top1pct_removed"]),
        check("max_top5_symbol_share_of_matched_excess", concentration.get("top_5_share"),
              concentration.get("top_5_share") is not None
              and concentration["top_5_share"] <= thresholds["max_top5_symbol_share_of_matched_excess"]),
        check("bootstrap_session_cluster_ci_low_above", (bootstrap.get("ci") or [None])[0],
              bool(bootstrap.get("ci")
                   and bootstrap["ci"][0] > thresholds["bootstrap_session_cluster_ci_low_above"])),
        check("max_downside_ratio_vs_control", matched.get("downside_ratio"),
              matched.get("downside_ratio") is not None
              and matched["downside_ratio"] <= thresholds["max_downside_ratio_vs_control"]),
        {"id": "price_neutralized_positive", "required": thresholds["price_neutralized_positive"],
         "observed": _majority_positive(block.get("by_price") or [], "mean_lift", 30),
         "ok": _majority_positive(block.get("by_price") or [], "mean_lift", 30)},
        {"id": "liquidity_neutralized_positive",
         "required": thresholds["liquidity_neutralized_positive"],
         "observed": _majority_positive(block.get("by_liquidity") or [], "mean_lift", 30),
         "ok": _majority_positive(block.get("by_liquidity") or [], "mean_lift", 30)},
        {"id": "time_direction_consistent", "required": thresholds["time_direction_consistent"],
         "observed": f"{months_positive}/{len(monthly)} months positive",
         "ok": bool(monthly) and months_positive * 2 > len(monthly)},
        {"id": "net_positive_at_declared_cost",
         "required": thresholds["net_positive_at_declared_cost"],
         "observed": cost.get("net_at_declared_cost"),
         "ok": bool(cost.get("net_positive_at_declared_cost"))},
    ]
    failed = [c["id"] for c in criteria if not c["ok"]]
    sample_short = "confirmation_rows" in failed
    lift = matched.get("mean_lift")
    edge_gone = lift is not None and lift <= 0

    if not failed:
        verdict = PASS if holdout_available else HOLDOUT_SHORT
    elif edge_gone:
        verdict = FAIL
    elif failed == ["confirmation_rows"] or sample_short:
        verdict = INCONCLUSIVE
    else:
        verdict = FAIL
    return {"verdict": verdict, "failed": failed, "criteria": criteria,
            "holdout_available": holdout_available,
            "promotes_to_strategy_e": verdict == PASS}
