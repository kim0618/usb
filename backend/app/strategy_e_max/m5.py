"""E-MAX-M5 protocol loader, exposure winner selection and M6 authorization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.strategy_e_max import exposure, m0, m1, m2, m3, m4

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m5_rules_v1.json"
RULES_CANONICAL_SHA256 = "b2a89a713af448d7207006a22a3035f1ac22a8a0074d2fa23c8da2b029583647"
M4_RESULT_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m4_result_v1.json"


class M5ContractError(ValueError):
    """The M5 protocol drifted from M0-M4 or from its own checksum."""


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if m0.canonical_sha256(payload) != RULES_CANONICAL_SHA256:
        raise M5ContractError("M5 protocol digest differs from the frozen value")
    base = m0.load_rules()
    for module in (m1, m2, m3, m4):
        module.load_rules()
    up = payload["upstream"]
    if (up["m0_rules_canonical_sha256"] != m0.RULES_CANONICAL_SHA256
            or up["m1_rules_canonical_sha256"] != m1.RULES_CANONICAL_SHA256
            or up["m2_rules_canonical_sha256"] != m2.RULES_CANONICAL_SHA256
            or up["m3_rules_canonical_sha256"] != m3.RULES_CANONICAL_SHA256
            or up["m4_rules_canonical_sha256"] != m4.RULES_CANONICAL_SHA256):
        raise M5ContractError("M5 does not point at the frozen M0-M4 contracts")
    if hashlib.sha256(M4_RESULT_PATH.read_bytes()).hexdigest() != up["m4_result_file_sha256"]:
        raise M5ContractError("the committed M4 result changed")
    verdict = json.loads(M4_RESULT_PATH.read_text(encoding="utf-8"))["verdict"]
    if verdict["winner"] != "X1" or verdict["m5"] != "M5 AUTHORIZED" or up["m4_winner"] != "X1":
        raise M5ContractError("M5 requires M4 = X1 and M5 AUTHORIZED")
    declared = [payload["variants"]["comparator"]] + list(payload["variants"]["candidates"])
    m0_grid = [h["multiplier"] for h in base["exposure"]["hypotheses"]]
    if ([v["global_multiplier"] for v in declared] != m0_grid
            or [v["m0_id"] for v in declared] != [h["id"] for h in base["exposure"]["hypotheses"]]
            or {v["id"]: float(exposure.MULTIPLIERS[v["id"]]) for v in declared}
            != {v["id"]: v["global_multiplier"] for v in declared}):
        raise M5ContractError("M5 multipliers differ from the M0 exposure grid")
    e = payload["eligibility"]
    if (e["mdd_10bp_ge"] != m0.MDD_CEILING
            or e["top1_share_10bp_le"] != base["base_reference_values"]["top1_share_10bp"]
            or e["min_coverage"] != 0.95
            or payload["improvement"]["paired_p_delta_le_zero_max"] != 0.10):
        raise M5ContractError("M5 gates differ from M0")
    return MappingProxyType(payload)


def eligible(summary: Mapping[str, Any], rules: Mapping) -> dict[str, bool]:
    """M0 eligibility plus the frozen catastrophic-loss rule (``summary['catastrophic']``)."""
    checks = dict(m1.eligible(summary, rules))
    checks.pop("pass")
    checks["no_catastrophic_session"] = not summary["catastrophic"]
    checks["pass"] = all(checks.values())
    return checks


def winner(candidates: Sequence[Mapping[str, Any]], rules: Mapping) -> dict[str, Any]:
    """``candidates``: id, eligible_pass, improved_pass, cagr_10bp, mean_10bp."""
    order = rules["variants"]["declared_order"]
    pool = [c for c in candidates if c["eligible_pass"] and c["improved_pass"]]
    labels = rules["verdict_labels"]
    if not pool:
        return {"winner": "E1", "label": labels["NONE"], "pool": []}
    best = sorted(pool, key=lambda c: (-c["cagr_10bp"], -c["mean_10bp"], order.index(c["id"])))[0]
    return {"winner": best["id"], "label": labels[best["id"]], "pool": [c["id"] for c in pool]}


def m6(carried_eligibility: Mapping[str, bool], rules: Mapping, *, stop_a: bool = False,
       stop_d: bool = False) -> str:
    ok = bool(carried_eligibility["pass"]) and not stop_a and not stop_d
    return rules["verdict_labels"]["M6_YES" if ok else "M6_NO"]


improved = m1.improved
cagr = m1.cagr
