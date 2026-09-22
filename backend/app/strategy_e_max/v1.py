"""E-MAX V1 (E-MAX-M6): the frozen integrated strategy, its provenance and the final gate.

``load_rules`` proves that the V1 contract is the frozen file, that every upstream stage contract
and committed result is the one M0-M5 froze, that the stage winners are R1 / C1 / B2 / X1 / E20,
and that the definition names exactly the values the code modules hold. ``compose`` is the only
place the B2 and global multipliers meet; ``gate`` and ``verdict`` apply the frozen M0 / M5
conditions to already computed numbers and compute no return themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.strategy_e_max import breadth, exposure, m0, m1, m2, m3, m4, m5, ranking

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs/backtest/strategy_e_max"
RULES_PATH = DOCS / "strategy_e_max_v1_rules.json"
RULES_CANONICAL_SHA256 = "b30a3e95d6333c17d736c4c4fa23f55a7f67ba4af328317379a86a4a37e89d0e"
STRATEGY_ID = "STRATEGY_E_MAX_V1"
RANKING = ranking.R1
MAX_SELECTED = 3
GLOBAL = exposure.MULTIPLIERS["E20"]
FINAL_EXPOSURE = {"normal": GLOBAL * breadth.BASE_MULTIPLIER, "high_breadth": GLOBAL * breadth.HIGH_MULTIPLIER}
RESULT_FILES = {f"M{i}": DOCS / f"strategy_e_max_m{i}_result_v1.json" for i in range(1, 6)}
MODULES = {"M0": m0, "M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5}
E_BASE_RESULT = m0.BASE_RESULT_PATH


class V1ContractError(ValueError):
    """The E-MAX V1 contract drifted from M0-M5 or from its own checksum."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise V1ContractError(message)


