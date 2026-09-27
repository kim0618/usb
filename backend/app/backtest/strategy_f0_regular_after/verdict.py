"""The F0 verdict: a pure function of the declared gates. Nothing diagnostic is an input.

Its parameters are the per-hypothesis gate cells, the PIT result, the critical-integrity flag and
the count of baseline sessions with a resolved row. Secondary horizons, the 16:15 diagnostic,
single-feature buckets, matched control, event and auction diagnostics cannot reach it.
"""

from collections.abc import Mapping

from app.backtest.strategy_f0_regular_after.params import Params

GATE_ORDER = ("Sample", "Statistical", "Tail", "Mean/Median", "Downside", "Extreme",
              "Concentration", "Time Consistency", "Cost", "PIT")
POINT_GATES = ("Tail", "Mean/Median", "Downside", "Extreme", "Concentration", "Time Consistency", "Cost", "PIT")


def hypothesis_label(cells: Mapping[str, str]) -> str:
    if cells["Sample"] != "PASS":
        return "INSUFFICIENT_SAMPLE"
    if all(cells[g] == "PASS" for g in GATE_ORDER):
        return "H_PASS"
    if cells["Statistical"] == "UNDERPOWERED" and all(cells[g] == "PASS" for g in POINT_GATES):
        return "UNDERPOWERED"
    return "H_FAIL"


def verdict(matrix: Mapping[str, Mapping[str, str]], *, pit_pass: bool, integrity_critical: bool,
            resolved_sessions: int, p: Params) -> dict:
    labels = {h: hypothesis_label(cells) for h, cells in matrix.items()}
    reasons = []
    if not pit_pass:
        raise RuntimeError("F0 PIT FAIL - NO VERDICT")   # the run must have stopped earlier
    if any(v == "H_PASS" for v in labels.values()) and not integrity_critical:
        return {"verdict": "PASS", "labels": labels, "reasons": ["at least one H_PASS"]}
    if all(v == "INSUFFICIENT_SAMPLE" for v in labels.values()):
        reasons.append("every H is INSUFFICIENT_SAMPLE")
    if any(v == "UNDERPOWERED" for v in labels.values()):
        reasons.append("an H is UNDERPOWERED")
    if resolved_sessions < p.min_resolved_sessions:
        reasons.append("EXECUTION_RESOLUTION_INSUFFICIENT")
    if integrity_critical:
        reasons.append("DATA_INTEGRITY")
    if reasons:
        return {"verdict": "INCONCLUSIVE", "labels": labels, "reasons": reasons}
    return {"verdict": "FAIL", "labels": labels,
            "reasons": ["sample/coverage sufficient and no H passes every gate"]}
