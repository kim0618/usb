"""E-MAX-M3 protocol loader, B2-vs-B1 decision and M4 authorization."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.strategy_e_max import breadth, m0, m1, m2

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m3_rules_v1.json"
RULES_CANONICAL_SHA256 = "2efbf457654f7260adb703a3a6ea2cd83f0e222e880323994ebc7e43ac56d020"
M2_RESULT_PATH = REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_m2_result_v1.json"


class M3ContractError(ValueError):
    """The M3 protocol drifted from M0-M2 or from its own checksum."""


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if m0.canonical_sha256(payload) != RULES_CANONICAL_SHA256:
        raise M3ContractError("M3 protocol digest differs from the frozen value")
    base = m0.load_rules()
    m1.load_rules()
    m2.load_rules()
    up = payload["upstream"]
    if (up["m0_rules_canonical_sha256"] != m0.RULES_CANONICAL_SHA256
            or up["m1_rules_canonical_sha256"] != m1.RULES_CANONICAL_SHA256
            or up["m2_rules_canonical_sha256"] != m2.RULES_CANONICAL_SHA256):
        raise M3ContractError("M3 does not point at the frozen M0-M2 contracts")
    if hashlib.sha256(M2_RESULT_PATH.read_bytes()).hexdigest() != up["m2_result_file_sha256"]:
        raise M3ContractError("the committed M2 result changed")
    if json.loads(M2_RESULT_PATH.read_text(encoding="utf-8"))["verdict"]["winner"] != up["m2_winner"] or up["m2_winner"] != "C1":
        raise M3ContractError("M3 must run on the M2 carried configuration (max 3)")
    v = payload["variants"]
    b2_rule = base["breadth"]["hypotheses"][1]["rule"]
    if (v["threshold"] != breadth.THRESHOLD or f"{breadth.THRESHOLD}" not in b2_rule
            or v["min_universe_rows"] != breadth.MIN_UNIVERSE_ROWS
            or v["high_multiplier"] != float(breadth.HIGH_MULTIPLIER) or "1.5" not in b2_rule
            or v["comparator"]["max_selected"] != 3 or v["candidate"]["max_selected"] != 3
            or v["comparator"]["ordering"] != "R1" or v["candidate"]["ordering"] != "R1"):
        raise M3ContractError("M3 variants differ from the M0 breadth hypotheses")
    e = payload["eligibility"]
    if (e["mdd_10bp_ge"] != m0.MDD_CEILING
            or e["top1_share_10bp_le"] != base["base_reference_values"]["top1_share_10bp"]
            or e["min_coverage"] != 0.95
            or payload["improvement"]["paired_p_delta_le_zero_max"] != 0.10):
        raise M3ContractError("M3 gates differ from M0")
    return MappingProxyType(payload)


def decide(eligibility: Mapping[str, bool], improvement: Mapping[str, bool],
           carried_eligibility: Mapping[str, bool], rules: Mapping) -> dict[str, Any]:
    """Verdict, the carried configuration, and M4 authorization for that configuration."""
    selected = bool(eligibility["pass"] and improvement["pass"])
    carried = "B2" if selected else "B1"
    m4 = bool(eligibility["pass"]) if selected else bool(carried_eligibility["pass"])
    labels = rules["verdict_labels"]
    return {"winner": carried, "label": labels["PASS" if selected else "NONE"],
            "m4": labels["M4_YES" if m4 else "M4_NO"]}


eligible = m1.eligible
improved = m1.improved
cagr = m1.cagr
