"""H paper book operations and read model: launch once, run daily, verify, and answer the screens.

``run`` is the daily step. It recomputes every paper event from the forward ledger and the price
store (``engine``), checks that the stored events are reproduced as a prefix, appends only the new
tail and records the infrastructure state. ``verify`` re-checks the invariants that would invalidate
the book: a WATCH or REJECT that entered, a fill at or before its decision, more than three open
positions, a position entered twice, NAV arithmetic that does not add up, a D5 contract other than
D5_D2R_V1, or any stored event the inputs no longer reproduce.

Everything a screen shows is derived from the events, so the book has one source.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.market.calendar import MarketCalendar
from app.strategies.h_forward import contract as FC
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import store as ST
from app.strategies.h_paper import contract as PC
from app.strategies.h_paper import engine as EN
from app.strategies.h_paper import store as PS
from app.strategies.performance import Book, DailyPoint

SELL_EVENTS = tuple(sorted(set(EN.SELL_ACTION.values())))
FILL_EVENTS = ("BUY",) + SELL_EVENTS
ACCOUNTING = "CASH_CHARGES_ONLY_V1"
NOT_LAUNCHED = "H_PAPER_NOT_LAUNCHED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def launched() -> bool:
    return PS.launch() is not None


def mode() -> str:
    return PC.SHADOW_PLUS_PAPER if launched() else PC.SHADOW_ONLY


def _file_sha(name: str) -> str | None:
    target = ST.path(name)
    return hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None


def launch(*, at: str | None = None) -> dict[str, Any]:
    """Write H_PAPER_V1_LAUNCH once. Only APPROVEs recorded after ``launched_at`` can ever trade."""
    if launched():
        return {"launched": False, "reason": "ALREADY_LAUNCHED", "launch": PS.launch()}
    if not ST.launched():
        return {"launched": False, "reason": "FORWARD_SHADOW_NOT_LAUNCHED"}
    latest = ST.latest_per_ticker()
    counts = {state: 0 for state in FC.DECISION_STATES}
    for row in latest.values():
        if row.get("decision") in counts:
            counts[row["decision"]] += 1
    body = {"record": PC.LAUNCH_RECORD, "strategy_id": FC.STRATEGY_H, "contract_id": PC.contract_id(),
            "contract_sha256": PC.sha256(), "launched_at": at or _now(),
            "initial_equity": str(PC.initial_equity()), "currency": "USD", "leverage": "1.0",
            "sizing_contract": PC.sizing_contract(), "max_open_positions": PC.max_open_positions(),
            "execution_version": "execution_v0", "accounting": ACCOUNTING,
            "forward_ledger_rows_at_launch": len(ST.ledger()),
            "forward_ledger_sha256_at_launch": _file_sha(ST.FORWARD_LEDGER),
            "decision_counts_at_launch": counts,
            "historical_backfill": False,
            "before_launch": PC.SHADOW_ONLY, "after_launch": PC.SHADOW_PLUS_PAPER}
    PS.write_launch(body)
    return {"launched": True, "launch": body}


def _identities() -> dict[str, dict[str, Any]]:
    return {r["ticker"]: {"cik": r.get("cik"), "security_id": r.get("security_id")} for r in ST.launch_rows()}


def compute(*, calendar: MarketCalendar | None = None) -> EN.Result | None:
    body = PS.launch()
    if body is None:
        return None
    engine = EN.Engine(body, ST.ledger(), PR.stored_sessions(), PR.bars_of, calendar=calendar,
                       identities=_identities())
    return engine.run()


def run(*, calendar: MarketCalendar | None = None) -> dict[str, Any]:
    """The daily paper step. Appends only what the recomputation adds after the stored prefix."""
    if not launched():
        return {"paper_run": "NOT_LAUNCHED", "appended": 0, "problems": []}
    result = compute(calendar=calendar)
    assert result is not None
    stored = PS.events()
    tail, problems = EN.reconcile(stored, result.events, PS.canonical)
    problems += invariant_problems(result.events)
    appended = 0 if problems else PS.append_events(tail)
    status = {"checked_at": _now(), "paper_run": "OK" if not problems else "PAPER_INTEGRITY_FAILURE",
              "problems": problems, "appended": appended, "events_total": len(stored) + appended,
              "processed_through": result.processed_through, "stalled_at": result.stalled_at,
              "pending": result.pending, "open_positions": len(result.positions),
              "cash": _s(result.cash), "price_store_latest": PR.latest_session()}
    PS.write_status(status)
    return status


def verify(*, calendar: MarketCalendar | None = None) -> dict[str, Any]:
    if not launched():
        return {"verdict": "PASS", "launched": False, "problems": []}
    result = compute(calendar=calendar)
    assert result is not None
    stored = PS.events()
    _, problems = EN.reconcile(stored, result.events, PS.canonical)
    problems += invariant_problems(stored)
    return {"verdict": "PASS" if not problems else "FAIL", "launched": True, "problems": problems,
            "stored_events": len(stored), "recomputed_events": len(result.events)}


def invariant_problems(events: Sequence[Mapping[str, Any]], *, calendar: MarketCalendar | None = None) -> list[str]:
    """The conditions that stop deployment and that ``verify`` checks on every run."""
    cal = calendar or MarketCalendar()
    problems: list[str] = []
    signals = {e["signal_key"]: e for e in events if e.get("event") == "PENDING_ENTRY"}
    ledger = {EN.signal_key(r): r for r in ST.ledger()}
    open_now: set[str] = set()
    entered: set[str] = set()
    for e in events:
        kind = e.get("event")
        if kind == "PENDING_ENTRY":
            row = ledger.get(e["signal_key"])
            if row is None or row.get("decision") != FC.APPROVE:
                problems.append(f"{e['ticker']}: an entry signal from a {None if row is None else row.get('decision')} row")
            if (row or {}).get("d5_contract") != PC.required_d5_contract():
                problems.append(f"{e['ticker']}: entry under D5 contract {(row or {}).get('d5_contract')}")
        if kind == "BUY":
            signal = signals.get(e.get("signal_key"))
            if signal is None:
                problems.append(f"{e['ticker']}: a BUY without a pending APPROVE entry")
            else:
                decided = EN._parse(signal["decision_time"])
                opens = cal.regular_market_open(datetime.fromisoformat(e["session"]).date())
                if opens is None or opens <= decided:
                    problems.append(f"{e['ticker']}: filled at or before its decision (future-price leakage)")
            if e.get("signal_key") in entered:
                problems.append(f"{e['ticker']}: the same APPROVE entered twice")
            entered.add(e.get("signal_key"))
            if e["ticker"] in open_now:
                problems.append(f"{e['ticker']}: a second position while one is open (pyramiding)")
            open_now.add(e["ticker"])
            if len(open_now) > PC.max_open_positions():
                problems.append(f"more than {PC.max_open_positions()} open positions at {e['session']}")
        if kind in SELL_EVENTS and e.get("position_status") == EN.CLOSED:
            open_now.discard(e["ticker"])
        if kind == "NAV":
            cash, value, equity = _d(e["cash"]), _d(e["positions_value"]), _d(e["equity"])
            if cash is None or value is None or equity is None or cash + value != equity:
                problems.append(f"NAV arithmetic fails at {e.get('session')}")
            if cash is not None and cash < 0:
                problems.append(f"negative cash at {e.get('session')} (leverage above 1.0)")
    return problems


# -- read model ------------------------------------------------------------------------------------

def events() -> list[dict[str, Any]]:
    return PS.events()


def nav_points() -> list[dict[str, Any]]:
    return [e for e in PS.events() if e.get("event") == "NAV"]


def fills() -> list[dict[str, Any]]:
    return [e for e in PS.events() if e.get("event") in FILL_EVENTS]


def _marks() -> tuple[str | None, dict[str, Decimal]]:
    latest = PR.latest_session()
    if latest is None:
        return None, {}
    return latest, {t: Decimal(str(b.close)) for t, b in PR.bars_of(latest).items()}


def trades() -> list[dict[str, Any]]:
    """One record per position lifecycle in the shared trade-record shape, newest first."""
    lifecycles: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for e in fills():
        lifecycles.setdefault(e["position_id"], []).append(e)
    session, marks = _marks()
    out: list[dict[str, Any]] = []
    for position_id, rows in lifecycles.items():
        buy = rows[0]
        sells = [r for r in rows[1:]]
        qty = _d(buy["quantity"])
        entry = _d(buy["fill_price"])
        remaining = _d(sells[-1]["remaining_shares"]) if sells else qty
        closed = bool(sells) and sells[-1].get("position_status") == EN.CLOSED
        gross = sum((_d(r["leg_gross_pnl"]) for r in sells), Decimal(0))
        charges = _d(buy["commission"]) + _d(buy["fx_cost"]) + sum(
            (_d(r["commission"]) + _d(r["fx_cost"]) for r in sells), Decimal(0))
        costs = _d(buy["total_cost"]) + sum((_d(r["total_cost"]) for r in sells), Decimal(0))
        sold = sum((_d(r["quantity"]) for r in sells), Decimal(0))
        exit_avg = (sum((_d(r["fill_price"]) * _d(r["quantity"]) for r in sells), Decimal(0)) / sold) if sold else None
        mark = marks.get(buy["ticker"])
        unrealized = (mark - entry) * remaining if (mark is not None and not closed) else (Decimal(0) if closed else None)
        out.append({
            "trade_id": f"{position_id}|ENTRY", "position_id": position_id,
            "strategy_id": FC.STRATEGY_H, "strategy_version": EN.VERSION, "book": "H_PAPER",
            "account_id": None, "symbol": buy["ticker"], "ticker": buy["ticker"],
            "cik": buy.get("cik"), "security_id": buy.get("security_id"),
            "session": buy["session"], "status": "CLOSED" if closed else "OPEN",
            "position_status": EN.CLOSED if closed else (EN.PARTIAL if sells else EN.OPEN),
            "signal_at": buy.get("entry_signal_time"), "entry_at": buy["session"],
            "entry_session": buy["session"], "entry_price": buy["fill_price"], "raw_entry_price": buy["raw_price"],
            "exit_at": sells[-1]["session"] if closed else None, "exit_price": _s(exit_avg),
            "qty": buy["quantity"], "initial_shares": buy["quantity"], "current_shares": _s(remaining),
            "initial_notional": buy["notional"],
            "thesis_version": buy.get("thesis_version"), "decision_version": buy.get("signal_key"),
            "tp1": buy.get("tp1"), "tp2": buy.get("tp2"), "bear": buy.get("bear"),
            "bear_rule": buy.get("bear_rule"), "bear_refusal": buy.get("bear_refusal"),
            "valuation_confidence": buy.get("valuation_confidence"),
            "gross_pnl": _s(gross) if closed else None, "fees": None, "slippage": None,
            "costs": _s(costs), "commission": _s(charges),
            "net_pnl": _s(gross - charges) if closed else None,
            "realized_pnl": _s(gross - charges),
            "unrealized_pnl": _s(unrealized), "mark_price": _s(mark), "mark_session": session,
            "mfe": None, "mae": None, "holding_seconds": None,
            "exit_reason": sells[-1]["exit_reason"] if closed else None,
            "exits": [{"action": r["trade_action"], "session": r["session"], "quantity": r["quantity"],
                       "fill_price": r["fill_price"], "reason": r["exit_reason"]} for r in sells],
            "mode": "PAPER", "accounting": ACCOUNTING, "accounting_version": "V1",
            "cost_contract_version": "execution_v0", "evaluation": "H_PAPER_OFFICIAL",
            "provenance": {"source": "h_paper_events", "position_id": position_id,
                           "signal_key": buy.get("signal_key")},
            "missing": ["fees", "slippage", "mfe", "mae", "holding_seconds"],
        })
    out.reverse()
    return out


def positions() -> list[dict[str, Any]]:
    return [t for t in trades() if t["status"] == "OPEN"]


def account() -> dict[str, Any]:
    body = PS.launch()
    if body is None:
        return {"launched": False, "reason": NOT_LAUNCHED}
    initial = Decimal(body["initial_equity"])
    navs = nav_points()
    rows = trades()
    session, marks = _marks()
    cash = _d(navs[-1]["cash"]) if navs else initial
    # The current mark uses the latest stored close; a held ticker without one leaves value unknown.
    open_rows = [t for t in rows if t["status"] == "OPEN"]
    value: Decimal | None = Decimal(0)
    for t in open_rows:
        mark = _d(t["mark_price"])
        if mark is None:
            value = None
            break
        value += mark * _d(t["current_shares"])
    equity = None if value is None else cash + value
    realized = sum((_d(t["realized_pnl"]) for t in rows), Decimal(0))
    unrealized = None if value is None else sum((_d(t["unrealized_pnl"]) or Decimal(0) for t in open_rows), Decimal(0))
    previous = _d(navs[-2]["equity"]) if len(navs) >= 2 else initial
    today = (_d(navs[-1]["equity"]) - previous) if navs else Decimal(0)
    closed = [t for t in rows if t["status"] == "CLOSED"]
    wins = [t for t in closed if _d(t["net_pnl"]) > 0]
    return {"launched": True, "launched_at": body["launched_at"], "currency": "USD",
            "initial_equity": _s(initial), "current_equity": _s(equity), "cash": _s(cash),
            "positions_value": _s(value), "unrealized_pnl": _s(unrealized), "realized_pnl": _s(realized),
            "total_pnl": None if equity is None else _s(equity - initial),
            "today_pnl": _s(today), "last_session": navs[-1]["session"] if navs else None,
            "mark_session": session, "open_positions": len(open_rows),
            "trades": len(closed), "fills": len(fills()),
            "win_rate": _s(Decimal(len(wins)) / len(closed)) if closed else None,
            "win_rate_na": None if closed else "NO_CLOSED_TRADE",
            "pending_entries": [e for e in (PS.status() or {}).get("pending") or []],
            "mode": mode()}


def book() -> Book:
    body = PS.launch()
    if body is None:
        return Book(FC.STRATEGY_H, None, [], [], [], [])
    initial = Decimal(body["initial_equity"])
    navs = nav_points()
    daily, previous = [], initial
    for e in navs:
        equity = Decimal(e["equity"])
        daily.append(DailyPoint(e["session"], equity - previous, equity))
        previous = equity
    return Book(FC.STRATEGY_H, initial, list(reversed(trades())), daily, [e["session"] for e in navs], positions())


def paper_action_by_signal() -> dict[str, str]:
    """Signal key -> what the paper book did with it, for the decision history table."""
    out: dict[str, str] = {}
    for e in PS.events():
        key = e.get("signal_key")
        if not key:
            continue
        kind = e.get("event")
        if kind == "BUY":
            out[key] = "BUY"
        elif kind == "EXIT_PENDING":
            out[key] = "CLOSE"
        elif kind in ("PENDING_ENTRY",):
            out.setdefault(key, "BUY_PENDING")
        elif kind == "APPROVE_WAITING_SLOT":
            out[key] = "WAITING_SLOT"
        elif kind == "SIGNAL_NO_ACTION":
            out[key] = "NONE"
        elif kind.startswith("ENTRY_REJECTED") or kind.startswith("APPROVE_"):
            out[key] = kind
    return out
