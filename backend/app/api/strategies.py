"""Read-only, per-strategy views of what is running: Strategy A, Strategy E-MAX V1 and Strategy H.

One shape, three very different sources. A's account, positions and trades come from the paper
database it already writes; E's come from its own runtime files, because E deliberately does not
share A's database; H's come from its own append-only forward files, because H holds no capital book
at all. The adapters are the only place that difference lives, so the UI asks the same six questions
of every strategy in the registry.

Strategy H was added in H-V2-D7 additively: no A or E branch, book, figure or route in this file
changed, H is not folded into the A+E combined column or the A/E portfolio simulation (it has no
initial equity to sum), and H is not judged by the A/E paper gate. H's own questions - the decision
cohort, the valuation legs, the forward outcomes - are served by the two ``/forward`` routes.

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
from app.strategies import books as BK
from app.strategies import official as OFF
from app.strategies.h_forward import contract as HC
from app.strategies.h_forward import views as HV
from app.strategies import paper_gate as GATE
from app.strategies import performance as PERF
from app.strategies import registry as REG
from app.strategy_e_max_rt import readback as RB

router = APIRouter(prefix="/strategies", tags=["Strategies"])
DB = Annotated[Session, Depends(get_db)]
UNKNOWN = "UNKNOWN"


def _et_today() -> date:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(get_settings().market_timezone)).date()


def _not_found(strategy_id: str) -> dict[str, Any]:
    return {"strategy_id": strategy_id, "available": False, "state": "UNKNOWN_STRATEGY"}


# -- operational status --------------------------------------------------------------------------------
# One vocabulary for every operating strategy, separate from the research lifecycle in the registry.

RUNNING, WAITING_SIGNAL, POSITION_OPEN, PAUSED, DATA_ERROR = (
    "RUNNING", "WAITING_SIGNAL", "POSITION_OPEN", "PAUSED", "DATA_ERROR")

_E_PHASE_TO_OPERATION = {
    "WAITING": WAITING_SIGNAL, "IDLE": WAITING_SIGNAL, "COMPLETE": WAITING_SIGNAL, "NO_SESSION_YET": WAITING_SIGNAL,
    "DECIDED": RUNNING, "POSITION_OPEN": POSITION_OPEN,
    "NO_DECISION": DATA_ERROR, "ERROR": DATA_ERROR, "DISABLED": PAUSED,
}


def _a_operational(broker_active: bool, open_positions: int) -> str:
    if not broker_active:
        return PAUSED
    return POSITION_OPEN if open_positions else WAITING_SIGNAL


def _e_operational(runtime_status: str | None) -> str:
    """E's engine phase, or the runner's own status when the engine never started that session."""
    return _E_PHASE_TO_OPERATION.get(runtime_status or "", DATA_ERROR)


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
    positions = broker_projection(broker)["open_positions"] if broker is not None else []
    return {"runtime_status": "RUNNING" if broker is not None else "NO_ACTIVE_SIM_BROKER",
            "operational_status": _a_operational(broker is not None, len(positions)),
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
             "average_price": p.get("average_price"), "cost_basis": p.get("invested_notional"),
             "opened_at": p.get("opened_at"), "source": "PAPER_DB"}
            for p in broker_projection(broker)["open_positions"]]


def _a_trades(db: Session, limit: int) -> list[dict[str, Any]]:
    """A's own paper ledger (``simulation_trades`` of the active account), newest first.

    Before 2026-09-27 this read ``shadow_trades`` control rows, which the paper runtime never
    writes, so the list was empty while A had closed paper trades."""
    return [row | {"evidence_status": None, "source": "PAPER_DB"}
            for row in BK.a_trades(db, BK.a_account_id(db), limit)]


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
        "operational_status": _e_operational(runtime_status),
        "paper_status": status or "NO_SESSION_YET",
        "evidence_status": status,
        "mode": "SIMULATION_VIRTUAL_ONLY",
        "session": session,
        "last_decision": (record or {}).get("decision_digest"),
        "last_update": (record or {}).get("_updated_at"),
        "detail": {
            "market_data_source": "KIWOOM",
            "rvol_threshold": (record or {}).get("rvol_threshold_frozen") or RB.frozen_rvol_threshold(),
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
        "accounting_version": RB.CURRENT,
        "legacy_books": {name: {"present": body is not None, "equity": (body or {}).get("equity"),
                                "realized_pnl": (body or {}).get("realized_pnl"),
                                "sessions": len((body or {}).get("sessions") or {}),
                                "accounting_version": RB.LEGACY}
                         for name, body in ((s, RB.book(s, RB.LEGACY)) for s in RB.BOOK_STATUSES)},
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
    """Every book (evidence grade x accounting root), each row tagged with its own; never merged."""
    out = [trade | {"evidence_status": status, "source": "E_RUNTIME_FILES"}
           for accounting in (RB.CURRENT, RB.LEGACY) for status in RB.BOOK_STATUSES
           for session in BK.e_session_trades(status, accounting) for trade in session["trades"]]
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
        elif meta.strategy_id == REG.STRATEGY_H_V2:
            body |= {k: v for k, v in HV.status().items() if k in ("runtime_status", "paper_status",
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
    if strategy_id == REG.STRATEGY_H_V2:
        return body | HV.status()
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
    if strategy_id == REG.STRATEGY_H_V2:
        return head | HV.account()
    return head | {"currency": None, "initial_equity": None, "current_equity": None, "today_pnl": None,
                   "total_pnl": None, "open_positions": 0, "closed_trades_today": None,
                   "source": "NONE", "empty_reason": "운영 전략이 아니다"}


@router.get("/{strategy_id}/positions")
async def strategy_positions(strategy_id: str, db: DB) -> list[dict[str, Any]]:
    if strategy_id == REG.STRATEGY_A:
        return _a_positions(db)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return _e_positions()
    if strategy_id == REG.STRATEGY_H_V2:
        return HV.positions()                 # a WATCH is an observation, never a position
    return []


@router.get("/{strategy_id}/trades")
async def strategy_trades(strategy_id: str, db: DB,
                          limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
    if strategy_id == REG.STRATEGY_A:
        return _a_trades(db, limit)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return _e_trades(limit)
    if strategy_id == REG.STRATEGY_H_V2:
        return HV.trades()                    # H writes no trade row; see /forward
    return []


@router.get("/{strategy_id}/equity")
async def strategy_equity(strategy_id: str, db: DB) -> dict[str, Any]:
    if strategy_id == REG.STRATEGY_A:
        return _a_equity(db)
    if strategy_id == REG.STRATEGY_E_MAX_V1:
        return _e_equity()
    if strategy_id == REG.STRATEGY_H_V2:
        return HV.equity()
    return {"strategy_id": strategy_id, "points": [], "baseline": None, "source": "NONE"}


# -- cards, ledger, performance, portfolio -------------------------------------------------------------

def _latest(values: list[str | None]) -> str | None:
    present = [v for v in values if v]
    return max(present) if present else None


def _today_realized(trades: list[dict[str, Any]], today: date) -> str:
    total = sum((Decimal(t["net_pnl"]) for t in trades
                 if t.get("net_pnl") is not None and t.get("exit_at")
                 and datetime.fromisoformat(str(t["exit_at"])).astimezone(ZoneInfo("America/New_York")).date() == today),
                Decimal(0))
    return str(total)


def _book_summary(book: PERF.Book) -> dict[str, Any]:
    closed = PERF.closed_with_net(book.trades)
    return {"trades": len(closed), "net_pnl": _sum_net(closed),
            "accounting_versions": sorted({t.get("accounting_version") or "UNKNOWN" for t in closed})}


def _card(db: Session, meta: REG.StrategyMeta) -> dict[str, Any]:
    today = _et_today()
    head = {k: v for k, v in meta.to_json().items()
            if k in ("strategy_id", "display_name", "short_name", "variant_label", "version",
                     "research_lifecycle", "lifecycle")}
    clock = OFF.state()
    if meta.strategy_id == REG.STRATEGY_A:
        status, account = _a_status(db), _a_account(db)
        broker = get_active_sim_broker()
        projection = broker_projection(broker) if broker is not None else {"account": None}
        books = BK.a_books(db, _a_positions(db))
        trades = BK.a_trades(db, BK.a_account_id(db))
        unrealized = (projection["account"] or {}).get("unrealized_pnl")
        official = [t for t in trades if t.get("evaluation") == OFF.OFFICIAL]
        return head | {
            "operational_status": status["operational_status"], "runtime_status": status["runtime_status"],
            "evidence_status": None, "currency": "USD",
            "equity": account["current_equity"], "initial_equity": account["initial_equity"],
            "open_positions": account["open_positions"],
            "today_realized_pnl": _today_realized(official, today),
            "today_unrealized_pnl": unrealized,
            "official": _book_summary(books["official"]), "legacy": _book_summary(books["legacy"]),
            "net_pnl": _book_summary(books["official"])["net_pnl"],
            "trades": _book_summary(books["official"])["trades"],
            "paper_clock": clock,
            "last_signal_at": None, "last_trade_at": _latest([t.get("exit_at") or t.get("entry_at") for t in trades]),
            "na": {"last_signal_at": "A 원장(simulation_trades)은 신호 시각을 기록하지 않는다",
                   **({"today_unrealized_pnl": "보유 포지션 시가 평가값 없음"} if unrealized is None else {})},
        }
    if meta.strategy_id == REG.STRATEGY_H_V2:
        # H shows states and counts where A and E show money: it has no capital book, so a money
        # field is None with a reason rather than a zero that would read as break-even.
        return head | HV.card()
    status, account = _e_status(), _e_account()
    evidence = account["evidence_status"]
    books = BK.e_books()
    sessions = [s for accounting in (RB.CURRENT, RB.LEGACY) for s in BK.e_session_trades(RB.OFFICIAL, accounting)]
    trades = [t for s in sessions for t in s["trades"]]
    official = [t for t in trades if t.get("evaluation") == OFF.OFFICIAL]
    open_now = account["open_positions"]
    return head | {
        "operational_status": status["operational_status"], "runtime_status": status["runtime_status"],
        "evidence_status": evidence, "currency": "USD",
        "equity": account["current_equity"], "initial_equity": account["initial_equity"],
        "open_positions": open_now,
        "today_realized_pnl": _today_realized(official, today),
        "today_unrealized_pnl": "0" if not open_now else None,
        "official": _book_summary(books["official"]), "legacy": _book_summary(books["legacy"]),
        "net_pnl": _book_summary(books["official"])["net_pnl"],
        "trades": _book_summary(books["official"])["trades"],
        "paper_clock": clock,
        "last_signal_at": _latest([s.get("decided_at") for s in sessions]),
        "last_trade_at": _latest([t.get("exit_at") or t.get("entry_at") for t in trades]),
        "na": {} if not open_now else {"today_unrealized_pnl": "E는 보유 중 시가 평가를 기록하지 않는다"},
    }


def _sum_net(trades: list[dict[str, Any]]) -> str | None:
    nets = [Decimal(t["net_pnl"]) for t in trades if t.get("status") == "CLOSED" and t.get("net_pnl") is not None]
    return str(sum(nets, Decimal(0))) if nets else None


@router.get("/cards")
async def strategy_cards(db: DB) -> list[dict[str, Any]]:
    """One card per operating strategy (registry ``enabled``); closed research never gets a card."""
    return [_card(db, meta) for meta in REG.enabled()]


@router.get("/ledger")
async def strategy_ledger(db: DB, strategy_id: str | None = None,
                          limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[dict[str, Any]]:
    """Canonical trade records of the operating strategies, each owned by exactly one book."""
    rows: list[dict[str, Any]] = []
    if strategy_id in (None, REG.STRATEGY_A):
        rows += _a_trades(db, limit)
    if strategy_id in (None, REG.STRATEGY_E_MAX_V1):
        rows += _e_trades(limit)
    if strategy_id in (None, REG.STRATEGY_H_V2):
        rows += HV.trades()          # empty by design: H's records are decisions, not trades
    rows.sort(key=lambda r: str(r.get("entry_at") or r.get("session") or ""), reverse=True)
    return rows[:limit]


def _books(db: Session) -> dict[str, dict[str, PERF.Book]]:
    """The books the A+E combined column and the A/E portfolio simulation read. A and E only.

    H is deliberately absent: ``PERF.combined_daily`` sums initial equities, and H has none, so
    adding it would either force an invented equity or silently return nothing. H's metrics are
    published as their own column in ``/performance`` instead (``_h_books``).
    """
    e_open = [p for p in _e_positions() if p.get("evidence_status") == RB.OFFICIAL]
    return {REG.STRATEGY_A: BK.a_books(db, _a_positions(db)), REG.STRATEGY_E_MAX_V1: BK.e_books(e_open)}


@router.get("/performance")
async def strategy_performance(db: DB) -> dict[str, Any]:
    """Official (ACCOUNTING_V1, from the official start) and legacy (V0) metrics per strategy.

    Only the official books feed the combined column and the frozen paper gate. Legacy metrics are
    reference figures, computed from the rows exactly as recorded, and never mixed with official.
    """
    books = _books(db)
    a, e = books[REG.STRATEGY_A], books[REG.STRATEGY_E_MAX_V1]
    clock = OFF.state()
    start = clock["official_paper_start"]
    e_sessions = BK.e_operating_sessions(RB.OFFICIAL, RB.CURRENT, start) if start else []
    # H's book is added to the per-strategy table only, through the same pure calculator, so its
    # column is computed by the same definitions as A's and E's and none of theirs move.
    with_h = books | {REG.STRATEGY_H_V2: HV.books()}
    official = {sid: PERF.strategy_metrics(b["official"]) for sid, b in with_h.items()}
    legacy = {sid: PERF.strategy_metrics(b["legacy"]) for sid, b in with_h.items()}
    provisional = [BK.e_provisional(acc) for acc in (RB.CURRENT, RB.LEGACY)]
    return {
        "currency": "USD",
        "paper_clock": clock,
        "strategies": {sid: {"strategy_id": sid, "official": official[sid] | {"accounting_version": "V1"},
                             "legacy": legacy[sid] | {"accounting_version": "V0"}} for sid in with_h},
        "combined": PERF.combined_metrics([a["official"], e["official"]]) | {"accounting_version": "V1"},
        "legacy_combined": PERF.combined_metrics([a["legacy"], e["legacy"]]) | {"accounting_version": "V0"},
        "gate": {
            REG.STRATEGY_A: GATE.evaluate(REG.STRATEGY_A, official[REG.STRATEGY_A], error_sessions=None,
                                          divergence=None),
            REG.STRATEGY_E_MAX_V1: GATE.evaluate(
                REG.STRATEGY_E_MAX_V1, official[REG.STRATEGY_E_MAX_V1],
                error_sessions=sum(1 for s in e_sessions if s["error"]) if start else None,
                divergence=BK.e_divergence(start) if start else None),
        },
        "books": {REG.STRATEGY_A: "PAPER_DB", REG.STRATEGY_E_MAX_V1: f"{RB.BOOK_ROOTS[RB.CURRENT]}/{RB.OFFICIAL}",
                  REG.STRATEGY_H_V2: HV.SOURCE},
        # H's own state. It is not a gate row: the A/E paper gate contract does not list H, and a
        # forward observation with no matured horizon has no verdict to give yet.
        "h_forward": HV.forward(),
        "combined_definition": "Combined는 A와 E의 공식 장부 합계다. H는 자본 장부가 없어 합산 대상이 아니다",
        "excluded": {"PROVISIONAL_RVOL_BOOTSTRAP": {
            "trades": sum(len(b.trades) for b in provisional), "sessions": sum(len(b.daily) for b in provisional),
            "reason": "부트스트랩 기간 장부는 평가·합산에서 제외한다"}},
        "e_sessions": e_sessions,
    }


@router.get("/daily")
async def strategy_daily(db: DB, limit: Annotated[int, Query(ge=1, le=400)] = 60) -> dict[str, Any]:
    """One row per session: what each strategy made or lost that day, and what it traded.

    This is the operating question - "how much today, how much yesterday" - answered once, instead
    of leaving it to be reassembled from four screens. It composes nothing new: the daily points and
    the trades are the same ones ``/performance`` and ``/ledger`` publish, grouped by session.

    Strategy H has no row here and that is not an omission: it holds no capital, so there is no
    daily figure to report. Its state is carried in ``strategies`` beside the rows.
    """
    books = _books(db)
    official = {sid: book["official"] for sid, book in books.items()}
    per_day: dict[str, dict[str, Any]] = {}
    for sid, book in official.items():
        for point in book.daily:
            row = per_day.setdefault(point.day, {"session": point.day, "strategies": {}, "trades": []})
            row["strategies"][sid] = {"pnl": PERF.money(point.pnl), "equity": PERF.money(point.equity),
                                      "trades": 0}
        for trade in book.trades:
            session = trade.get("session")
            if session is None or trade.get("status") != "CLOSED":
                continue
            row = per_day.setdefault(session, {"session": session, "strategies": {}, "trades": []})
            row["strategies"].setdefault(sid, {"pnl": None, "equity": None, "trades": 0})["trades"] += 1
            row["trades"].append({k: trade.get(k) for k in
                                  ("strategy_id", "symbol", "entry_at", "entry_price", "exit_at",
                                   "exit_price", "qty", "net_pnl", "costs", "exit_reason",
                                   "holding_seconds", "accounting_version", "evaluation")})
    rows = []
    for session in sorted(per_day, reverse=True)[:limit]:
        row = per_day[session]
        amounts = [Decimal(s["pnl"]) for s in row["strategies"].values() if s.get("pnl") is not None]
        row["total_pnl"] = PERF.money(sum(amounts, Decimal(0))) if amounts else None
        row["trades"].sort(key=lambda t: str(t.get("exit_at") or t.get("entry_at") or ""))
        rows.append(row)
    # Why a strategy can read as flat for weeks: its own trades may predate the official clock.
    # Those sessions are in the legacy (V0) book, which charges costs twice and is never mixed in
    # here. Saying so is the difference between "it made nothing" and "its record is elsewhere".
    before_start = {}
    for sid, book in books.items():
        legacy = [point.day for point in book["legacy"].daily if point.pnl != 0]
        if legacy:
            before_start[sid] = {"sessions": len(legacy), "last_session": max(legacy)}
    h_state = HV.status()["detail"]
    return {
        "currency": "USD", "paper_clock": OFF.state(), "rows": rows,
        "excluded_before_start": before_start,
        "strategies": [{"strategy_id": sid, "display_name": REG.get(sid).display_name,
                        "short_name": REG.get(sid).short_name, "has_daily_pnl": True}
                       for sid in official]
                      + [{"strategy_id": REG.STRATEGY_H_V2, "display_name": "Strategy H",
                          "short_name": "H", "has_daily_pnl": False,
                          "reason": HV.NO_CAPITAL_BOOK,
                          "decision_counts": h_state["decision_counts"],
                          "launch": h_state["launch"]}],
        "note": "일별 손익은 각 전략의 자체 장부에서 읽은 그대로이고, 합계는 단순 합입니다",
    }


@router.get("/portfolio")
async def strategy_portfolio(db: DB, book: Annotated[str, Query(pattern="^(official|legacy)$")] = "official"
                             ) -> dict[str, Any]:
    """The A+E portfolio view: exposure by symbol, cash usage and the 50/50 risk-budget simulation.

    ``book=official`` (default) reads only ACCOUNTING_V1 official books; ``book=legacy`` reads only V0.
    The two are never blended into one curve, correlation or PF."""
    books = _books(db)
    a, e = books[REG.STRATEGY_A][book], books[REG.STRATEGY_E_MAX_V1][book]
    broker = get_active_sim_broker()
    projection = broker_projection(broker) if broker is not None else {"account": None}
    a_cash = (projection["account"] or {}).get("cash")
    a_equity = (projection["account"] or {}).get("equity")
    return {
        "currency": "USD",
        "book": book, "accounting_version": "V1" if book == "official" else "V0",
        "paper_clock": OFF.state(),
        "baseline": PERF.portfolio_baseline(a, e),
        "combined": {k: v for k, v in PERF.combined_metrics([a, e]).items()
                     if k in ("net_pnl", "mdd", "mdd_amount", "initial_equity", "equity_change", "trades")},
        "exposure": PERF.exposure([books[REG.STRATEGY_A]["official"], books[REG.STRATEGY_E_MAX_V1]["official"]]),
        "cash_usage": {
            REG.STRATEGY_A: {"cash": a_cash, "equity": a_equity,
                             "cash_share": None if not (a_cash and a_equity) else str(Decimal(a_cash) / Decimal(a_equity))},
            REG.STRATEGY_E_MAX_V1: {"cash": None, "equity": (RB.book(RB.OFFICIAL) or {}).get("equity"), "cash_share": None,
                                    "note": "E 런타임은 현금 잔고를 기록하지 않는다. 세션 사이에는 전액 현금이다"},
        },
    }


# -- Strategy H forward shadow ------------------------------------------------------------------------
# H-only routes. They exist because a decision cohort has no place in a trade ledger: an issuer with
# a Bear leg refused, a binding clause and four unmatured horizons is not a trade record with empty
# columns. A and E never reach these routes and never answer them.

@router.get("/{strategy_id}/forward")
async def strategy_forward(strategy_id: str) -> dict[str, Any]:
    """H's cohort: every issuer's decision, valuation legs, live price and forward outcomes."""
    if strategy_id != REG.STRATEGY_H_V2:
        return {"strategy_id": strategy_id, "available": False,
                "state": "NOT_A_FORWARD_SHADOW_STRATEGY",
                "reason": "forward shadow 기록은 Strategy H만 가진다"}
    return {"available": True} | HV.forward()


@router.get("/{strategy_id}/forward/{ticker}")
async def strategy_forward_issuer(strategy_id: str, ticker: str) -> dict[str, Any]:
    """One H issuer in full: D3/D4/D5/D6 legs, decision history and every horizon."""
    if strategy_id != REG.STRATEGY_H_V2:
        return {"strategy_id": strategy_id, "ticker": ticker, "available": False,
                "state": "NOT_A_FORWARD_SHADOW_STRATEGY"}
    body = HV.issuer(ticker.upper())
    if body is None:
        return {"strategy_id": strategy_id, "ticker": ticker.upper(), "available": False,
                "state": "NOT_IN_COHORT",
                "reason": "launch snapshot에 없는 종목이다",
                "contract": HC.state()}
    return {"available": True} | body
