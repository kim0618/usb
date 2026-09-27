"""One trade-record shape for every operating strategy, read from each strategy's own ledger.

A and E keep physically separate books: A writes ``simulation_trades`` in the paper database under
its own account, E writes its engine session files under ``paper_state/<evidence>/<strategy id>``.
Neither can see the other's rows, so ownership of a symbol held by both is never ambiguous: an A
row carries A's account id, an E row carries E's evidence book. This module only reads and
normalises; it never writes, migrates or relabels a stored record.

Fields a ledger does not record are ``None`` and named in ``missing``, never filled with a guess:

* MFE / MAE: neither ledger stores the intratrade path, so both are ``None`` for A and E;
* fees vs slippage: both ledgers store one ``total_cost`` per fill (spread + slippage embedded in
  the fill price, plus commission and FX), not the split, so ``fees`` and ``slippage`` are ``None``
  and ``costs`` carries the recorded total;
* A's signal time and selection run: ``simulation_trades`` has no link back to the scanner run.

The accounting convention each row was written under is detected, not assumed. Before the PnL
accounting contract (``app.broker.accounting``), SimBroker wrote ``net = gross - total_cost``,
which charges the price-embedded spread and slippage a second time. Rows written that way are
labelled ``TOTAL_COST_DEDUCTED_V0`` (``accounting_version`` V0) so no screen can present them as
cash-true. A V0 row keeps its ``recorded_net_pnl`` exactly as stored; where the V1 figure can be
derived it is published beside it as ``recomputed_v1_net_pnl``, never in its place.

``evaluation`` says where a row counts (``app.strategies.official``): OFFICIAL (V1, on or after the
official start), PRE_OFFICIAL (V1, before it) or LEGACY (V0).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.execution.config import ExecutionConfig
from app.strategies import official as OFF

ET = ZoneInfo("America/New_York")
TOLERANCE = Decimal("0.000001")

ACCOUNTING_V0 = "TOTAL_COST_DEDUCTED_V0"        # net = gross - total_cost (spread/slippage twice)
ACCOUNTING_V1 = "CASH_CHARGES_ONLY_V1"          # net = gross - commission - fx (accounting contract)
ACCOUNTING_UNKNOWN = "UNRECONCILED"
VERSION_OF = {ACCOUNTING_V0: "V0", ACCOUNTING_V1: "V1"}

#: A's share of ``total_cost`` that is a cash charge under ``execution_v0`` (commission + FX over all
#: four bps). Every A paper row was written under this config, so a V0 row's V1 net is derivable.
_A_EXEC = ExecutionConfig()
A_CASH_SHARE = ((_A_EXEC.commission_bps + _A_EXEC.fx_cost_bps)
                / (_A_EXEC.default_spread_bps + _A_EXEC.default_slippage_bps + _A_EXEC.commission_bps
                   + _A_EXEC.fx_cost_bps))

A_MISSING = ("signal_at", "fees", "slippage", "mfe", "mae", "selection_run_id")
E_MISSING = ("fees", "slippage", "mfe", "mae")


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _holding(entry_at: Any, exit_at: Any) -> int | None:
    start, end = _parse(entry_at), _parse(exit_at)
    if start is None or end is None:
        return None
    return int((end - start).total_seconds())


def _session_of(at: Any) -> str | None:
    moment = _parse(at)
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.date().isoformat()
    return moment.astimezone(ET).date().isoformat()


def detect_accounting(gross: Decimal | None, net: Decimal | None, costs: Decimal | None) -> str:
    if gross is None or net is None or costs is None:
        return ACCOUNTING_UNKNOWN
    if abs(gross - costs - net) <= TOLERANCE:
        return ACCOUNTING_V0
    if net > gross - costs:
        return ACCOUNTING_V1
    return ACCOUNTING_UNKNOWN


# -- Strategy A: simulation_trades ---------------------------------------------------------------

def a_trade_record(row: Any, *, strategy_id: str, strategy_version: str, account_id: int | None) -> dict[str, Any]:
    """One ``SimulationTradeRecord`` (or any object with its attributes) as a canonical record."""
    gross, net, costs = _dec(row.gross_pnl), _dec(row.net_pnl), _dec(row.total_cost)
    closed = row.status == "CLOSED"
    accounting = detect_accounting(gross, net, costs) if closed else None
    version = VERSION_OF.get(accounting or "")
    session = _session_of(row.entry_time)
    recomputed = (gross - costs * A_CASH_SHARE) if (version == "V0" and gross is not None and costs is not None) else None
    return {
        "trade_id": row.trade_uid, "strategy_id": strategy_id, "strategy_version": strategy_version,
        "book": "PAPER_DB", "account_id": account_id, "symbol": row.symbol,
        "session": session, "status": row.status,
        "signal_at": None,
        "entry_at": _iso(row.entry_time), "entry_price": _s(_dec(row.average_entry_price)),
        "exit_at": _iso(row.exit_time), "exit_price": _s(_dec(row.average_exit_price)),
        "qty": _s(_dec(row.total_quantity)),
        "gross_pnl": _s(gross) if closed else None, "fees": None, "slippage": None,
        "costs": _s(costs), "net_pnl": _s(net) if closed else None,
        "mfe": None, "mae": None,
        "holding_seconds": _holding(row.entry_time, row.exit_time),
        "exit_reason": row.exit_reason, "mode": "PAPER",
        "accounting": accounting, "accounting_version": version,
        "recorded_net_pnl": _s(net) if closed else None,
        "recomputed_v1_net_pnl": _s(recomputed),
        "cost_contract_version": _A_EXEC.version,
        "evaluation": OFF.classify(session, version) if closed else None,
        "provenance": {"source": "simulation_trades", "account_id": account_id, "trade_uid": row.trade_uid,
                       "selection_run_id": None},
        "missing": list(A_MISSING),
    }


# -- Strategy E: engine session files -------------------------------------------------------------

def e_trade_records(state: Mapping[str, Any], *, strategy_id: str, strategy_version: str, book: str,
                    book_session: Mapping[str, Any] | None) -> dict[str, Any]:
    """Every filled entry of one E engine session, plus how the rows reconcile with the book.

    Per-trade gross is the fill-price difference; ``costs`` is the recorded entry + exit
    ``fill_cost``. A per-trade net is published only when the rows reproduce the book's own
    ``sim_realized_pnl`` for the session under a recognised convention; otherwise the session total
    stays the authority and the per-trade net is ``None``.
    """
    session = state.get("session")
    v1 = state.get("accounting_version") == "V1"
    decision = state.get("decision") or {}
    digest = decision.get("digest")
    selected = list(decision.get("selected") or [])
    exits = state.get("exits") or {}
    rows: list[dict[str, Any]] = []
    not_filled: list[dict[str, Any]] = []
    for symbol, entry in (state.get("entries") or {}).items():
        if entry.get("status") != "FILLED":
            not_filled.append({"symbol": symbol, "status": entry.get("status"),
                               "reason": entry.get("reason") or entry.get("rejection")})
            continue
        exit_row = exits.get(symbol) or {}
        exited = exit_row.get("status") == "FILLED"
        qty = _dec((entry.get("intent") or {}).get("quantity"))
        entry_price, exit_price = _dec(entry.get("fill_price")), _dec(exit_row.get("fill_price"))
        gross = qty * (exit_price - entry_price) if (exited and qty is not None and entry_price is not None
                                                       and exit_price is not None) else None
        entry_cost, exit_cost = _dec(entry.get("fill_cost")), _dec(exit_row.get("fill_cost"))
        if exited:
            costs = None if entry_cost is None or exit_cost is None else entry_cost + exit_cost
        else:
            costs = entry_cost
        # V0 rows: the V1 figure is the fill-price gross less only the cash part of each leg's cost
        # (fill_cost minus the price-embedded part, |fill - raw| x qty).
        recomputed = None
        if exited and not v1 and gross is not None and costs is not None and qty is not None:
            embedded = sum((abs(_dec(leg.get("fill_price")) - _dec(leg.get("raw_market_price"))) * qty
                            for leg in (entry, exit_row)
                            if leg.get("fill_price") and leg.get("raw_market_price")), Decimal(0))
            recomputed = gross - (costs - embedded)
        rows.append({
            "trade_id": entry.get("key") or f"{strategy_id}|{session}|{symbol}|ENTRY",
            "strategy_id": strategy_id, "strategy_version": strategy_version, "book": book,
            "account_id": None, "symbol": symbol, "session": session,
            "status": "CLOSED" if exited else "OPEN",
            "signal_at": entry.get("signal_at"),
            "entry_at": entry.get("fill_at"), "entry_price": _s(entry_price),
            "exit_at": exit_row.get("fill_at"), "exit_price": _s(exit_price),
            "qty": _s(qty), "gross_pnl": _s(gross), "fees": None, "slippage": None,
            "costs": _s(costs), "net_pnl": None,
            "commission": _s(_sum_present(entry.get("commission"), exit_row.get("commission"))),
            "recorded_net_pnl": None, "recomputed_v1_net_pnl": _s(recomputed),
            "accounting_version": "V1" if v1 else "V0",
            "cost_contract_version": state.get("cost_contract_version") or "execution_v0",
            "execution_cost_bp": state.get("execution_cost_bp"),
            "evaluation": OFF.classify(session, "V1" if v1 else "V0"),
            "mfe": None, "mae": None,
            "holding_seconds": _holding(entry.get("fill_at"), exit_row.get("fill_at")),
            "exit_reason": exit_row.get("exit_flag"), "mode": "PAPER", "accounting": None,
            "provenance": {"source": "engine_session", "decision_digest": digest,
                           "h5_signal_id": None if digest is None else f"{digest[:16]}:{symbol}",
                           "selection_rank": selected.index(symbol) + 1 if symbol in selected else None,
                           "weight": entry.get("weight"), "evidence_status": book},
            "missing": list(E_MISSING),
        })
    reconciliation = _reconcile_e(rows, book_session, v1=v1)
    for row in rows:
        if row["status"] == "CLOSED" and reconciliation["status"] == "RECONCILED":
            gross = _dec(row["gross_pnl"])
            charged = _dec(row["commission"]) if v1 else _dec(row["costs"])
            if gross is not None and charged is not None:
                row["net_pnl"] = row["recorded_net_pnl"] = str(gross - charged)
                row["accounting"] = reconciliation["accounting"]
    return {"session": session, "book": book, "trades": rows, "not_filled": not_filled,
            "reconciliation": reconciliation}


def _sum_present(*values: Any) -> Decimal | None:
    present = [_dec(v) for v in values if v is not None]
    return sum(present, Decimal(0)) if present else None


def _reconcile_e(rows: Iterable[Mapping[str, Any]], book_session: Mapping[str, Any] | None,
                 *, v1: bool = False) -> dict[str, Any]:
    """Do the per-trade rows reproduce the book's own session figure?

    V1: sum(gross - commission) must equal ``sim_realized_pnl`` (commission is the only charge).
    V0: sum(gross - total_cost) must, which is how the old broker wrote it."""
    closed = [r for r in rows if r["status"] == "CLOSED"]
    recorded = _dec((book_session or {}).get("sim_realized_pnl"))
    if recorded is None:
        return {"status": "NO_BOOK_ENTRY", "accounting": None, "book_realized": None, "derived_v0": None}
    gross = [_dec(r["gross_pnl"]) for r in closed]
    costs = [_dec(r["commission"] if v1 else r["costs"]) for r in closed]
    if any(v is None for v in gross + costs):
        return {"status": "INCOMPLETE_FILLS", "accounting": None, "book_realized": str(recorded), "derived_v0": None}
    derived = sum(gross, Decimal(0)) - sum(costs, Decimal(0))
    if abs(derived - recorded) <= TOLERANCE * max(1, len(closed)):
        return {"status": "RECONCILED", "accounting": ACCOUNTING_V1 if v1 else ACCOUNTING_V0,
                "book_realized": str(recorded), "derived": str(derived),
                "derived_v0": None if v1 else str(derived)}
    return {"status": "UNRECONCILED", "accounting": ACCOUNTING_UNKNOWN, "book_realized": str(recorded),
            "derived_v0": str(derived)}
