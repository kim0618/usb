"""E-R2: load the frozen V1.1 development replay protocol and check it against the frozen chain.

Nothing here replays, prices or scores anything. The loader only proves that the protocol file is
the frozen one, that it points at the V1.1 artifacts E-R1 committed and at the E-D6 dataset
binding, and that its gate, bootstrap and block settings are E-D6's, value for value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import numpy as np

from app.market.calendar import MarketCalendar
from app.strategy_e import costs, execution, exits, risk, signal
from app.strategy_e_v1_1 import context, decision
from app.strategy_e_v1_1.universe import UNIVERSE_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS = REPO_ROOT / "docs/backtest/strategy_e_candidate"
RULES_PATH = DOCS / "strategy_e_v1_1_replay_rules.json"
RULES_CANONICAL_SHA256 = "86b750ef664ef3c67d8560c5001940c77e792ceeab17a5d024690f939aa8045e"
E_D6_RULES_PATH = DOCS / "strategy_e_backtest_rules_v1.json"
E_D6_RULES_CANONICAL_SHA256 = "73056ac8e8cd6308c4affc343adaad75a7ea4e902e0f8c72b3bdc918e7cb4713"
FORWARD_HOLDOUT_START = date(2026, 9, 17)
TIMELINE_FIRST = date(2024, 10, 16)
TIMELINE_LAST = date(2026, 9, 16)
RUNTIME_ROOT = Path("data/runtime/strategy_e/v1_1_replay_runs")


class ReplayProtocolError(ValueError):
    """The replay protocol is not the frozen one, or it drifted from the frozen chain."""


def canonical_sha256(payload: object) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load(path: Path, expected: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = canonical_sha256(payload)
    if found != expected:
        raise ReplayProtocolError(f"{path.name} digest {found} != frozen {expected}")
    return payload


def timeline(calendar: MarketCalendar | None = None) -> list[date]:
    """The fixed evaluation sessions: every XNYS session 2024-10-16..2026-09-16."""
    market = calendar or MarketCalendar("America/New_York")
    out, day = [], TIMELINE_FIRST
    while day <= TIMELINE_LAST:
        out.append(day)
        day = market.next_trading_day(day)
    return out


def block_bounds(sessions: Sequence[date], blocks: int) -> list[list[str]]:
    """E-D6's block assignment (``numpy.array_split`` of the sorted sessions), bounds only."""
    parts = np.array_split(np.array(sorted(sessions), dtype=object), blocks)
    return [[part[0].isoformat(), part[-1].isoformat()] for part in parts]


def load_rules(path: Path = RULES_PATH) -> Mapping:
    payload = _load(path, RULES_CANONICAL_SHA256)
    e_d6 = _load(E_D6_RULES_PATH, E_D6_RULES_CANONICAL_SHA256)
    upstream = payload["upstream"]
    expected = {
        "trading_v1_1_rules_canonical_sha256": decision.RULES_CANONICAL_SHA256,
        "forward_feature_context_canonical_sha256": context.CONTRACT_CANONICAL_SHA256,
        "universe_version": UNIVERSE_VERSION,
        "e_d0_rules_canonical_sha256": signal.TRADING_RULES_CANONICAL_SHA256,
        "e_d2_execution_rules_canonical_sha256": execution.EXECUTION_RULES_CANONICAL_SHA256,
        "e_d3_horizon_semantics_canonical_sha256": exits.HORIZON_SEMANTICS_CANONICAL_SHA256,
        "e_d3_exit_rules_canonical_sha256": exits.EXIT_RULES_CANONICAL_SHA256,
        "e_d4_cost_rules_canonical_sha256": costs.COST_RULES_CANONICAL_SHA256,
        "e_d5_risk_rules_canonical_sha256": risk.RISK_RULES_CANONICAL_SHA256,
        "e_d6_backtest_rules_canonical_sha256": E_D6_RULES_CANONICAL_SHA256,
    }
    moved = [key for key, value in expected.items() if upstream.get(key) != value]
    if moved:
        raise ReplayProtocolError(f"replay protocol provenance is broken: {moved}")
    decision.load_rules()
    context.load_contract()

    stats, e_stats = payload["statistics"], e_d6["statistics"]
    same = (
        payload["costs"]["primary_scenario"] == e_d6["costs"]["primary_scenario"] == "COST_10BP"
        and [payload["costs"]["scenario_bp"][k] for k in payload["costs"]["scenarios"]]
        == e_d6["costs"]["sensitivity_total_round_trip_bp"]
        and stats["bootstrap"]["seed"] == e_stats["bootstrap"]["seed"]
        and stats["bootstrap"]["replicates"] == e_stats["bootstrap"]["replicates"]
        and stats["bootstrap"]["method"] == e_stats["bootstrap"]["method"]
        and stats["annualization_sessions"] == e_stats["annualization_sessions"]
        and stats["blocks"] == e_d6["chronological_robustness"]["blocks"]
        and all(payload["gate"][k] == e_d6["gate"][k]
                for k in ("data_quality", "net_economics", "statistical_support",
                          "chronological_robustness"))
        and payload["gate"]["precedence"] == e_d6["gate"]["precedence"]
    )
    if not same:
        raise ReplayProtocolError("replay gate, costs, bootstrap or blocks differ from E-D6")
    if set(payload["verdict_labels"]) != {
            "E-D6 BLOCKED — INTEGRITY", "E-D6 INCONCLUSIVE — DATA QUALITY", "E-D6 FAIL",
            "E-D6 INCONCLUSIVE — STATISTICAL", "E-D6 PASS — READY FOR PAPER / FORWARD"}:
        raise ReplayProtocolError("verdict labels must map every E-D6 outcome")

    sessions = timeline()
    line = payload["evaluation_timeline"]
    if (len(sessions) != line["count"] or sessions[0].isoformat() != line["first"]
            or sessions[-1].isoformat() != line["last"]
            or block_bounds(sessions, stats["blocks"]) != stats["expected_block_bounds"]):
        raise ReplayProtocolError("evaluation timeline or block bounds differ from the calendar")
    if (date.fromisoformat(payload["forward_exclusion"]["forward_holdout_start"])
            != FORWARD_HOLDOUT_START or sessions[-1] >= FORWARD_HOLDOUT_START):
        raise ReplayProtocolError("the development timeline reaches the forward holdout")
    return MappingProxyType(payload)
