"""The E1 gate, evaluated against the numbers frozen in the declaration.

Two things distinguish it from E0's gate. It is evaluated on one declared primary label
(``R_5M``), so three horizons are not three chances at a PASS. And it carries the
volatility-selector rejection the declaration added after E0: a candidate whose mean rises while
its median does not and both tails widen is recorded as a volatility selector and refused
credit, whatever its mean.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.backtest.strategy_e1_premarket.config import E1Rules

PASS, INCONCLUSIVE, FAIL = "PASS", "INCONCLUSIVE", "FAIL"


def _positive_quarters(quarterly: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for q in quarterly
               if q.get("lift") and q["lift"].get("mean") is not None and q["lift"]["mean"] > 0)


def _value(block: Mapping[str, Any], *path: str, default: Any = None) -> Any:
    node: Any = block
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return default
        node = node[key]
    return node


def evaluate(candidate: Mapping[str, Any], rules: E1Rules,
             secondary: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Apply every declared criterion to one candidate block on the primary label."""
    thresholds = rules.gate
    summary = candidate.get("summary") or {}
    if not summary.get("n"):
        return {"name": candidate.get("name"), "verdict": FAIL,
                "reason": "the candidate set is empty", "criteria": [], "failed": ["min_rows"]}
    lift = candidate.get("lift") or {}
    bootstrap = candidate.get("bootstrap") or {}
    extremes = {row["removal"]: row for row in candidate.get("extreme_removal") or []}
    top1pct = extremes.get("drop_top_1pct", {})
    concentration = candidate.get("symbol_concentration") or {}
    ci_low = (bootstrap.get("mean_lift_ci") or [None])[0]

    def check(name: str, observed: Any, ok: bool) -> dict[str, Any]:
        return {"id": name, "required": thresholds[name], "observed": observed, "ok": bool(ok)}

    criteria = [
        check("min_rows", summary["n"], summary["n"] >= thresholds["min_rows"]),
        check("min_mean_lift", lift.get("mean"),
              (lift.get("mean") or -1) >= thresholds["min_mean_lift"]),
        check("min_median_lift", lift.get("median"),
              (lift.get("median") if lift.get("median") is not None else -1)
              >= thresholds["min_median_lift"]),
        check("min_win_rate_lift", lift.get("win_rate"),
              (lift.get("win_rate") if lift.get("win_rate") is not None else -1)
              >= thresholds["min_win_rate_lift"]),
        check("min_positive_quarters", _positive_quarters(candidate.get("quarterly") or []),
              _positive_quarters(candidate.get("quarterly") or [])
              >= thresholds["min_positive_quarters"]),
        check("mean_lift_after_top1pct_removed", top1pct.get("mean_lift"),
              (top1pct.get("mean_lift") if top1pct.get("mean_lift") is not None else -1)
              >= thresholds["mean_lift_after_top1pct_removed"]),
        check("max_top5_ticker_share_of_excess", concentration.get("top_share_of_total_excess"),
              concentration.get("top_share_of_total_excess") is not None
              and concentration["top_share_of_total_excess"]
              <= thresholds["max_top5_ticker_share_of_excess"]),
        check("bootstrap_mean_lift_ci_low_above", ci_low,
              ci_low is not None and ci_low > thresholds["bootstrap_mean_lift_ci_low_above"]),
        check("max_downside_ratio_vs_baseline", lift.get("downside_ratio"),
              lift.get("downside_ratio") is not None
              and lift["downside_ratio"] <= thresholds["max_downside_ratio_vs_baseline"]),
    ]
    failed = [c["id"] for c in criteria if not c["ok"]]
    is_selector = bool(candidate.get("volatility_selector"))

    # Declared escape hatch: every criterion met on both R_1M and R_15M but not on R_5M.
    secondary_all_pass = False
    if secondary:
        secondary_all_pass = all(
            block and not evaluate_simple(block, rules)["failed"] for block in secondary.values())

    if not failed and not is_selector:
        verdict = PASS
    elif is_selector:
        verdict = FAIL
    elif secondary_all_pass or (ci_low is not None and ci_low > 0):
        verdict = INCONCLUSIVE
    else:
        verdict = FAIL
    return {"name": candidate.get("name"), "verdict": verdict, "failed": failed,
            "volatility_selector": is_selector,
            "secondary_horizons_all_pass": secondary_all_pass, "criteria": criteria}


def evaluate_simple(candidate: Mapping[str, Any], rules: E1Rules) -> dict[str, Any]:
    """The same criteria without the secondary-horizon recursion, for the R_1M / R_15M check."""
    return evaluate(candidate, rules, secondary=None)


def combine(verdicts: Sequence[Mapping[str, Any]]) -> str:
    values = {v["verdict"] for v in verdicts}
    if PASS in values:
        return PASS
    if INCONCLUSIVE in values:
        return INCONCLUSIVE
    return FAIL
