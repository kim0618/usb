"""What the [진입분석] preview is handed for one second (CONTRACT_ENTRY_ADVISOR_R0 section 10).

Read only. The stored `results.json` of a `wall-lifecycle.r1` run says which family x timing
cells passed the gate; the current btc-context snapshot (walls, flow, derivatives) supplies the
figures the panel shows as its basis. With no passing cell, which is the case today, the answer is
NO_TRADE and the panel says 관망. No order, no callback, no recorder write; the
`analysis_request_id` is deterministic so a later study could join a shown verdict to a manual
trade without any hook here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

RESULTS_ENV = "BTC_CONTEXT_ENTRY_ANALYSIS_RESULTS"
NO_RESULTS = "NO_RESEARCH_RESULTS"
NO_PASSING_FAMILY = "NO_VALIDATED_ENTRY_FAMILY"
#: Words the panel is allowed to use. No internal family or question name leaves this module.
VERDICT_WORDS = {"NO_TRADE": "관망", "LONG": "LONG", "SHORT": "SHORT"}


def results_path() -> Path | None:
    raw = os.environ.get(RESULTS_ENV, "").strip()
    return Path(raw) if raw else None


def load_results(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "advisor" in data else None


def passing_cells(results: dict[str, Any] | None) -> list[str]:
    cells = ((results or {}).get("advisor") or {}).get("cells") or {}
    return sorted(k for k, c in cells.items() if (c.get("gate") or {}).get("passes_review"))


def request_id(session8: str, sample_ms: int) -> str:
    return hashlib.sha256(f"{session8}|{sample_ms}|preview".encode()).hexdigest()[:16]


def _wall_basis(walls: list[dict[str, Any]], mid: float | None) -> list[dict[str, Any]]:
    out = []
    previous = mid
    for w in walls[:3]:
        price = float(w["price"])
        gap = abs(price - previous) / previous * 1e4 if previous else None
        out.append({"price": w["price"], "notional_usdt": w.get("notional_usdt"),
                    "age_ms": w.get("age_ms"), "continuity": w.get("continuity"),
                    "gap_bp": None if gap is None else round(gap, 2)})
        previous = price
    return out


def analysis(snapshot: dict[str, Any], results: dict[str, Any] | None, *, session8: str,
             sample_ms: int) -> dict[str, Any]:
    """The preview payload. `snapshot` is the btc-context `/snapshot` body."""
    walls = snapshot.get("walls") or {}
    mid = float(walls["mid"]) if walls.get("mid") else None
    passing = passing_cells(results)
    verdict = "NO_TRADE"
    reason = NO_RESULTS if results is None else NO_PASSING_FAMILY
    flow = snapshot.get("flow") or {}
    deriv = snapshot.get("derivatives") or {}
    return {
        "available": True,
        "verdict": verdict,
        "verdict_word": VERDICT_WORDS[verdict],
        "reason": reason,
        "statement": "현재 검증된 진입 조건 없음",
        "entry_zone": None, "invalidation": None, "targets": [], "expected_r": None,
        "passing_cells": passing,
        "research": {"version": (results or {}).get("version"),
                     "advisor_verdict": ((results or {}).get("advisor") or {}).get("verdict"),
                     "wall_verdict": (results or {}).get("wall_verdict")},
        "basis": {
            "mid": walls.get("mid"),
            "ask": _wall_basis(walls.get("ask") or [], mid),
            "bid": _wall_basis(walls.get("bid") or [], mid),
            "ask_touches": None, "bid_touches": None,
            "flow": {"window": flow.get("window"), "buy_pct": flow.get("buy_pct"),
                     "sell_pct": flow.get("sell_pct"), "live": flow.get("live")},
            "oi": {"value_usdt": deriv.get("oi_value_usdt"), "change_pct": deriv.get("oi_change_pct"),
                   "window": deriv.get("oi_window"), "direction": deriv.get("oi_direction")},
        },
        "analysis_request_id": request_id(session8, sample_ms),
        "sample_ms": sample_ms,
    }


__all__ = ["RESULTS_ENV", "results_path", "load_results", "passing_cells", "analysis", "request_id"]
