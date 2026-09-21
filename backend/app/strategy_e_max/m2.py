"""E-MAX-M2 protocol loader and the frozen C2-vs-C1 decision.

Eligibility and improvement reuse M1's frozen functions (the conditions are M0's); only the
comparator and the labels change.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.strategy_e_max import m0, m1

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m2_rules_v1.json"
RULES_CANONICAL_SHA256 = "aabeddcd25c16daefe3e273b5df5e2e59d362a48820dd8188fe86c483f285a4b"
M1_RESULT_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m1_result_v1.json"
CAPACITIES = {"C1": 3, "C2": 5}


class M2ContractError(ValueError):
    """The M2 protocol drifted from M0 / M1 or from its own checksum."""


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if m0.canonical_sha256(payload) != RULES_CANONICAL_SHA256:
        raise M2ContractError("M2 protocol digest differs from the frozen value")
    base = m0.load_rules()
    m1.load_rules()
    up = payload["upstream"]
    if (up["m0_rules_canonical_sha256"] != m0.RULES_CANONICAL_SHA256
            or up["m1_rules_canonical_sha256"] != m1.RULES_CANONICAL_SHA256):
        raise M2ContractError("M2 does not point at the frozen M0 / M1 contracts")
    if hashlib.sha256(M1_RESULT_PATH.read_bytes()).hexdigest() != up["m1_result_file_sha256"]:
        raise M2ContractError("the committed M1 result changed")
    winner = json.loads(M1_RESULT_PATH.read_text(encoding="utf-8"))["winner"]["winner"]
    if winner != up["m1_winner"] or winner != "R1":
        raise M2ContractError("M2 must run on the M1 winner R1")
    variants = payload["variants"]
    found = {variants["comparator"]["id"]: variants["comparator"]["max_selected"],
             variants["candidate"]["id"]: variants["candidate"]["max_selected"]}
    allowed = [h["max_selected"] for h in base["capacity"]["hypotheses"]]
    if found != CAPACITIES or sorted(found.values()) != sorted(allowed):
        raise M2ContractError("M2 capacities must be exactly the M0 values 3 and 5")
    if variants["comparator"]["ordering"] != "R1" or variants["candidate"]["ordering"] != "R1":
        raise M2ContractError("M2 must keep the R1 ordering")
    e = payload["eligibility"]
    if (e["mdd_10bp_ge"] != m0.MDD_CEILING
            or e["top1_share_10bp_le"] != base["base_reference_values"]["top1_share_10bp"]
            or e["min_coverage"] != 0.95
            or payload["improvement"]["paired_p_delta_le_zero_max"] != 0.10):
        raise M2ContractError("M2 gates differ from M0")
    ex = payload["execution"]
    if ex["primary_cost"] != m0.PRIMARY_COST or ex["exposure_multiplier"] != 1.0:
        raise M2ContractError("M2 must keep 10 bp and 1.0x")
    return MappingProxyType(payload)


def decide(eligibility: Mapping[str, bool], improvement: Mapping[str, bool],
           rules: Mapping) -> dict[str, Any]:
    selected = bool(eligibility["pass"] and improvement["pass"])
    return {"winner": "C2" if selected else "C1",
            "label": rules["verdict_labels"]["PASS" if selected else "NONE"]}


# M0's frozen conditions, applied through M1's functions (their thresholds are read from ``rules``).
eligible = m1.eligible
improved = m1.improved
cagr = m1.cagr
