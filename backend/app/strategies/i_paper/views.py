"""Read model of the Strategy I paper store for the operating screen. Pure reads, no writes, no orders.

Every figure comes from the paper store (``launch.json``, ``events.jsonl``, ``state.json``,
``runs.jsonl``, ``last_error.json``). Before launch, or with no completed run, no account figure,
position or return is produced: the response says which state the book is in and nothing else.

States (``status``):

* ``NOT_LAUNCHED``  no ``launch.json``.
* ``LAUNCHED_NO_RUN``  launched, no completed run yet.
* ``RUNNING``  last run completed and its as-of is the latest session that could be stored.
* ``DATA_DELAYED``  last completed as-of is behind the expected latest session.
* ``INPUT_BLOCKED``  the last run refused its inputs (point-in-time view incomplete).
* ``ERROR``  the last run failed (integrity or other); the stored book is shown as of its last good run.

The industry overlay is returned in its own ``overlay`` block, labelled virtual; it is never merged
into positions, cash or performance.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import engine as EN

STRATEGY_ID = "STRATEGY_I_MOM12_2"
DISPLAY = "Strategy I · MOM12-2"
NY = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[4]


def default_store() -> Path:
    return Path(os.environ.get("USB_STRATEGY_I_PAPER_STORE") or (REPO_ROOT / "data/runtime/strategy_i_paper"))


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _events(store: Path) -> list[dict[str, Any]]:
    p = store / "events.jsonl"
    if not p.exists(): return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln]


def expected_latest_session(cal, now: datetime) -> str:
    """Newest session a nightly grouped-daily run could have stored: the XNYS session before today (ET)."""
    return cal.previous_trading_day(now.astimezone(NY).date()).isoformat()


def build(store: Path | None = None, now: datetime | None = None, cal=None) -> dict[str, Any]:
    from app.market.calendar import MarketCalendar
    store = Path(store or default_store())
    cal = cal or MarketCalendar()
    now = now or datetime.now(NY)
    base: dict[str, Any] = {"strategy_id": STRATEGY_ID, "display_name": DISPLAY, "orders": "NONE", "store": str(store),
                            "initial_capital": "10000", "target_holdings": 20}
    launch = _read_json(store / "launch.json")
    if not launch:
        return dict(base, status="NOT_LAUNCHED", reason="Paper가 시작되지 않았습니다 (launch.json 없음).", launched=False)
    state = _read_json(store / "state.json")
    err = _read_json(store / "last_error.json")
    base.update(launched=True, launch_signal=launch["launch_signal"], launched_at=launch.get("created_at"),
                continuity_contract="US-B-I-PAPER-CONTINUITY-PIT-V1")
    if not state:
        st = "INPUT_BLOCKED" if err and err.get("kind") == "INPUT_BLOCKED" else ("ERROR" if err else "LAUNCHED_NO_RUN")
        return dict(base, status=st, reason=(err or {}).get("error") or "아직 완료된 실행이 없습니다.", error=err)
    ev = _events(store)
    expected = expected_latest_session(cal, now)
    if err:
        status = "INPUT_BLOCKED" if err.get("kind") == "INPUT_BLOCKED" else "ERROR"
    elif state["as_of"] < expected:
        status = "DATA_DELAYED"
    else:
        status = "RUNNING"
    navs = [e for e in ev if e["type"] == "NAV"]
    trades = [e for e in ev if e["type"] == "TRADE"]
    execs = [e for e in ev if e["type"] == "EXEC"]
    latest = state.get("latest") or {}
    # average cost of the shares still held (buys add notional + fee; sells remove cost pro rata)
    cost: dict[str, list[float]] = {}
    for t in trades:
        sh, c = cost.get(t["ticker"], [0.0, 0.0])
        if t["side"].startswith("BUY"):
            sh += t["shares"]; c += t["notional"] + t["cost"]
        else:
            if sh > 0: c -= c * min(1.0, t["shares"] / sh)
            sh -= t["shares"]
        if sh <= 1e-9: cost.pop(t["ticker"], None)
        else: cost[t["ticker"]] = [sh, c]
    nav = navs[-1]["nav"] if navs else None
    positions = []
    for p in latest.get("positions", []):
        sh, c = cost.get(p["ticker"], [p["shares"], None])
        positions.append({"ticker": p["ticker"], "shares": p["shares"], "avg_cost": (c / sh) if c is not None and sh else None,
                          "last_close": p["close"], "value": p["value"], "weight": (p["value"] / nav) if nav else None, "stale_price": p["stale"]})
    prev_nav = navs[-2]["nav"] if len(navs) >= 2 else 10000.0
    account = None
    if navs:
        account = {"as_of": navs[-1]["session"], "nav": nav, "cash": latest.get("cash_from_logged_trades"),
                   "positions_value": sum(p["value"] for p in positions) if positions else 0.0,
                   "total_pnl": nav - 10000.0, "total_return": nav / 10000.0 - 1, "day_pnl": nav - prev_nav,
                   "day_return": nav / prev_nav - 1 if prev_nav else None, "day_session": navs[-1]["session"],
                   "holdings": len(positions), "operating_sessions": len(navs)}
    from datetime import timedelta
    a0 = date.fromisoformat(state["as_of"])
    nxt = EN.month_end_signals(cal, a0, a0 + timedelta(days=70))
    nxt = [d for d in nxt if d.isoformat() > state["as_of"]]
    obs = [e for e in ev if e["type"] == "OVERLAY_OBS"]
    flags = {"continuity_unresolved": [e for e in ev if e["type"] == "CONTINUITY_UNRESOLVED"][-10:],
             "price_stop_held": [e for e in ev if e["type"] == "PRICE_STOP_HELD"][-10:],
             "continuity_linked": [e for e in ev if e["type"] == "CONTINUITY_LINKED"][-10:],
             "stale_positions": [p["ticker"] for p in positions if p["stale_price"]],
             "data_delay": {"as_of": state["as_of"], "expected": expected, "delayed": state["as_of"] < expected},
             "pending_signals": state.get("pending_signals", []), "refused_signals": state.get("refused_signals", []),
             "error": err}
    return dict(base, status=status, reason=(err or {}).get("error"),
                last_run={"as_of": state["as_of"], "completed_at": state.get("completed_at"), "events": state["events"]},
                next_signal=nxt[0].isoformat() if nxt else None,
                account=account, positions=sorted(positions, key=lambda p: -p["value"]),
                rebalances=[{k: e.get(k) for k in ("k", "session", "signal", "holdings", "trade_count", "cash_pct", "nav_post", "costs",
                                                   "turnover_one_way")} for e in execs][::-1],
                trades=[{k: t[k] for k in ("session", "k", "side", "ticker", "shares", "notional", "cost", "stale_last_close")} for t in trades][::-1][:200],
                nav_series=[{"session": n["session"], "nav": n["nav"]} for n in navs],
                overlay={"virtual": True, "affects_book": False, "latest": obs[-1] if obs else None,
                         "evaluated_count": sum(1 for o in obs if o.get("status") == "EVALUATED"), "observations": len(obs)},
                flags=flags)
