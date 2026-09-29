"""The preregistered verdict, applied mechanically.

Every threshold here comes from the frozen contract. The function takes the metrics and returns a
label; it never looks at what would be convenient. Order matters: a target with too few positives
is INCONCLUSIVE rather than NO_SIGNAL, because those two say different things and only one of
them is an answer.
"""
from __future__ import annotations

import math
from typing import Any

from . import contract as C


def _ok(value: float | None) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def evaluate(pooled: dict[str, Any], fold_skills: list[float],
             year_skills: dict[str, float]) -> dict[str, Any]:
    """Verdict for one (target, model) pair from its pooled and per-slice metrics."""
    n, positives = pooled["n"], pooled["positives"]
    checks: dict[str, Any] = {}

    if n < C.MIN_VALID_ROWS or positives < C.MIN_VALID_POSITIVES:
        return {"verdict": C.INCONCLUSIVE,
                "reason": f"validation rows {n} or positives {positives} below the preregistered "
                          f"minimum ({C.MIN_VALID_ROWS} rows, {C.MIN_VALID_POSITIVES} positives)",
                "checks": checks}

    skill, auc, ece = pooled["brier_skill"], pooled["roc_auc"], pooled["ece"]
    slope = pooled.get("calibration_slope")

    conf = next((row for row in pooled["confidence"]
                 if abs(row["threshold"] - C.S5_CONF_THRESHOLD) < 1e-9), None)
    conf_n = conf["n"] if conf else 0
    conf_lift = conf["lift"] if conf else None

    folds_positive = sum(1 for s in fold_skills if _ok(s) and s > 0)
    years_positive = sum(1 for s in year_skills.values() if _ok(s) and s > 0)

    checks["S1_brier_skill"] = {"value": skill, "threshold": C.S1_BRIER_SKILL,
                                "pass": _ok(skill) and skill > C.S1_BRIER_SKILL}
    checks["S2_roc_auc"] = {"value": auc, "threshold": C.S2_AUC,
                            "pass": _ok(auc) and auc > C.S2_AUC}
    checks["S3_ece"] = {"value": ece, "threshold": C.S3_ECE_MAX,
                        "pass": _ok(ece) and ece <= C.S3_ECE_MAX}
    checks["S4_calibration_slope"] = {
        "value": slope, "range": list(C.S4_SLOPE_RANGE),
        "pass": _ok(slope) and C.S4_SLOPE_RANGE[0] <= slope <= C.S4_SLOPE_RANGE[1]}
    checks["S5_confidence_bucket"] = {
        "threshold": C.S5_CONF_THRESHOLD, "n": conf_n, "min_n": C.S5_MIN_N,
        "lift": conf_lift, "min_lift": C.S5_MIN_LIFT,
        "pass": conf_n >= C.S5_MIN_N and _ok(conf_lift) and conf_lift >= C.S5_MIN_LIFT}
    checks["S6_fold_consistency"] = {
        "positive_folds": folds_positive, "of": C.S6_TOTAL_FOLDS, "min": C.S6_MIN_FOLDS,
        "pass": folds_positive >= C.S6_MIN_FOLDS}
    checks["S7_year_consistency"] = {
        "positive_years": years_positive, "of": C.S7_TOTAL_YEARS, "min": C.S7_MIN_YEARS,
        "pass": years_positive >= C.S7_MIN_YEARS}

    if all(c["pass"] for c in checks.values()):
        return {"verdict": C.STRONG, "reason": "all seven preregistered conditions met",
                "checks": checks}

    if _ok(skill) and skill > C.W1_BRIER_SKILL and _ok(auc) and auc > C.W2_AUC:
        failed = [k for k, c in checks.items() if not c["pass"]]
        return {"verdict": C.WEAK,
                "reason": "beats the base rate but fails " + ", ".join(failed),
                "checks": checks}

    return {"verdict": C.NO_SIGNAL,
            "reason": f"brier skill {skill:+.4f} and AUC {auc:.4f} do not clear the weak "
                      f"thresholds ({C.W1_BRIER_SKILL:+.3f}, {C.W2_AUC:.3f})",
            "checks": checks}


def best_of(verdicts: dict[str, dict[str, Any]]) -> str:
    """A target's verdict is the best its models achieved (contract section 13)."""
    order = [C.STRONG, C.WEAK, C.NO_SIGNAL, C.INCONCLUSIVE]
    labels = [v["verdict"] for v in verdicts.values()]
    for label in order:
        if label in labels:
            return label
    return C.INCONCLUSIVE
