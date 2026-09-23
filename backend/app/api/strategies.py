"""Read-only, per-strategy views of what is actually running: Strategy A and Strategy E-MAX V1.

One shape, two very different sources. A's account, positions and trades come from the paper
database it already writes; E's come from its own runtime files, because E deliberately does not
share A's database. The adapters are the only place that difference lives, so the UI asks the same
six questions of every strategy in the registry.

Three rules this layer keeps:

* nothing is summed across strategies. A and E each carry their own account, equity and PnL;
* E's provisional and official paper books stay separate, exactly as the runtime writes them, and
  the provisional one is never folded into the official totals;
* a value that does not exist yet is ``null`` with a state beside it, never a zero. "No official
  paper trade yet" and "zero profit" must not look alike.

Nothing here decides, orders or mutates anything.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.simulation import broker_projection
from app.core.config import get_settings
from app.core.database import get_db
from app.repositories.simulation import SimulationStateRepository
from app.services.performance_baseline import strategy_baseline_equity, strategy_performance_fields
from app.services.simulation_runtime import get_active_runtime, get_active_sim_broker
from app.strategies import registry as REG
from app.strategy_e_max_rt import readback as RB

router = APIRouter(prefix="/strategies", tags=["Strategies"])
DB = Annotated[Session, Depends(get_db)]
UNKNOWN = "UNKNOWN"


def _et_today() -> date:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(get_settings().market_timezone)).date()


def _not_found(strategy_id: str) -> dict[str, Any]:
    return {"strategy_id": strategy_id, "available": False, "state": "UNKNOWN_STRATEGY"}


# -- Strategy A ------------------------------------------------------------------------------------

def _a_daily(db: Session, limit: int = 250) -> tuple[list[dict[str, Any]], Any]:
    runtime = get_active_runtime()
    if runtime is None or runtime.account_id is None:
        return [], None
    repository = SimulationStateRepository(db)
    account = repository.get_account_by_id(runtime.account_id)
    if account is None:
        return [], None
    baseline = strategy_baseline_equity(db, runtime.account_id)
    rows = repository.list_daily_performance(runtime.account_id, limit=limit)
    return [{"trading_date": r.trading_date, "opening_equity": str(r.opening_equity),
             "closing_equity": str(r.closing_equity), "daily_pnl": str(r.daily_pnl),
             "cumulative_pnl": str(r.closing_equity - account.initial_cash),
             **strategy_performance_fields(r.trading_date, r.closing_equity, baseline)} for r in rows], account


def _a_status(db: Session) -> dict[str, Any]:
    broker = get_active_sim_broker()
    runtime = get_active_runtime()
    rows, account = _a_daily(db, limit=1)
    return {"runtime_status": "RUNNING" if broker is not None else "NO_ACTIVE_SIM_BROKER",
            "paper_status": "PAPER" if account is not None else "NO_ACCOUNT",
            "evidence_status": None, "mode": "SIMULATION_PAPER",
            "session": rows[0]["trading_date"] if rows else None,
            "last_decision": None, "last_update": None if runtime is None else getattr(runtime, "started_at", None),
            "detail": {"account_id": None if runtime is None else runtime.account_id}}


def _a_account(db: Session) -> dict[str, Any]:
    broker = get_active_sim_broker()
    rows, account = _a_daily(db, limit=2)
    projection = broker_projection(broker) if broker is not None else {"account": None, "open_positions": []}
    today = rows[0] if rows else None
    return {"currency": "USD",
            "initial_equity": None if account is None else str(account.initial_cash),
            "current_equity": (today or {}).get("closing_equity") or (projection["account"] or {}).get("equity"),
            "today_pnl": (today or {}).get("daily_pnl"),
            "total_pnl": (today or {}).get("cumulative_pnl"),
            "open_positions": len(projection["open_positions"]),
            "closed_trades_today": None,
            "source": "PAPER_DB", "evidence_status": None,
            "empty_reason": None if account is not None else "no active simulation account"}


def _a_positions(db: Session) -> list[dict[str, Any]]:
    broker = get_active_sim_broker()
    if broker is None:
        return []
    return [{"strategy_id": REG.STRATEGY_A, "symbol": p["symbol"], "quantity": p.get("quantity"),
             "average_price": p.get("average_price"), "cost_basis": p.get("cost_basis"),
             "opened_at": p.get("opened_at"), "source": "PAPER_DB"}
            for p in broker_projection(broker)["open_positions"]]


def _a_trades(db: Session, limit: int) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from app.models.execution import ShadowTradeRecord
    rows = db.scalars(select(ShadowTradeRecord).where(ShadowTradeRecord.is_control.is_(True))
                      .order_by(ShadowTradeRecord.id.desc()).limit(limit))
    return [{"strategy_id": REG.STRATEGY_A, "symbol": r.symbol, "status": r.status,
             "entry_price": None if r.average_entry_price is None else str(r.average_entry_price),
             "exit_price": None if r.average_exit_price is None else str(r.average_exit_price),
             "net_pnl": str(r.net_pnl), "exit_reason": r.exit_reason, "source": "PAPER_DB"} for r in rows]


def _a_equity(db: Session) -> dict[str, Any]:
    rows, account = _a_daily(db)
    points = [{"date": r["trading_date"], "equity": r["closing_equity"]} for r in reversed(rows)]
    return {"strategy_id": REG.STRATEGY_A, "currency": "USD", "points": points,
            "baseline": None if account is None else str(account.initial_cash), "source": "PAPER_DB"}


# -- Strategy E-MAX V1 -----------------------------------------------------------------------------

def _e_books() -> dict[str, dict[str, Any] | None]:
    return {status: RB.book(status) for status in RB.BOOK_STATUSES}


def _e_status() -> dict[str, Any]:
    record = RB.session_record()
    session = None if record is None else record.get("session")
    status = RB.evidence_status(record)
    engine = RB.engine_session(status, session) if (status and session) else None
    bootstrap = RB.bootstrap()
    today = _et_today()
    universe = RB.universe(today)
    runtime_status = (engine or {}).get("phase")
    if runtime_status is None:
        runtime_status = (record or {}).get("status") or "NO_SESSION_YET"
    return {
        "runtime_status": runtime_status,
        "paper_status": status or "NO_SESSION_YET",
        "evidence_status": status,
        "mode": "SIMULATION_VIRTUAL_ONLY",
        "session": session,
        "last_decision": (record or {}).get("decision_digest"),
        "last_update": (record or {}).get("_updated_at"),
        "detail": {
            "market_data_source": "KIWOOM",
            "rvol_threshold": (record or {}).get("rvol_threshold_frozen"),
            "rvol_window": (record or {}).get("rvol_window"),
            "canonical_universe": (record or {}).get("canonical_universe"),
            "rvol_ready": (record or {}).get("rvol_ready"),
            "rvol_missing": (record or {}).get("rvol_missing"),
            "market_data_unavailable": (record or {}).get("market_data_unavailable"),
            "sparse_no_premarket": (record or {}).get("sparse_no_premarket"),
            "h5_true": (record or {}).get("h5_true"),
            "h5_false": (record or {}).get("h5_false"),
            "h5_unknown": (record or {}).get("h5_unknown"),
            "selected": (record or {}).get("selected"),
            "no_decision_reason": (record or {}).get("reason"),
            "bootstrap": bootstrap,
            "universe": universe,
        },
    }


def _e_account() -> dict[str, Any]:
    record = RB.session_record()
    status = RB.evidence_status(record) or RB.PROVISIONAL
    books = _e_books()
    book = books.get(status)
    session = None if record is None else record.get("session")
    engine = RB.engine_session(status, session) if session else None
    summary = (engine or {}).get("summary") or {}
    entries = (engine or {}).get("entries") or {}
    exits = (engine or {}).get("exits") or {}
    filled = [s for s, e in entries.items() if e.get("status") == "FILLED"]
    have_trades = bool(book and book.get("sessions"))
    return {
        "currency": "USD",
        "initial_equity": None if book is None else book.get("initial_equity"),
        "current_equity": None if book is None else book.get("equity"),
        "today_pnl": summary.get("sim_realized_pnl"),
        "total_pnl": None if book is None else book.get("realized_pnl"),
        "open_positions": len([s for s in filled if s not in exits]),
        "closed_trades_today": len(exits),
        "source": "E_RUNTIME_FILES",
        "evidence_status": status,
        "books": {name: {"present": body is not None,
                         "initial_equity": (body or {}).get("initial_equity"),
                         "equity": (body or {}).get("equity"),
                         "realized_pnl": (body or {}).get("realized_pnl"),
                         "sessions": len((body or {}).get("sessions") or {})}
                  for name, body in books.items()},
        "separate_books": "PROVISIONAL과 OFFICIAL 성과는 합산하지 않는다",
        "empty_reason": None if have_trades else (
            "official paper 거래 없음" if status == RB.OFFICIAL else "RVOL 부트스트랩 중 (provisional)"),
    }


def _e_positions() -> list[dict[str, Any]]:
    record = RB.session_record()
    status = RB.evidence_status(record)
    session = None if record is None else record.get("session")
    engine = RB.engine_session(status, session) if (status and session) else None
    if engine is None:
        return []
    exits = engine.get("exits") or {}
    out = []
    for symbol, entry in (engine.get("entries") or {}).items():
        if entry.get("status") != "FILLED" or symbol in exits:
            continue
        intent = entry.get("intent") or {}
        out.append({"strategy_id": REG.STRATEGY_E_MAX_V1, "symbol": symbol,
                    "quantity": intent.get("quantity"), "average_price": entry.get("fill_price"),
                    "cost_basis": intent.get("notional"), "opened_at": entry.get("fill_at"),
                    "evidence_status": status, "source": "E_RUNTIME_FILES"})
    return out


def _e_trades(limit: int) -> list[dict[str, Any]]:
    out = []
    for status in RB.BOOK_STATUSES:
        for engine in RB.engine_sessions(status):
            exits = engine.get("exits") or {}
            for symbol, entry in (engine.get("entries") or {}).items():
                exit_row = exits.get(symbol)
                out.append({"strategy_id": REG.STRATEGY_E_MAX_V1, "session": engine.get("session"),
                            "symbol": symbol, "status": entry.get("status"),
                            "entry_price": entry.get("fill_price"), "exit_price": (exit_row or {}).get("fill_price"),
                            "net_pnl": None, "exit_reason": (exit_row or {}).get("exit_flag"),
                            "evidence_status": status, "source": "E_RUNTIME_FILES"})
    out.sort(key=lambda r: (r["session"] or "", r["symbol"]), reverse=True)
    return out[:limit]


def _e_equity() -> dict[str, Any]:
    series = {}
    for status in RB.BOOK_STATUSES:
        body = RB.book(status) or {}
        points = [{"date": session, "equity": row.get("equity_after")}
                  for session, row in sorted((body.get("sessions") or {}).items())]
        series[status] = {"points": points, "baseline": body.get("initial_equity")}
    current = RB.evidence_status(RB.session_record()) or RB.PROVISIONAL
    return {"strategy_id": REG.STRATEGY_E_MAX_V1, "currency": "USD", "source": "E_RUNTIME_FILES",
            "current_book": current, "points": series[current]["points"],
            "baseline": series[current]["baseline"], "books": series,
            "note": "PROVISIONAL과 OFFICIAL은 각자의 장부이며 이어 붙이지 않는다"}


# -- routes ------------------------------------------------------------------------------------------

@router.get("")
async def list_strategies(db: DB) -> list[dict[str, Any]]:
    out = []
    for meta in REG.REGISTRY:
        body = meta.to_json()
        if meta.strategy_id == REG.STRATEGY_A:
            body |= {k: v for k, v in _a_status(db).items() if k in ("runtime_status", "paper_status",
                                                                     "evidence_status", "session")}
        elif meta.strategy_id == REG.STRATEGY_E_MAX_V1:
            body |= {k: v for k, v in _e_status().items() if k in ("runtime_status", "paper_status",
                                                                   "evidence_status", "session")}
        else:
            body |= {"runtime_status": "NOT_RUNNING", "paper_status": None, "evidence_status": None,
                     "session": None}
        out.append(body)
    return out


@router.get("/{strategy_id}/status")
async def strategy_status(strategy_id: str, db: DB) -> dict[str, Any]:
    meta = REG.get(strategy_id)
    if meta is None:
        return _not_found(strategy_id)
    body = meta.to_json() | {"available": True}
    if strategy_id == REG.STRATEGY_A:
        return body | _a_status(db)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return body | _e_status()
    return body | {"runtime_status": "NOT_RUNNING", "paper_status": None, "evidence_status": None,
                   "session": None, "detail": {}}


@router.get("/{strategy_id}/account")
async def strategy_account(strategy_id: str, db: DB) -> dict[str, Any]:
    meta = REG.get(strategy_id)
    if meta is None:
        return _not_found(strategy_id)
    head = {"strategy_id": strategy_id, "display_name": meta.display_name, "available": True}
    if strategy_id == REG.STRATEGY_A:
        return head | _a_account(db)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return head | _e_account()
    return head | {"currency": None, "initial_equity": None, "current_equity": None, "today_pnl": None,
                   "total_pnl": None, "open_positions": 0, "closed_trades_today": None,
                   "source": "NONE", "empty_reason": "운영 전략이 아니다"}


@router.get("/{strategy_id}/positions")
async def strategy_positions(strategy_id: str, db: DB) -> list[dict[str, Any]]:
    if strategy_id == REG.STRATEGY_A:
        return _a_positions(db)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return _e_positions()
    return []


@router.get("/{strategy_id}/trades")
async def strategy_trades(strategy_id: str, db: DB,
                          limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    if strategy_id == REG.STRATEGY_A:
        return _a_trades(db, limit)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return _e_trades(limit)
    return []


@router.get("/{strategy_id}/equity")
async def strategy_equity(strategy_id: str, db: DB) -> dict[str, Any]:
    if strategy_id == REG.STRATEGY_A:
        return _a_equity(db)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return _e_equity()
    return {"strategy_id": strategy_id, "points": [], "baseline": None, "source": "NONE"}