def _winner(stage: str, payload: Mapping[str, Any]) -> str:
    return (payload["winner"] if stage == "M1" else payload["verdict"])["winner"]


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(m0.canonical_sha256(payload) == RULES_CANONICAL_SHA256, "V1 contract digest differs")
    _require(payload["declaration"]["strategy_id"] == STRATEGY_ID, "strategy id moved")
    base = m0.load_rules()
    m5_rules = m5.load_rules()              # loads M0-M4 and checks the M4 result as well
    stages = payload["upstream"]["stages"]
    for name, module in MODULES.items():
        _require(stages[name]["rules_canonical_sha256"] == module.RULES_CANONICAL_SHA256,
                 f"{name} contract is not the frozen one")
    for name, file in RESULT_FILES.items():
        _require(hashlib.sha256(file.read_bytes()).hexdigest() == stages[name]["result_file_sha256"],
                 f"committed {name} result changed")
        recorded = json.loads(file.with_suffix(".sha256").read_text(encoding="utf-8"))
        _require(recorded["file_sha256"] == stages[name]["result_file_sha256"], f"{name} sha256 file moved")
        _require(_winner(name, json.loads(file.read_text(encoding="utf-8"))) == stages[name]["winner"],
                 f"{name} winner is not {stages[name]['winner']}")
    _require([stages[f"M{i}"]["winner"] for i in range(1, 6)] == ["R1", "C1", "B2", "X1", "E20"],
             "E-MAX V1 must be R1 + C1 (max 3) + B2 + X1 + E20")
    m5_verdict = json.loads(RESULT_FILES["M5"].read_text(encoding="utf-8"))["verdict"]
    _require(m5_verdict["m6"] == stages["M5"]["m6_authorization"] == "M6 AUTHORIZED", "M6 not authorized")

    e_base = payload["upstream"]["e_base"]
    _require(e_base["e_r3_result_file_sha256"] == m0.BASE_RESULT_SHA256
             and hashlib.sha256(E_BASE_RESULT.read_bytes()).hexdigest() == m0.BASE_RESULT_SHA256,
             "E-R3 Base result changed")
    for key in ("alpha_h5_canonical_sha256", "trading_v1_1_canonical_sha256",
                "replay_protocol_e_r2_canonical_sha256"):
        _require(any(e_base[key] in v for v in base["base_frozen"].values()), f"{key} is not M0's")
    s3 = payload["upstream"]["stage_artifacts"]
    _require(s3["S2_PLUS_B2"]["trades_csv_sha256"] == m5_rules["upstream"]["m4_x1_trades_csv_sha256"]
             and s3["S2_PLUS_B2"]["daily_returns_csv_sha256"]
             == m5_rules["upstream"]["m4_x1_daily_returns_csv_sha256"], "S2 is not the M5 E1 input")
    m5_sha = json.loads(RESULT_FILES["M5"].with_suffix(".sha256").read_text(encoding="utf-8"))
    _require(s3["S3_PLUS_GLOBAL_2X"]["trades_csv_sha256"] == m5_sha["runtime_artifacts"]["E20_trades.csv"]
             and s3["S3_PLUS_GLOBAL_2X"]["daily_returns_csv_sha256"]
             == m5_sha["runtime_artifacts"]["E20_daily_returns.csv"], "S3 is not the M5 E20 artifact")
    m3_sha = json.loads(RESULT_FILES["M3"].with_suffix(".sha256").read_text(encoding="utf-8"))
    m1_sha = json.loads(RESULT_FILES["M1"].with_suffix(".sha256").read_text(encoding="utf-8"))
    _require(s3["S2_PLUS_B2"]["exposure_map_json_sha256"] == m3_sha["runtime_artifacts"]["exposure_map.json"]
             and s3["S1_R1_MAX3_X1_1X"]["trades_csv_sha256"] == m1_sha["runtime_artifacts"]["R1_trades.csv"]
             and s3["S1_R1_MAX3_X1_1X"]["daily_returns_csv_sha256"]
             == m1_sha["runtime_artifacts"]["R1_daily_returns.csv"], "S1/S2 artifacts are not M1/M3's")

    d = payload["definition"]
    _require(d["ranking"]["id"] == RANKING and d["capacity"]["max_selected"] == MAX_SELECTED
             and d["capacity"]["replacement"] == "NONE", "ranking or capacity moved")
    b = d["breadth"]
    _require(b["threshold"] == breadth.THRESHOLD and b["min_universe_rows"] == breadth.MIN_UNIVERSE_ROWS
             and Fraction(b["high_multiplier"]) == breadth.HIGH_MULTIPLIER
             and Fraction(b["normal_multiplier"]) == breadth.BASE_MULTIPLIER, "B2 moved")
    _require(Fraction(d["global_exposure"]["multiplier"]) == GLOBAL
             and d["global_exposure"]["multiplier"] in m0.ALLOWED_MULTIPLIERS, "global multiplier moved")
    _require({k: Fraction(v) for k, v in d["final_session_exposure"].items()} == FINAL_EXPOSURE,
             "final exposure is not global x breadth")
    _require(d["cost"]["primary"] == m0.PRIMARY_COST
             and d["cost"]["scenarios"] == base["risk_envelope"]["cost_scenarios_reported"], "cost moved")
    g = payload["gate"]
    m0_elig = m5_rules["eligibility"]
    _require(g["min_coverage"] == m0_elig["min_coverage"] and g["mdd_10bp_ge"] == m0.MDD_CEILING
             and g["mean_session_10bp_gt"] == m0_elig["mean_session_10bp_gt"]
             and g["profit_factor_10bp_gt"] == m0_elig["profit_factor_10bp_gt"]
             and g["top1_share_10bp_le"] == base["base_reference_values"]["top1_share_10bp"],
             "M6 gates differ from M0 / M5")
    _require(payload["dataset"]["forward_excluded_from"] == "2026-09-17"
             and payload["dataset"]["sessions"] == m1.SESSIONS, "dataset moved")
    return MappingProxyType(payload)


