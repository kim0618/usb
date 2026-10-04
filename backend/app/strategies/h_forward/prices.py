"""H's own daily price store for the forward shadow, and the session calendar it is read against.

Two things this module is careful about.

**It never writes into the frozen historical mirror.** The D3-D6 price panel is read out of
``data/runtime/strategy_b_e0/mirror/...``, which is part of the frozen USB-HIST-V1 dataset and whose
digest is an audited fact. Forward sessions therefore go into a store of H's own, one small JSON per
session holding only the cohort's rows plus the benchmark, with the fetch recorded beside them.

**A session that was not collected is absent, not zero.** The trading calendar
(``app.market.MarketCalendar``, the same one A and E use) says which sessions were expected; the
store says which were observed. An expected session with no stored row is reported as a gap, and
``outcomes`` refuses to mature a horizon across one. Substituting the last available price for a
missing session is exactly how a forward observation quietly becomes a backtest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.market.calendar import MarketCalendar
from app.strategies.h_forward import store as ST

SOURCE = "MASSIVE_GROUPED_DAILY"
SCHEMA = "h_v2_d7_price_session_v1"
FIELDS = ("close", "high", "low", "volume")


@dataclass(frozen=True)
class Bar:
    session: str
    close: float
    high: float | None
    low: float | None
    volume: float | None


def session_path(session: str) -> Path:
    return ST.price_dir() / session[:4] / f"{session}.json"


def write_session(session: str, bars: Mapping[str, Mapping[str, Any]], *, fetched_at: str,
                  source: str = SOURCE) -> int:
    """Store one session's bars. A stored session is never rewritten with different numbers."""
    target = session_path(session)
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("bars") != dict(bars):
            raise ST.AppendOnlyViolation(f"price session {session} is already stored with other values")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema_version": SCHEMA, "session": session, "source": source,
            "fetched_at": fetched_at, "bars": dict(bars)}
    target.write_text(json.dumps(body, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 1


def read_session(session: str) -> dict[str, Any] | None:
    try:
        return json.loads(session_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stored_sessions() -> list[str]:
    root = ST.price_dir()
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.rglob("*.json") if len(p.stem) == 10)


def bars_of(session: str) -> dict[str, Bar]:
    body = read_session(session) or {}
    out: dict[str, Bar] = {}
    for symbol, row in (body.get("bars") or {}).items():
        close = row.get("close")
        if not isinstance(close, (int, float)):
            continue
        out[symbol] = Bar(session=session, close=float(close),
                          high=_opt(row.get("high")), low=_opt(row.get("low")),
                          volume=_opt(row.get("volume")))
    return out


def _opt(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def series(symbol: str, *, sessions: Sequence[str] | None = None) -> dict[str, Bar]:
    """One symbol's stored bars, keyed by session, in session order."""
    out: dict[str, Bar] = {}
    for session in sessions if sessions is not None else stored_sessions():
        bar = bars_of(session).get(symbol)
        if bar is not None:
            out[session] = bar
    return out


def latest_session() -> str | None:
    stored = stored_sessions()
    return stored[-1] if stored else None


# -- calendar -------------------------------------------------------------------------------------

def expected_sessions(start: str, end: str, *, calendar: MarketCalendar | None = None) -> list[str]:
    """Every regular trading session in ``(start, end]``, exclusive of ``start``.

    The window is open at the left on purpose: the launch baseline session is session zero, and
    horizon n is the nth session after it.
    """
    cal = calendar or MarketCalendar()
    begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    out: list[str] = []
    if finish <= begin:
        return out
    day = cal.next_trading_day(begin)
    while day <= finish:
        out.append(day.isoformat())
        day = cal.next_trading_day(day)
    return out


def gaps(start: str, end: str, *, calendar: MarketCalendar | None = None) -> list[str]:
    """Expected sessions in the window that the store does not hold."""
    have = set(stored_sessions())
    return [s for s in expected_sessions(start, end, calendar=calendar) if s not in have]


# -- collection -----------------------------------------------------------------------------------

def collect(sessions: Iterable[str], symbols: Iterable[str], *, client, fetched_at: str) -> dict[str, Any]:
    """Fetch and store the given sessions, keeping only the cohort's rows and the benchmark.

    One Massive ``grouped_daily`` call per session. A session the provider has no result for (a
    holiday the calendar disagrees about, or a session that has not settled yet) is reported as
    ``empty`` and nothing is stored for it, so the next run tries again.
    """
    wanted = set(symbols)
    written, empty, already, failed = [], [], [], {}
    for session in sessions:
        if read_session(session) is not None:
            already.append(session)
            continue
        try:
            body = client.grouped_daily(date.fromisoformat(session))
        except Exception as error:                      # provider refusal is data, not a crash
            failed[session] = f"{type(error).__name__}: {error}"
            continue
        rows = body.get("results") or []
        bars = {r["T"]: {"close": r.get("c"), "high": r.get("h"), "low": r.get("l"),
                         "volume": r.get("v")}
                for r in rows if r.get("T") in wanted and isinstance(r.get("c"), (int, float))}
        if not bars:
            empty.append(session)
            continue
        write_session(session, bars, fetched_at=fetched_at)
        written.append(session)
    return {"written": written, "already_stored": already, "no_result": empty, "failed": failed,
            "symbols": sorted(wanted), "source": SOURCE}
