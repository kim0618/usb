"""E-MAX-M4 protocol loader, X2-vs-X1 decision and M5 authorization."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.strategy_e_max import exit_x2, m0, m1, m2, m3

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m4_rules_v1.json"
RULES_CANONICAL_SHA256 = "6b3405a787afbfa2d8a0639673de88d7d5b70dbcf79507f312193bcbc34c125c"
M3_RESULT_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m3_result_v1.json"
PRIOR_WINNERS = ("M1:R1", "M3:B2")


class M4ContractError(ValueError):
    """The M4 protocol drifted from M0-M3 or from its own checksum."""


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if m0.canonical_sha256(payload) != RULES_CANONICAL_SHA256:
        raise M4ContractError("M4 protocol digest differs from the frozen value")
    base = m0.load_rules()
    for module in (m1, m2, m3):
        module.load_rules()
    up = payload["upstream"]
    if (up["m0_rules_canonical_sha256"] != m0.RULES_CANONICAL_SHA256
            or up["m1_rules_canonical_sha256"] != m1.RULES_CANONICAL_SHA256
            or up["m2_rules_canonical_sha256"] != m2.RULES_CANONICAL_SHA256
            or up["m3_rules_canonical_sha256"] != m3.RULES_CANONICAL_SHA256):
        raise M4ContractError("M4 does not point at the frozen M0-M3 contracts")
    if hashlib.sha256(M3_RESULT_PATH.read_bytes()).hexdigest() != up["m3_result_file_sha256"]:
        raise M4ContractError("the committed M3 result changed")
    verdict = json.loads(M3_RESULT_PATH.read_text(encoding="utf-8"))["verdict"]
    if verdict["winner"] != "B2" or verdict["m4"] != "M4 AUTHORIZED" or up["m3_winner"] != "B2":
        raise M4ContractError("M4 requires M3 = B2 and M4 AUTHORIZED")
    x2_rule = base["exit"]["hypotheses"][1]["rule"]
    if ("09:44" not in x2_rule or "close(09:34) > open(09:30)" not in x2_rule
            or exit_x2.EXTENDED_BAR_START_ET != "09:44"
            or payload["variants"]["continuation_condition"] != "close(09:34) > open(09:30), strict, raw prices"):
        raise M4ContractError("M4 X2 differs from the M0 exit hypothesis")
    e = payload["eligibility"]
    if (e["mdd_10bp_ge"] != m0.MDD_CEILING
            or e["top1_share_10bp_le"] != base["base_reference_values"]["top1_share_10bp"]
            or e["min_coverage"] != 0.95
            or payload["improvement"]["paired_p_delta_le_zero_max"] != 0.10
            or payload["costs"]["primary"] != m0.PRIMARY_COST):
        raise M4ContractError("M4 gates differ from M0")
    return MappingProxyType(payload)


def decide(eligibility: Mapping[str, bool], improvement: Mapping[str, bool],
           carried_eligibility: Mapping[str, bool], rules: Mapping,
           *, stop_a: bool = False, stop_d: bool = False) -> dict[str, Any]:
    """Verdict, the carried exit and M5 authorization (prior winners R1 and B2 exist)."""
    selected = bool(eligibility["pass"] and improvement["pass"])
    carried_ok = bool(eligibility["pass"]) if selected else bool(carried_eligibility["pass"])
    m5 = bool(PRIOR_WINNERS or selected) and carried_ok and not stop_a and not stop_d
    labels = rules["verdict_labels"]
    return {"winner": "X2" if selected else "X1", "label": labels["PASS" if selected else "NONE"],
            "m5": labels["M5_YES" if m5 else "M5_NO"]}


eligible = m1.eligible
improved = m1.improved
cagr = m1.cagr
