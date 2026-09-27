"""Assemble each operating strategy's own books from its own storage.

Every strategy has two books here, and they are never merged:

* **official**: ACCOUNTING_V1 rows on or after the official start (``app.strategies.official``).
  This is the only book the paper gate, the combined column and the portfolio view read. Before the
  start is set it is empty. A non-V1 row dated on or after the start is kept in it on purpose, so
  the accounting mix shows and the gate reads it as ``ACCOUNTING_MIXED`` instead of hiding it.
* **legacy**: rows written under V0 (before ACCOUNTING_V1), kept exactly as recorded, for reference.

A: the paper database of the active runtime's account (``simulation_trades``,
``account_daily_performance``, the live SimBroker for open positions).
E: runtime files. Official = ``paper_state_v1/OFFICIAL_KIWOOM_PAPER``; legacy = ``paper_state/
OFFICIAL_KIWOOM_PAPER`` (V0). PROVISIONAL books are reported beside, never counted.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.simulation import AccountDailyPerformanceRecord, SimulationAccountRecord, SimulationTradeRecord
from app.strategies import ledger as L
from app.strategies import official as OFF
from app.strategies import registry as REG
from app.strategies.performance import Book, DailyPoint
from app.strategy_e_max_rt import readback as RB

E_ERROR_PHASES = {"ERROR", "NO_DECISION"}


def _meta(strategy_id: str) -> REG.StrategyMeta:
    meta = REG.get(strategy_id)
    assert meta is not None
    return meta


def _start() -> str | None:
    begin = OFF.start()
    return None if begin is None else begin.isoformat()


# -- A ---------------------------------------------------------------------------------------------

def a_account_id(db: Session) -> int | None:
    from app.services.simulation_runtime import get_active_runtime
    runtime = get_active_runtime()
    return None if runtime is None else runtime.account_id


def a_trades(db: Session, account_id: int | None, limit: int | None = None) -> list[dict[str, Any]]:
    if account_id is None:
        return []
    stmt = (select(SimulationTradeRecord).where(SimulationTradeRecord.account_id == account_id)
            .order_by(SimulationTradeRecord.entry_time.desc(), SimulationTradeRecord.id.desc()))
    if limit:
        stmt = stmt.limit(limit)
    version = _meta(REG.STRATEGY_A).version
    return [L.a_trade_record(row, strategy_id=REG.STRATEGY_A, strategy_version=version, account_id=account_id)
            for row in db.scalars(stmt)]


def _a_daily(db: Session, account_id: int | None) -> list[AccountDailyPerformanceRecord]:
    if account_id is None:
        return []
    return list(db.scalars(select(AccountDailyPerformanceRecord)
                           .where(AccountDailyPerformanceRecord.account_id == account_id)
                           .order_by(AccountDailyPerformanceRecord.trading_date)))


def _points(rows: list[AccountDailyPerformanceRecord]) -> list[DailyPoint]:
    return [DailyPoint(r.trading_date.isoformat(), Decimal(r.daily_pnl), Decimal(r.closing_equity)) for r in rows]


def a_books(db: Session, open_positions: list[dict[str, Any]] | None = None) -> dict[str, Book]:
    account_id = a_account_id(db)
    account = None if account_id is None else db.get(SimulationAccountRecord, account_id)
    trades = list(reversed(a_trades(db, account_id)))
    rows = _a_daily(db, account_id)
    start = _start()
    official_rows = [] if start is None else [r for r in rows if r.trading_date.isoformat() >= start]
    legacy_rows = [r for r in rows if start is None or r.trading_date.isoformat() < start]
    official_trades = [] if start is None else [t for t in trades if (t.get("session") or "") >= start]
    legacy_trades = [t for t in trades if t.get("accounting_version") == "V0"
                     and (start is None or (t.get("session") or "") < start)]
    official_initial = Decimal(official_rows[0].opening_equity) if official_rows else None
    return {
        "official": Book(REG.STRATEGY_A, official_initial, official_trades, _points(official_rows),
                         [r.trading_date.isoformat() for r in official_rows], open_positions or []),
        "legacy": Book(REG.STRATEGY_A, None if account is None else Decimal(account.initial_cash),
                       legacy_trades, _points(legacy_rows), [r.trading_date.isoformat() for r in legacy_rows]),
    }


# -- E ---------------------------------------------------------------------------------------------

def e_session_trades(status: str, accounting: str = RB.CURRENT) -> list[dict[str, Any]]:
    """Every engine session of one evidence book, normalised, with its reconciliation."""
    body = RB.book(status, accounting) or {}
    version = _meta(REG.STRATEGY_E_MAX_V1).version
    return [L.e_trade_records(state, strategy_id=REG.STRATEGY_E_MAX_V1, strategy_version=version, book=status,
                              book_session=(body.get("sessions") or {}).get(state.get("session")))
            | {"phase": state.get("phase"), "summary": state.get("summary"),
               "decided_at": (state.get("decision") or {}).get("decided_at"),
               "equity_at_open": state.get("equity_at_open"), "accounting_version": accounting}
            for state in RB.engine_sessions(status, accounting)]


def e_operating_sessions(status: str, accounting: str = RB.CURRENT, since: str | None = None) -> list[dict[str, Any]]:
    """The sessions the E runtime was expected to decide under one evidence grade and book.

    A session belongs from the first session record carrying that grade on (and, for the official
    book, from the official start). It is ``error`` when its engine ended in ERROR or NO_DECISION,
    or when the runner left no engine session at all; a decided session with no candidate is normal.
    """
    engines = {s["session"]: s for s in e_session_trades(status, accounting)}
    dirs = RB.session_dirs()
    first = next((d.name for d in dirs
                  if RB.evidence_status(RB.session_record(d.name) or {}) == status), None)
    if first is None:
        return []
    floor = max(first, since) if since else first
    out = []
    for directory in dirs:
        if directory.name < floor:
            continue
        engine = engines.get(directory.name)
        phase = None if engine is None else engine.get("phase")
        out.append({"session": directory.name, "phase": phase,
                    "error": engine is None or phase in E_ERROR_PHASES})
    return out


def _e_book(status: str, accounting: str, since: str | None,
            open_positions: list[dict[str, Any]] | None) -> Book:
    body = RB.book(status, accounting)
    rows = sorted(((body or {}).get("sessions") or {}).items())
    initial = None if body is None else Decimal(body["initial_equity"])
    if since is not None and initial is not None:
        before = [r for day, r in rows if day < since]
        if before:
            initial = Decimal(before[-1]["equity_after"])     # equity carried into the first official session
        rows = [(day, r) for day, r in rows if day >= since]
    daily = [DailyPoint(day, Decimal(r["sim_realized_pnl"]), Decimal(r["equity_after"])) for day, r in rows]
    trades = [t for s in e_session_trades(status, accounting) if since is None or (s["session"] or "") >= since
              for t in s["trades"]]
    operating = [s["session"] for s in e_operating_sessions(status, accounting, since)]
    return Book(REG.STRATEGY_E_MAX_V1, initial, trades, daily, operating, open_positions or [])


def e_books(open_positions: list[dict[str, Any]] | None = None) -> dict[str, Book]:
    start = _start()
    official = (_e_book(RB.OFFICIAL, RB.CURRENT, start, open_positions) if start is not None
                else Book(REG.STRATEGY_E_MAX_V1, None, [], [], []))
    return {"official": official, "legacy": _e_book(RB.OFFICIAL, RB.LEGACY, None, None)}


def e_provisional(accounting: str) -> Book:
    return _e_book(RB.PROVISIONAL, accounting, None, None)


def e_divergence(since: str | None, accounting: str = RB.CURRENT) -> Decimal | None:
    """Mean of (paper session return - recorded net_10bp view) over completed sessions (gate metric)."""
    values = []
    for session in e_session_trades(RB.OFFICIAL, accounting):
        if since is not None and (session["session"] or "") < since:
            continue
        summary = session.get("summary") or {}
        view = (summary.get("fixed_bp_session_return_views") or {}).get("net_10bp")
        realized, start = summary.get("sim_realized_pnl"), session.get("equity_at_open")
        if session.get("phase") != "COMPLETE" or view is None or realized is None or not start:
            continue
        values.append(Decimal(realized) / Decimal(start) - Decimal(view))
    return sum(values, Decimal(0)) / len(values) if values else None
