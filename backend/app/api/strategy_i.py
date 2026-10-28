"""Strategy I (MOM12-2) order-less paper: read-only view of its own store.

Separate from ``/strategies`` on purpose: Strategy I is not in the strategy registry, so it never enters
the A/E cards, the paper gate, the combined performance board or any E/H response. Errors are reported
in the body (``status``), never as a 500, the same way the H paper view does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.strategies.i_paper import views as IV

router = APIRouter(prefix="/strategy-i", tags=["Strategy I paper"])

GUARD_TAIL_BYTES = 65536
GUARD_FIELDS = ("decision", "at", "guard", "exit_code", "reasons")


def guard_readback(store: Path) -> dict[str, Any] | None:
    """Latest and latest-SKIP decisions the timer guard appended to ``guard.jsonl`` (tail read only).

    A guard SKIP is a normal outcome (the run is held back under host pressure), so the screen needs it
    to explain why the as-of has not moved; the latest record alone can hide it behind a later manual RUN.
    """
    p = store / "guard.jsonl"
    try:
        if not p.is_file(): return None
        with p.open("rb") as fh:
            fh.seek(max(0, p.stat().st_size - GUARD_TAIL_BYTES))
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError as ex:
        return {"latest": {"decision": "UNREADABLE", "error": str(ex)[:200]}, "last_skip": None}
    recs = []
    for ln in lines:
        try: g = json.loads(ln)
        except ValueError: continue          # first line of the tail window may be cut
        if isinstance(g, dict): recs.append({k: g.get(k) for k in GUARD_FIELDS})
    if not recs:
        return {"latest": {"decision": "UNREADABLE"}, "last_skip": None} if lines else None
    skips = [r for r in recs if r["decision"] == "SKIP"]
    return {"latest": recs[-1], "last_skip": skips[-1] if skips else None}


@router.get("/paper")
async def strategy_i_paper() -> dict[str, Any]:
    try:
        view = IV.build()
    except Exception as ex:        # read model failure: say so, do not hide it behind an HTTP error
        view = {"strategy_id": IV.STRATEGY_ID, "display_name": IV.DISPLAY, "status": "ERROR", "launched": None,
                "reason": "read model failed: %s" % str(ex)[:300], "orders": "NONE"}
    return dict(view, guard=guard_readback(IV.default_store()))
