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
    return f"_{symbol}" if symbol.upper() in RESERVED else symbol


@dataclass
class Inventory:
    daily_sessions: set[date] = field(default_factory=set)
    reference_dates: set[date] = field(default_factory=set)
    splits_through: date | None = None
    minute_ranges: dict[str, list[tuple[date, date]]] = field(default_factory=dict)
    eligible_universe: dict[date, tuple[str, ...]] = field(default_factory=dict)

    def page_covers(self, symbol: str, day: date) -> bool:
        return any(a <= day <= b for a, b in self.minute_ranges.get(symbol, ()))


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
    if inventory.splits_through is None or inventory.splits_through < session:
        through = inventory.splits_through.isoformat() if inventory.splits_through else "none"
        reasons["MISSING_SPLITS_ASOF"] = f"split list covers executions through {through}, needs {session.isoformat()}"
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
        history = sum(inventory.page_covers(s, previous) for s in universe)
        if covered < eligible:
            reasons["MISSING_MINUTE"] = f"D minute page for {covered} of {eligible} eligible symbols"
        if history < eligible:
            reasons["MISSING_RVOL_HISTORY"] = f"minute history through D-1 for {history} of {eligible} eligible symbols"
    return {"session": session.isoformat(), "state": READY if not reasons else NOT_READY,
            "status": None if not reasons else context.FEATURE_CONTEXT_INCOMPLETE,
            "reasons": {k: reasons[k] for k in REASONS if k in reasons},
            "eligible_universe": eligible, "minute_covered": covered, "history_covered": history}


# -- storage probe (read-only) ------------------------------------------------------------------------

def _dates_in(directory: Path, pattern: str) -> set[date]:
    out = set()
    if directory.exists():
        for path in directory.rglob(pattern):
            found = re.findall(r"\d{4}-\d{2}-\d{2}", path.name)
            if found:
                out.add(date.fromisoformat(found[-1]))
    return out


def _minute_ranges(root: Path, symbols: Iterable[str] | None = None) -> dict[str, list[tuple[date, date]]]:
    out: dict[str, list[tuple[date, date]]] = {}
    for base, forward in ((root / RAW / "minute", False), (root / layout.MINUTE, True)):
        if not base.exists():
            continue
        with os.scandir(base) as dirs:
            for d in dirs:
                name = d.name[1:] if d.name.startswith("_") else d.name     # _CON: reserved-name directory
                if not d.is_dir() or (symbols is not None and name not in symbols):
                    continue
                with os.scandir(d.path) as files:
                    for f in files:
                        m = PAGE.match(f.name)
                        if m:
                            out.setdefault(m["sym"], []).append((date.fromisoformat(m["a"]),
                                                                 date.fromisoformat(m["b"])))
                            continue
                        m = FORWARD_PAGE.match(f.name) if forward else None
                        if m:
                            day = date.fromisoformat(m["d"])
                            out.setdefault(m["sym"], []).append((day, day))
    return out


def _splits_through(root: Path) -> date | None:
    ends = [date.fromisoformat(re.findall(r"\d{4}-\d{2}-\d{2}", p.name)[-1])
            for p in (root / RAW / "splits").glob("splits_*.json.gz")]
    ends += list(_dates_in(root / layout.SPLITS, "splits_asof_*.json.gz"))
    return max(ends) if ends else None


def eligible_universe_last_historical(root: Path, calendar: MarketCalendar) -> tuple[str, ...]:
    """D-1 daily eligibility for D = 2026-09-17 from the frozen USB-HIST-V1 daily panel.

    The split flag for (D-1, D] cannot be known without a split list through D, so it is taken as
    False here: the count is the pre-split upper bound, labelled as such by the caller.
    """
    from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
    from app.backtest.strategy_e0_overnight.dataset import load_daily_rows
    from app.strategy_e_v1_1 import universe as U
    _, history = load_daily_rows(root, load_e0_rules())
    panel = history.panel
    grid = list(panel.sessions)
    factor = panel.split_arrays()[0]
    membership = panel.membership()
    ok = U.daily_eligibility(layout.FORWARD_HOLDOUT_START, grid, close=panel.close, volume=panel.volume,
                             factor=factor, membership=membership[-1],
                             split_in_window=np.zeros(len(panel.tickers), dtype=bool), calendar=calendar)
    return tuple(sorted(t for t, e in zip(panel.tickers, ok) if e))


def probe(root: Path, *, calendar: MarketCalendar) -> tuple[Inventory, dict[str, Any]]:
    inv = Inventory()
    inv.daily_sessions = (_dates_in(root / RAW / "grouped_daily", "*.json.gz")
                          | _dates_in(root / layout.GROUPED_DAILY, "*.json.gz"))
    inv.reference_dates = (_dates_in(root / RAW / "reference_tickers", "CS_*.json.gz")
                           | _dates_in(root / layout.REFERENCE, "CS_*.json.gz"))
    inv.splits_through = _splits_through(root)
    inv.minute_ranges = _minute_ranges(root)
    universe = eligible_universe_last_historical(root, calendar)
    inv.eligible_universe[layout.FORWARD_HOLDOUT_START] = universe
    notes = {"forward_tree_exists": (root / layout.FORWARD_MARKET_DATA).exists(),
             "grouped_daily_last": max(inv.daily_sessions).isoformat() if inv.daily_sessions else None,
             "reference_last": max(inv.reference_dates).isoformat() if inv.reference_dates else None,
             "splits_through": inv.splits_through.isoformat() if inv.splits_through else None,
             "minute_symbols_with_pages": len(inv.minute_ranges),
             "minute_last_page_end": max(b for r in inv.minute_ranges.values() for _, b in r).isoformat()
             if inv.minute_ranges else None,
             "eligible_universe_2026_09_17_pre_split_upper_bound": len(universe),
             "eligible_with_history_through_2026_09_16": sum(inv.page_covers(s, layout.HISTORICAL_LAST_SESSION)
                                                             for s in universe)}
    return inv, notes


def matrix(sessions: Sequence[date], inventory: Inventory, *, today_et: date,
           calendar: MarketCalendar) -> list[dict[str, Any]]:
    return [assess(s, inventory, today_et=today_et, calendar=calendar) for s in sessions]


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {r: sum(r in row["reasons"] for row in rows) for r in REASONS}
    return {"sessions": len(rows), "ready": sum(row["state"] == READY for row in rows),
            "not_ready": sum(row["state"] == NOT_READY for row in rows), "reason_counts": counts}