def compose(session: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(S2 = + B2, S3 = + global 2.0x) for one 1.0x R1 / max 3 / X1 session, in the M5 order."""
    k = breadth.multiplier(session["candidates"], session["eligible"])
    s2 = breadth.scale(session, k)
    return s2, exposure.apply_global(s2, GLOBAL)


def e_base_reference() -> dict[str, float]:
    """Exact COST_10BP E-Base values from the committed E-R3 result (no rounded constants)."""
    s = json.loads(E_BASE_RESULT.read_text(encoding="utf-8"))["scenarios"]["COST_10BP"]
    return {"cumulative_10bp": s["cumulative_return"], "cagr_10bp": m1.cagr(s["cumulative_return"]),
            "mdd_10bp": s["maximum_drawdown"]}


def stop_b(cagr: float, mdd: float, ref_cagr: float, ref_mdd: float) -> dict[str, Any]:
    """M0 stop B: flagged when |MDD| / |MDD_ref| exceeds CAGR / CAGR_ref."""
    cagr_ratio = cagr / ref_cagr if ref_cagr else None
    mdd_ratio = abs(mdd) / abs(ref_mdd) if ref_mdd else None
    defined = cagr_ratio is not None and mdd_ratio is not None
    return {"cagr_ratio": cagr_ratio, "mdd_ratio": mdd_ratio,
            "flagged": bool(not defined or mdd_ratio > cagr_ratio),
            "margin_cagr_minus_mdd_ratio": cagr_ratio - mdd_ratio if defined else None}


def stop_a() -> bool:
    """M0 stop A from the committed verdicts: M1-M4 all kept their comparator."""
    comparators = {"M1": (None, "R0"), "M2": ("C1",), "M3": ("B1",), "M4": ("X1",)}
    return all(_winner(n, json.loads(RESULT_FILES[n].read_text(encoding="utf-8"))) in comparators[n]
               for n in comparators)


def stop_d(m6_mdd_10bp: float) -> bool:
    """M0 stop D for the last two executed stages: every M5 candidate and E-MAX V1 below -35%."""
    variants = json.loads(RESULT_FILES["M5"].read_text(encoding="utf-8"))["variants"]
    m5_all_below = all(variants[c]["evaluation"]["scenarios"]["COST_10BP"]["maximum_drawdown"]
                       < m0.MDD_CEILING for c in ("E15", "E20"))
    return bool(m5_all_below and m6_mdd_10bp < m0.MDD_CEILING)


def cost_sensitivity(mdd_by_scenario: Mapping[str, float], rules: Mapping) -> str:
    ceiling = rules["gate"]["mdd_10bp_ge"]
    if mdd_by_scenario["COST_15BP"] < ceiling:
        return "HIGH"
    if mdd_by_scenario["COST_20BP"] < ceiling:
        return "ELEVATED"
    return "LOW"


def gate(summary: Mapping[str, Any], rules: Mapping) -> dict[str, bool]:
    """Economic and risk conditions. ``summary``: coverage, mean_10bp, pf_10bp, mdd_10bp,
    top1_share_10bp, catastrophic, stop_a, stop_b_flagged, stop_d."""
    g = rules["gate"]
    top1 = summary["top1_share_10bp"]
    checks = {
        "coverage": summary["coverage"] is not None and summary["coverage"] >= g["min_coverage"],
        "mean_session_10bp": summary["mean_10bp"] > g["mean_session_10bp_gt"],
        "profit_factor_10bp": summary["pf_10bp"] is not None and summary["pf_10bp"] > g["profit_factor_10bp_gt"],
        "mdd_10bp": summary["mdd_10bp"] >= g["mdd_10bp_ge"],
        "concentration_top1": top1 is not None and top1 <= g["top1_share_10bp_le"],
        "no_catastrophic_session": not summary["catastrophic"],
        "stop_a_clear": not summary["stop_a"],
        "stop_b_clear": not summary["stop_b_flagged"],
        "stop_d_clear": not summary["stop_d"],
    }
    checks["pass"] = all(checks.values())
    return checks


def verdict(*, integrity: bool, reproducible: bool, m5_identity: bool, gate_checks: Mapping[str, bool],
            rules: Mapping) -> str:
    labels = rules["verdict_labels"]
    if not (integrity and reproducible and m5_identity):
        return labels["BLOCK"]
    return labels["PASS"] if gate_checks["pass"] else labels["FAIL"]
