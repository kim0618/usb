"""The G0 gate: twelve declared criteria per hypothesis, thresholds read from the frozen rules.

Evaluated on the primary label only, so secondary exits and the 07:00 entry are diagnostics and
never a second chance at a PASS. The bootstrap criterion reads the weaker (lower) of the session
and the symbol cluster intervals.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

PASS, INCONCLUSIVE, FAIL = "PASS", "INCONCLUSIVE", "FAIL"


def _get(node: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return default
        node = node[key]
    return node


def _num(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return v


def _ge(value: Any, bound: float) -> bool:
    v = _num(value)
    return bool(np.isfinite(v) and v >= bound)


def _le(value: Any, bound: float) -> bool:
    v = _num(value)
    return bool(np.isfinite(v) and v <= bound)


def evaluate(block: Mapping[str, Any], execution: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any]:
    summary = block.get("summary") or {}
    if not summary.get("n"):
        return {"name": block.get("name"), "verdict": FAIL, "failed": ["sample"], "criteria": []}
    lift = block.get("lift") or {}
    exc = block.get("excursion_lift") or {}
    removal = {row["removal"]: row for row in block.get("extreme_removal") or []}
    top1pct = removal.get("drop_top_1pct", {})
    conc = block.get("concentration") or {}
    bs, by = block.get("bootstrap_session") or {}, block.get("bootstrap_symbol") or {}
    mean_low = min(_num(_get(bs, "mean", "ci95", default=[np.nan])[0]),
                   _num(_get(by, "mean", "ci95", default=[np.nan])[0]))
    tail_low = max(
        min(_num(_get(bs, "p_ge_2pct", "ci95", default=[np.nan])[0]),
            _num(_get(by, "p_ge_2pct", "ci95", default=[np.nan])[0])),
        min(_num(_get(bs, "p_mfe_ge_3pct", "ci95", default=[np.nan])[0]),
            _num(_get(by, "p_mfe_ge_3pct", "ci95", default=[np.nan])[0])))
    months = [m for m in block.get("monthly") or [] if m.get("n", 0) >= gate["time"]["min_rows_per_month"]]
    positive_months = sum(1 for m in months if _num(m.get("mean_lift")) > 0)
    month_share = positive_months / len(months) if months else float("nan")
    matched = _get(block, "matched", "estimator_primary", default={}) or {}
    cost = block.get("cost") or {}

    tail_ok = (_ge(lift.get("p_gap_ge_2pct"), gate["tail"]["min_p_ge_2pct_lift_pp"])
               or _ge(lift.get("p_gap_ge_3pct"), gate["tail"]["min_p_ge_3pct_lift_pp"])
               or _ge(exc.get("p_mfe_ge_3pct"), gate["tail"]["min_p_mfe_ge_3pct_lift_pp"]))

    criteria = [
        ("1_sample", {"n": summary["n"], "sessions": block.get("sessions"),
                      "symbols": block.get("unique_symbols")},
         summary["n"] >= gate["sample"]["min_rows"]
         and (block.get("sessions") or 0) >= gate["sample"]["min_sessions"]
         and (block.get("unique_symbols") or 0) >= gate["sample"]["min_symbols"]),
        ("2_mean_lift", lift.get("mean"), _ge(lift.get("mean"), gate["mean"]["min_mean_lift"])),
        ("3_median", {"median": summary.get("median"), "median_lift": lift.get("median")},
         _ge(summary.get("median"), gate["median"]["min_candidate_median"])),
        ("4_tail", {"p_ge_2pct_lift": lift.get("p_gap_ge_2pct"), "p_ge_3pct_lift": lift.get("p_gap_ge_3pct"),
                    "p_mfe_ge_3pct_lift": exc.get("p_mfe_ge_3pct")}, tail_ok),
        ("5_extreme_removal", _get(top1pct, "lift", "mean"),
         _ge(_get(top1pct, "lift", "mean"), gate["extreme_removal"]["min_mean_lift_after_top1pct"])),
        ("6_concentration", {"top1": conc.get("top1_share_of_total_excess"),
                             "top5": conc.get("top5_share_of_total_excess")},
         _le(conc.get("top1_share_of_total_excess"), gate["concentration"]["max_top1_share"])
         and _le(conc.get("top5_share_of_total_excess"), gate["concentration"]["max_top5_share"])),
        ("7_downside", lift.get("p_gap_le_minus_2pct_ratio"),
         _le(lift.get("p_gap_le_minus_2pct_ratio"), gate["downside"]["max_p_le_minus_2pct_ratio"])),
        ("8_bootstrap", {"mean_lift_ci_low_weaker": mean_low, "tail_lift_ci_low_best": tail_low},
         bool(np.isfinite(mean_low) and mean_low > gate["bootstrap"]["mean_lift_ci_low_above"])),
        ("9_time", {"months_counted": len(months), "positive_share": month_share},
         bool(len(months) >= gate["time"]["min_months"]
              and np.isfinite(month_share) and month_share >= gate["time"]["min_positive_month_share"])),
        ("10_neutralization", {"matched_mean_lift": matched.get("mean_lift"),
                               "match_rate": matched.get("match_rate")},
         _ge(matched.get("mean_lift"), gate["neutralization"]["min_matched_mean_lift"])
         and _ge(matched.get("match_rate"), gate["neutralization"]["min_match_rate"])),
        ("11_cost", cost.get("break_even_cost_bp"),
         _ge(cost.get("break_even_cost_bp"), gate["cost"]["min_break_even_cost_bp"])),
        ("12_execution", {k: execution.get(k) for k in ("median_entry_dollar", "entry_fill_rate")},
         _ge(execution.get("median_entry_dollar"), gate["execution"]["min_median_entry_bar_dollar"])
         and _ge(execution.get("entry_fill_rate"), gate["execution"]["min_entry_fill_rate"])),
    ]
    rows = [{"id": cid, "observed": observed, "ok": bool(ok)} for cid, observed, ok in criteria]
    failed = [row["id"] for row in rows if not row["ok"]]
    statistical = [c for c in failed if c != "12_execution"]
    if not failed:
        verdict = PASS
    elif not statistical:
        verdict = INCONCLUSIVE        # declared: statistical PASS, execution inconclusive
    elif (np.isfinite(mean_low) and mean_low > 0 and _num(cost.get("break_even_cost_bp")) > 0
          and "1_sample" not in failed):
        verdict = INCONCLUSIVE
    else:
        verdict = FAIL
    return {"name": block.get("name"), "verdict": verdict, "failed": failed, "criteria": rows}


def combine(verdicts: Sequence[Mapping[str, Any]]) -> str:
    values = {v["verdict"] for v in verdicts}
    if PASS in values:
        return PASS
    if INCONCLUSIVE in values:
        return INCONCLUSIVE
    return FAIL
