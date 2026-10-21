"""Industry overlay, observation only (R1 frozen contract D1/D16 cap 25%, SIC_DERIV_V2 rule, FORWARD_PIT C.3).

The overlay never changes the paper book: it reads the post-trade holdings and records industry
weights and the *virtual* actions the overlay book would have taken (worst-rank full exits until the
industry is at or below 25% of signal NAV, and entries made into an industry above the cap).

It evaluates only when a forward point-in-time SIC snapshot exists for the signal date:
``<sic_store>/<signal>.json`` with ``fetched_at`` (UTC) inside the C.3 window [signal close 16:00 New
York, execution open 09:30 New York), one record per CIK. Any other case is recorded as
``NOT_EVALUATED`` with its reason; there is no historical or latest-snapshot fallback. Collecting that
snapshot is a separate collector that does not exist yet.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
KAPPA = 0.25   # observation threshold; decisions of the frozen contract are exact, this record is a diagnostic


def sic2_of(raw: Any) -> str | None:
    s = str(raw).strip() if raw is not None else ""
    if not re.fullmatch(r"[0-9]{1,4}", s) or int(s) == 0:
        return None
    return s.zfill(4)[:2]


def load_snapshot(sic_store: Path | None, signal: str, execution: str) -> tuple[dict[str, str | None] | None, str]:
    if sic_store is None:
        return None, "NO_SIC_STORE"
    p = Path(sic_store) / ("%s.json" % signal)
    if not p.exists():
        return None, "NO_FORWARD_PIT_SIC_SNAPSHOT"
    doc = json.loads(p.read_text())
    lo = datetime.combine(datetime.fromisoformat(signal).date(), time(16, 0), NY)
    hi = datetime.combine(datetime.fromisoformat(execution).date(), time(9, 30), NY)
    out = {}
    for r in doc.get("records", []):
        f = datetime.fromisoformat(r["fetched_at"].replace("Z", "+00:00"))
        if f.tzinfo is None or not (lo <= f < hi) or r.get("http_status") != 200:
            return None, "SNAPSHOT_OUTSIDE_PIT_WINDOW_OR_FAILED"
        out[str(r["cik"]).zfill(10)] = sic2_of(r.get("sic"))
    return out, "OK"


def observe(m21: Any, k: int, signal: str, execution: str, hold: dict[str, float], cash: float, sic_store: Path | None,
            entries: list[str] | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {"type": "OVERLAY_OBS", "session": execution, "k": k, "signal": signal, "affects_book": False}
    table, why = load_snapshot(sic_store, signal, execution)
    if table is None:
        ev.update(status="NOT_EVALUATED", reason=why)
        return ev
    m1 = m21.m1; C, adj, pos = m21.C, m21.adj, m21.pos
    R = m1.get_rank(signal, "MOM12_2"); snap = R["snap"]
    si, ei = pos[signal], pos[execution]
    x, grp, nav_cash = {}, {}, cash
    for tk, sh in hold.items():
        p = float(C[tk].iloc[si] * adj[tk].iloc[si] / adj[tk].iloc[ei])
        x[tk] = sh * p if p == p else None
        cik = (snap.get(tk) or {}).get("cik")
        g = table.get(str(cik).zfill(10)) if cik else None
        grp[tk] = g if g else "UNKNOWN"
    if any(v is None for v in x.values()):
        ev.update(status="NOT_EVALUATED", reason="MISSING_SIGNAL_PRICE")
        return ev
    nav = nav_cash + sum(x.values())
    E: dict[str, float] = {}
    for tk, v in x.items(): E[grp[tk]] = E.get(grp[tk], 0.0) + v
    cap = KAPPA * nav
    virt_exit = []
    for g in sorted(gg for gg in E if gg != "UNKNOWN" and E[gg] > cap):
        members = sorted((tk for tk in x if grp[tk] == g), key=lambda t: -(R["rank"].get(t) or 10 ** 9))
        e_g = E[g]
        for tk in members:
            if e_g <= cap: break
            virt_exit.append(tk); e_g -= x[tk]
    ev.update(status="EVALUATED", basis="post-trade shares x signal close in execution-share units / (cash + sum)",
              weights_pct={g: round(100 * v / nav, 4) for g, v in sorted(E.items())},
              over_cap=sorted(g for g in E if g != "UNKNOWN" and E[g] > cap),
              virtual_forced_exits=virt_exit,
              virtual_blocked_entries=sorted(t for t in (entries or []) if grp.get(t) in E and grp[t] != "UNKNOWN" and E[grp[t]] > cap),
              unknown_weight_pct=round(100 * E.get("UNKNOWN", 0.0) / nav, 4))
    return ev
