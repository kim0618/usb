"""Forward session readiness: which closed forward sessions could be sealed today, and why not.

``assess`` is pure: it takes an ``Inventory`` (what the store holds) and applies the E-R1 feature
context contract's completeness rules, one explicit reason per missing input. ``probe`` builds the
inventory from the workspace, read-only, without fetching or taking the writer lock.

The minute-history check is at page-range level (a symbol counts as history-collected when a
Common Raw or forward page covers D-1); the exact per-session RVOL range check belongs to the
forward builder and ``context.require_complete``. Any missing input is FEATURE_CONTEXT_INCOMPLETE:
the session is NOT_READY and never becomes an H5 = False session.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import os
from pathlib import Path
import re
from typing import Any

import numpy as np

from app.backtest.strategy_e1_forward import layout
from app.market.calendar import MarketCalendar
from app.strategy_e_v1_1 import context

READY, NOT_READY = "READY", "NOT_READY"
REASONS = ("SESSION_NOT_CLOSED", "MISSING_DAILY_CONTEXT", "MISSING_SPLITS_ASOF", "MISSING_REFERENCE",
           "MISSING_MINUTE", "MISSING_RVOL_HISTORY", "MISSING_SPY_CONTEXT")
RAW = "market_data/raw/massive"
PAGE = re.compile(r"^(?P<sym>.+)_(?P<a>\d{4}-\d{2}-\d{2})_(?P<b>\d{4}-\d{2}-\d{2})\.p\d+\.json\.gz$")
FORWARD_PAGE = re.compile(r"^(?P<sym>.+)_(?P<d>\d{4}-\d{2}-\d{2})\.json\.gz$")
#: DOS device names cannot be directories on the Windows-hosted store; forward pages use _<SYMBOL>.
RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{d}" for d in "0123456789"), *(f"LPT{d}" for d in "0123456789")})


def forward_minute_dir(symbol: str) -> str:
    from app.strategy_e_max_forward.storage import symbol_to_storage
    return symbol_to_storage(symbol)


@dataclass
class Inventory:
    daily_sessions: set[date] = field(default_factory=set)
    reference_dates: set[date] = field(default_factory=set)
    splits_through: date | None = None
    splits_asof: set[date] = field(default_factory=set)
    minute_ranges: dict[str, list[tuple[date, date]]] = field(default_factory=dict)
    eligible_universe: dict[date, tuple[str, ...]] = field(default_factory=dict)

    def page_covers(self, symbol: str, day: date) -> bool:
        return any(a <= day <= b for a, b in self.minute_ranges.get(symbol, ()))

    def first_collected(self, symbol: str) -> date | None:
        ranges = self.minute_ranges.get(symbol)
        return min(a for a, _ in ranges) if ranges else None


#: RVOL needs up to 20 qualifying prior sessions; the page-level check asks for this many collected
#: sessions ending D-1 (or all sessions since the symbol's first collected page). The exact per-session
#: rule is the forward builder's (context.require_complete).
HISTORY_WINDOW = 25


def forward_sessions(until_exclusive: date, calendar: MarketCalendar) -> list[date]:
    out, day = [], layout.FORWARD_HOLDOUT_START
    while day < until_exclusive:
        if calendar.session(day) is not None:
            out.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    return out


def _previous(day: date, n: int, calendar: MarketCalendar) -> list[date]:
    out, cur = [], day
    for _ in range(n):
        cur = calendar.previous_trading_day(cur)
        out.append(cur)
    return out[::-1]


def assess(session: date, inventory: Inventory, *, today_et: date,
           calendar: MarketCalendar | None = None) -> dict[str, Any]:
    market = calendar or MarketCalendar("America/New_York")
    layout.require_forward_session(session)
    reasons: dict[str, str] = {}
    previous = market.previous_trading_day(session)
    if session >= today_et:
        reasons["SESSION_NOT_CLOSED"] = "Massive Stocks Basic serves a session only after its ET date ends (T-1)"
    window = _previous(session, context.REQUIRED_DAILY_SESSIONS, market)
    absent = [d for d in window if d not in inventory.daily_sessions]
    if absent:
        reasons["MISSING_DAILY_CONTEXT"] = f"grouped daily missing for {len(absent)} of the 21 sessions ending D-1 ({absent[0].isoformat()}..)"
    if not any(d <= previous for d in inventory.reference_dates):
        reasons["MISSING_REFERENCE"] = "no CS reference snapshot dated on or before D-1"
    if session not in inventory.splits_asof:
        through = inventory.splits_through.isoformat() if inventory.splits_through else "none"
        reasons["MISSING_SPLITS_ASOF"] = (f"no verified splits_asof_{session.isoformat()} (frozen list covers "
                                          f"executions through {through})")
    spy = []
    if previous not in inventory.daily_sessions:
        spy.append("SPY close(D-1)")
    if not inventory.page_covers("SPY", session):
        spy.append("SPY minute page for D")
    if spy:
        reasons["MISSING_SPY_CONTEXT"] = ", ".join(spy)
    universe = inventory.eligible_universe.get(session)
    if universe is None:
        reasons["MISSING_MINUTE"] = "D-1 daily-eligible universe cannot be formed, so minute coverage cannot be checked; no forward page for D exists for any symbol" \
            if not any(inventory.page_covers(s, session) for s in inventory.minute_ranges) else \
            "D-1 daily-eligible universe cannot be formed"
        reasons["MISSING_RVOL_HISTORY"] = "undetermined until the D-1 universe exists"
        eligible = covered = history = None
    else:
        eligible = len(universe)
        covered = sum(inventory.page_covers(s, session) for s in universe)
        window = _previous(session, HISTORY_WINDOW, market)
        lacking = [s for s in universe if not _history_ok(inventory, s, window)]
        history = eligible - len(lacking)
        missing_minute = [s for s in universe if not inventory.page_covers(s, session)]
        if missing_minute:
            reasons["MISSING_MINUTE"] = (f"D minute page for {covered} of {eligible} eligible symbols "
                                         f"(e.g. {', '.join(missing_minute[:5])})")
        if lacking:
            reasons["MISSING_RVOL_HISTORY"] = (f"collected minute history for the {HISTORY_WINDOW} sessions ending "
                                               f"D-1 for {history} of {eligible} (missing {', '.join(lacking[:5])})")
    missing_symbols = {"minute": missing_minute[:50], "rvol_history": lacking[:50]} if universe is not None else None
    return {"session": session.isoformat(), "state": READY if not reasons else NOT_READY, "missing_symbols": missing_symbols,
            "status": None if not reasons else context.FEATURE_CONTEXT_INCOMPLETE,
            "reasons": {k: reasons[k] for k in REASONS if k in reasons},
            "eligible_universe": eligible, "minute_covered": covered, "history_covered": history}


def _history_ok(inventory: Inventory, symbol: str, window: Sequence[date]) -> bool:
    first = inventory.first_collected(symbol)
    if first is None:
        return False
    return all(inventory.page_covers(symbol, d) for d in window if d >= first)


# -- storage probe (read-only) ------------------------------------------------------------------------

def _dates_in(directory: Path, pattern: str) -> set[date]:
    out = set()
    if directory.exists():
        for path in directory.rglob(pattern):
            found = re.findall(r"\d{4}-\d{2}-\d{2}", path.name)
            if found:
                out.add(date.fromisoformat(found[-1]))
    return out


def _raw_minute_ranges(root: Path) -> dict[str, list[tuple[date, date]]]:
    """Common Raw (historical) pages: trusted as collected by historical_v2; read-only."""
    out: dict[str, list[tuple[date, date]]] = {}
    base = root / RAW / "minute"
    if not base.exists():
        return out
    with os.scandir(base) as dirs:
        for d in dirs:
            if not d.is_dir():
                continue
            with os.scandir(d.path) as files:
                for f in files:
                    m = PAGE.match(f.name)
                    if m:
                        out.setdefault(m["sym"], []).append((date.fromisoformat(m["a"]), date.fromisoformat(m["b"])))
    return out


def _verified_forward_ranges(root: Path, tree: str) -> dict[str, list[tuple[date, date]]]:
    """Forward / RVOL-context ledgers that are COMPLETE and carry a PASS validation sidecar."""
    from app.strategy_e_max_forward import storage as ST
    out: dict[str, list[tuple[date, date]]] = {}
    base = root / tree
    if not base.exists():
        return out
    for ledger in base.glob("*/*.request.json"):
        side = ST.read_sidecar(ledger)
        if side is None or side.get("status") != ST.PASS:
            continue
        symbol = ST.storage_to_symbol(ledger.parent.name)
        a, b = side["range"]
        out.setdefault(symbol, []).append((date.fromisoformat(a), date.fromisoformat(b)))
    return out


def _verified_dates(directory: Path, pattern: str) -> set[date]:
    from app.strategy_e_max_forward import storage as ST
    out = set()
    if directory.exists():
        for path in directory.rglob(pattern):
            if path.name.endswith(".validation.json") or not ST.verified(path):
                continue
            out.add(date.fromisoformat(re.findall(r"\d{4}-\d{2}-\d{2}", path.name)[-1]))
    return out


def _splits_through(root: Path) -> date | None:
    ends = [date.fromisoformat(re.findall(r"\d{4}-\d{2}-\d{2}", p.name)[-1])
            for p in (root / RAW / "splits").glob("splits_*.json.gz")]
    return max(ends) if ends else None


def probe(root: Path, *, calendar: MarketCalendar, sessions: Sequence[date]) -> tuple[Inventory, dict[str, Any]]:
    """Read-only inventory: frozen raw inputs plus verified forward files, and each forward session's
    D-1 daily-eligible universe from ``forward_daily`` (None when its daily inputs are incomplete)."""
    from app.strategy_e_max_forward import forward_daily as FD, storage as ST
    inv = Inventory()
    inv.daily_sessions = (_dates_in(root / RAW / "grouped_daily", "*.json.gz")
                          | _verified_dates(root / layout.GROUPED_DAILY, "*.json.gz"))
    inv.reference_dates = (_dates_in(root / RAW / "reference_tickers", "CS_*.json.gz")
                           | _verified_dates(root / layout.REFERENCE, "CS_*.json.gz"))
    inv.splits_through = _splits_through(root)
    inv.splits_asof = _verified_dates(root / layout.SPLITS, "splits_asof_*.json.gz")
    ranges = _raw_minute_ranges(root)
    for tree in (ST.MINUTE, ST.RVOL_CONTEXT):
        for symbol, extra in _verified_forward_ranges(root, tree).items():
            ranges.setdefault(symbol, []).extend(extra)
    inv.minute_ranges = ranges
    base = FD.load_base(root)
    missing_inputs = {}
    for session in sessions:
        universe, missing = FD.eligible_universe(base, root, session, calendar)
        if universe is not None:
            inv.eligible_universe[session] = universe
        else:
            missing_inputs[session.isoformat()] = missing
    notes = {"forward_tree_exists": (root / layout.FORWARD_MARKET_DATA).exists(),
             "grouped_daily_last": max(inv.daily_sessions).isoformat() if inv.daily_sessions else None,
             "reference_last": max(inv.reference_dates).isoformat() if inv.reference_dates else None,
             "splits_frozen_through": inv.splits_through.isoformat() if inv.splits_through else None,
             "splits_asof_verified": sorted(d.isoformat() for d in inv.splits_asof),
             "minute_symbols_with_pages": len(inv.minute_ranges),
             "minute_last_page_end": max(b for r in inv.minute_ranges.values() for _, b in r).isoformat()
             if inv.minute_ranges else None,
             "eligible_universe": {d.isoformat(): len(u) for d, u in sorted(inv.eligible_universe.items())},
             "universe_inputs_missing": missing_inputs}
    return inv, notes


def matrix(sessions: Sequence[date], inventory: Inventory, *, today_et: date,
           calendar: MarketCalendar) -> list[dict[str, Any]]:
    return [assess(s, inventory, today_et=today_et, calendar=calendar) for s in sessions]


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {r: sum(r in row["reasons"] for row in rows) for r in REASONS}
    return {"sessions": len(rows), "ready": sum(row["state"] == READY for row in rows),
            "not_ready": sum(row["state"] == NOT_READY for row in rows), "reason_counts": counts}
