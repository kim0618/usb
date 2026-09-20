"""The E0 gate, evaluated against the numbers frozen in the declaration.

The gate is mechanical on purpose. Each criterion is read from the declaration file, compared
with the candidate's measured value, and recorded with both numbers, so the verdict can be
re-derived from the artifact without rerunning anything. No criterion is relaxed, dropped or
reinterpreted here; a declared threshold that fails, fails.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.backtest.strategy_e0_overnight.config import E0Rules

PASS, INCONCLUSIVE, FAIL = "PASS", "INCONCLUSIVE", "FAIL"


def _positive_quarters(quarterly: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for q in quarterly
               if q.get("lift") and q["lift"].get("mean") is not None and q["lift"]["mean"] > 0)


def evaluate(candidate: Mapping[str, Any], rules: E0Rules) -> dict[str, Any]:
    """Apply every declared criterion to one candidate block from ``evaluate_candidate``."""
    thresholds = rules.gate
    summary = candidate.get("summary") or {}
    if not summary.get("n"):
        return {"name": candidate.get("name"), "verdict": FAIL,
                "reason": "the candidate set is empty", "criteria": []}
    lift = candidate.get("lift") or {}
    bootstrap = candidate.get("bootstrap") or {}
    quarterly = candidate.get("quarterly") or []
    extremes = {row["removal"]: row for row in candidate.get("extreme_removal") or []}
    concentration = candidate.get("symbol_concentration") or {}

    top1pct = extremes.get("drop_top_1pct", {})
    criteria = [
        {"id": "min_rows", "required": thresholds["min_rows"], "observed": summary["n"],
         "ok": summary["n"] >= thresholds["min_rows"]},
        {"id": "min_mean_lift", "required": thresholds["min_mean_lift"],
         "observed": lift.get("mean"), "ok": (lift.get("mean") or -1) >= thresholds["min_mean_lift"]},
        {"id": "min_median_lift", "required": thresholds["min_median_lift"],
         "observed": lift.get("median"),
         "ok": (lift.get("median") if lift.get("median") is not None else -1)
               >= thresholds["min_median_lift"]},
        {"id": "min_win_rate_lift", "required": thresholds["min_win_rate_lift"],
         "observed": lift.get("win_rate"),
         "ok": (lift.get("win_rate") if lift.get("win_rate") is not None else -1)
               >= thresholds["min_win_rate_lift"]},
        {"id": "min_positive_quarters", "required": thresholds["min_positive_quarters"],
         "observed": _positive_quarters(quarterly),
         "ok": _positive_quarters(quarterly) >= thresholds["min_positive_quarters"]},
        {"id": "mean_lift_after_top1pct_removed",
         "required": thresholds["mean_lift_after_top1pct_removed"],
         "observed": top1pct.get("mean_lift"),
         "ok": (top1pct.get("mean_lift") if top1pct.get("mean_lift") is not None else -1)
               >= thresholds["mean_lift_after_top1pct_removed"]},
        {"id": "max_top5_ticker_share_of_excess",
         "required": thresholds["max_top5_ticker_share_of_excess"],
         "observed": concentration.get("top_share_of_total_excess"),
         "ok": (concentration.get("top_share_of_total_excess") is not None
                and concentration["top_share_of_total_excess"]
                <= thresholds["max_top5_ticker_share_of_excess"])},
        {"id": "bootstrap_mean_lift_ci_low_above",
         "required": thresholds["bootstrap_mean_lift_ci_low_above"],
         "observed": (bootstrap.get("mean_lift_ci") or [None])[0],
         "ok": bool(bootstrap.get("mean_lift_ci")
                    and bootstrap["mean_lift_ci"][0] > thresholds["bootstrap_mean_lift_ci_low_above"])},
        {"id": "max_downside_ratio_vs_baseline",
         "required": thresholds["max_downside_ratio_vs_baseline"],
         "observed": lift.get("downside_ratio"),
         "ok": (lift.get("downside_ratio") is not None
                and lift["downside_ratio"] <= thresholds["max_downside_ratio_vs_baseline"])},
    ]
    failed = [c["id"] for c in criteria if not c["ok"]]
    ci_low = (bootstrap.get("mean_lift_ci") or [None])[0]
    if not failed:
        verdict = PASS
    elif ci_low is not None and ci_low > 0:
        verdict = INCONCLUSIVE
    else:
        verdict = FAIL
    return {"name": candidate.get("name"), "verdict": verdict, "failed": failed,
            "criteria": criteria}


def combine(verdicts: Sequence[Mapping[str, Any]]) -> str:
    """The study verdict: the strongest outcome any declared hypothesis reached."""
    values = {v["verdict"] for v in verdicts}
    if PASS in values:
        return PASS
    if INCONCLUSIVE in values:
        return INCONCLUSIVE
    return FAIL
