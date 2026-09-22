"""Can the measured Kiwoom capabilities produce the frozen 09:24-cutoff H5 inputs for the whole
canonical universe before 09:30? Pure arithmetic over ``strategy_e_max_kiwoom_scanner_capabilities_v1``.

Every mode must reproduce, for every D-1 daily-eligible symbol, the premarket block of bars starting
in [04:00, 09:24] (last, high, low, volume, dollar volume, the 09:00 window) with no print after
09:24:59, and the B2 denominator must stay the canonical universe. A mode that would drop a possible
H5 symbol (false negative) or read a post-cutoff print is infeasible, however fast.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.strategy_e_max_rt import config as CFG

MANIFEST = CFG.REPO_ROOT / "docs/backtest/strategy_e_max/strategy_e_max_kiwoom_scanner_capabilities_v1.json"
DECISION_WINDOW_SECONDS = 300.0          # 09:25:00 (09:24 bar complete) -> 09:30:00 entry


def load(path: Path = MANIFEST) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ModeVerdict:
    mode: str
    feasible: bool
    reasons: tuple[str, ...]


def evaluate(universe_rows: int, caps: Mapping[str, Any] | None = None) -> dict[str, ModeVerdict]:
    caps = caps or load()
    rest_rate = float(caps["rest"]["rate_limit"]["limit_per_api_id_per_second"])
    ws_cap = int(caps["websocket"]["max_realtime_symbols_per_app_key"])
    rank_cap = max(int(v) for k, v in caps["rest"]["rankings"].items() if k.startswith("usa"))
    final_pass = universe_rows / rest_rate           # one usa06011 page per symbol after 09:24:59
    out = {
        "FULL_WEBSOCKET": ModeVerdict("FULL_WEBSOCKET", universe_rows <= ws_cap, (
            f"{universe_rows} symbols > {ws_cap} realtime items per app key (one session per key)",)
            if universe_rows > ws_cap else ()),
        "A_SAFE_PREFILTER_WEBSOCKET": ModeVerdict("A_SAFE_PREFILTER_WEBSOCKET", False, (
            f"no false-negative-free prefilter: rankings stop at {rank_cap} rows, US condition search not available, "
            "and no H5 necessary condition is monotone before 09:24 (gap, range position and the 09:00 return "
            "can change until the cutoff; volume only rises)",)),
        "B_REST_SCAN_ROTATING_WEBSOCKET": ModeVerdict("B_REST_SCAN_ROTATING_WEBSOCKET", False, (
            "rotating FE subscriptions miss prints while a symbol is unsubscribed, so the premarket high / low "
            "(FE 16-18 are fixed in premarket) and the 09:00 window cannot be exact; quote snapshots after 09:24 "
            "include post-cutoff prints",)),
        "C_CONDITION_SEARCH": ModeVerdict("C_CONDITION_SEARCH", False, ("US condition search not available",)),
        "D_REST_ROLLING_MINUTE": ModeVerdict("D_REST_ROLLING_MINUTE", final_pass <= DECISION_WINDOW_SECONDS, (
            f"the last page of every symbol can only be read after 09:24:59: {universe_rows} / {rest_rate:g} per s "
            f"= {final_pass:.0f} s > {DECISION_WINDOW_SECONDS:.0f} s",) if final_pass > DECISION_WINDOW_SECONDS else ()),
    }
    return out


def blockers(universe_rows: int, caps: Mapping[str, Any] | None = None) -> list[str]:
    verdicts = evaluate(universe_rows, caps)
    if any(v.feasible for v in verdicts.values()):
        return []
    return [f"{v.mode}: {'; '.join(v.reasons)}" for v in verdicts.values()]
