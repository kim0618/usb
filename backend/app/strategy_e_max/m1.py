"""E-MAX-M1 protocol loader and the frozen eligibility / improvement / winner rules.

The functions below take already computed numbers and apply the rules M0 and M1 froze. They
compute no return themselves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.strategy_e_max import m0, ranking

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m1_rules_v1.json"
RULES_CANONICAL_SHA256 = "4556a4f3b8acb16c897ddfdfb392f82be39eb76286f67e35fed706dc7df64c79"
SESSIONS = 480
ANNUALIZATION = 252


class M1ContractError(ValueError):
    """The M1 protocol drifted from M0 or from its own frozen checksum."""


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = m0.canonical_sha256(payload)
    if found != RULES_CANONICAL_SHA256:
        raise M1ContractError(f"M1 protocol digest {found} != frozen")
    base = m0.load_rules()
    if payload["upstream"]["m0_rules_canonical_sha256"] != m0.RULES_CANONICAL_SHA256:
        raise M1ContractError("M1 does not point at the frozen M0 contract")
    if payload["upstream"]["e_r3_result_file_sha256"] != m0.BASE_RESULT_SHA256:
        raise M1ContractError("M1 does not point at the frozen E-R3 result")
    ids = [h["id"] for h in payload["variants"]["hypotheses"]]
    if ids != [h["id"] for h in base["ranking"]["hypotheses"]] or ids != list(ranking.RULES[1:]):
        raise M1ContractError("M1 variants differ from the M0 ranking hypotheses")
    if len(ids) > base["research_budget"]["ranking"]:
        raise M1ContractError("M1 exceeds the M0 ranking budget")
    held = payload["held_fixed"]
    if (held["max_selected"] != 3 or held["exposure"] != 1.0 or held["primary_cost"] != m0.PRIMARY_COST
            or held["replacement"] != "NONE"):
        raise M1ContractError("M1 must hold capacity, exposure, cost and replacement fixed")
    eligibility = payload["eligibility"]
    if (eligibility["mdd_10bp_ge"] != m0.MDD_CEILING
            or eligibility["top1_share_10bp_le"] != base["base_reference_values"]["top1_share_10bp"]
            or eligibility["min_coverage"] != 0.95):
        raise M1ContractError("M1 eligibility differs from M0")
    if payload["improvement"]["paired_p_delta_le_zero_max"] != 0.10:
        raise M1ContractError("M1 improvement differs from M0")
    return MappingProxyType(payload)


def cagr(cumulative: float, sessions: int = SESSIONS) -> float:
    return (1.0 + cumulative) ** (ANNUALIZATION / sessions) - 1.0


def eligible(summary: Mapping[str, Any], rules: Mapping) -> dict[str, bool]:
    """``summary`` keys: integrity, coverage, mean_10bp, pf_10bp, mdd_10bp, top1_share_10bp."""
    e = rules["eligibility"]
    top1 = summary["top1_share_10bp"]
    checks = {
        "integrity": bool(summary["integrity"]),
        "coverage": summary["coverage"] is not None and summary["coverage"] >= e["min_coverage"],
        "mean_session_10bp": summary["mean_10bp"] > e["mean_session_10bp_gt"],
        "profit_factor_10bp": summary["pf_10bp"] is not None and summary["pf_10bp"] > e["profit_factor_10bp_gt"],
        "mdd_10bp": summary["mdd_10bp"] >= e["mdd_10bp_ge"],
        "top1_share_10bp": top1 is not None and top1 <= e["top1_share_10bp_le"],
    }
    checks["pass"] = all(checks.values())
    return checks


def improved(candidate_cumulative: float, comparator_cumulative: float,
             paired: Mapping[str, float], rules: Mapping) -> dict[str, bool]:
    i = rules["improvement"]
    checks = {
        "cumulative_10bp": candidate_cumulative > comparator_cumulative,
        "paired_mean_delta": paired["mean"] > i["paired_mean_delta_gt"],
        "paired_p_delta_le_zero": paired["p_mean_le_zero"] <= i["paired_p_delta_le_zero_max"],
    }
    checks["pass"] = all(checks.values())
    return checks


def winner(candidates: Sequence[Mapping[str, Any]], rules: Mapping) -> dict[str, Any]:
    """``candidates``: dicts with id, eligible_pass, improved_pass, cagr_10bp, mean_10bp."""
    order = rules["variants"]["declared_order"]
    pool = [c for c in candidates if c["eligible_pass"] and c["improved_pass"]]
    if not pool:
        return {"winner": None, "label": rules["verdict_labels"]["NONE"], "pool": []}
    best = sorted(pool, key=lambda c: (-c["cagr_10bp"], -c["mean_10bp"], order.index(c["id"])))[0]
    return {"winner": best["id"], "label": rules["verdict_labels"][best["id"]],
            "pool": [c["id"] for c in pool]}
