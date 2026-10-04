"""Strategy H answered in the shapes the multi-strategy read layer already asks every strategy.

``app/api/strategies.py`` asks six questions of each strategy (status, account, positions, trades,
equity, card) and the frontend is built from the registry rather than from per-strategy branches.
H answers all six so that it appears beside A and E without either of them changing, and answers
them honestly: H has no capital book, so its initial equity, its equity curve and its PnL are
``None`` with a reason, not zeros that would read as a flat, break-even strategy.

H's own questions - the cohort, the decisions, the valuation legs, the forward outcomes - are served
by ``cohort`` through H-only routes. A's and E's records are not read, written or reshaped anywhere
in this package.
"""

from __future__ import annotations

from typing import Any

from app.strategies.h_forward import cohort as CO
from app.strategies.h_forward import contract as C
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import store as ST
from app.strategies.performance import Book

SOURCE = "H_FORWARD_FILES"
MODE = "FORWARD_SHADOW"

#: Why each money field is absent. H observes decisions; it does not hold capital, so these are
#: structural absences rather than "no data yet".
NO_CAPITAL_BOOK = "H는 자본 장부가 없다. forward shadow는 결정을 관찰한다"
NO_POSITION_YET = "APPROVE가 0이고, APPROVE라도 sizing 계약이 동결되기 전에는 포지션을 만들지 않는다"


def observed_sessions() -> list[str]:
    """Sessions the price store holds on or after the launch baseline."""
    state = CO.launch_state()
    baseline = state.get("baseline_session")
    stored = PR.stored_sessions()
    return [s for s in stored if baseline and s >= baseline]


def book() -> Book:
    """H as the shared performance calculator sees it: a book with no trade and no equity series."""
    return Book(C.STRATEGY_H, None, [], [], observed_sessions(), [])


def books() -> dict[str, Book]:
    """The same two-book shape A and E publish. H has no accounting-V0 past, so legacy is empty."""
    return {"official": book(), "legacy": Book(C.STRATEGY_H, None, [], [], [])}


def status() -> dict[str, Any]:
    launch = CO.launch_state()
    cohort = CO.rows()
    counts = CO.counts(cohort)
    latest = PR.latest_session()
    running = launch["status"] == "LAUNCHED"
    return {
        "runtime_status": "FORWARD_SHADOW_RUNNING" if running else "NOT_LAUNCHED",
        "operational_status": "RUNNING" if running else "PAUSED",
        "paper_status": "FORWARD_SHADOW" if running else "NOT_LAUNCHED",
        "evidence_status": None,
        "mode": MODE,
        "session": latest,
        # The card shows this truncated beside A's and E's. A's is None and E's is a decision digest,
        # so H gives the identifier of its newest decision - the thesis version - rather than a
        # timestamp that would render as a chopped ISO string.
        "last_decision": max((r.get("thesis_version") or "" for r in cohort), default="") or None,
        "last_update": max((r.get("decision_time") or "" for r in cohort), default="") or None,
        "detail": {
            "market_data_source": PR.SOURCE,
            "contract": C.state(),
            "launch": launch,
            "decision_counts": counts,
            "watchlist": [r["ticker"] for r in cohort if r["decision"] == C.WATCH],
            "rejected": [r["ticker"] for r in cohort if r["decision"] == C.REJECT],
            "approved": [r["ticker"] for r in cohort if r["decision"] == C.APPROVE],
            "price_sessions_observed": len(observed_sessions()),
            "price_store_latest": latest,
            "evaluation": CO.evaluation(cohort),
        },
    }


def account() -> dict[str, Any]:
    cohort = CO.rows()
    counts = CO.counts(cohort)
    return {
        "currency": "USD",
        "initial_equity": None, "current_equity": None,
        "today_pnl": None, "total_pnl": None,
        "open_positions": 0,
        "closed_trades_today": 0,
        "source": SOURCE, "evidence_status": None,
        "decision_counts": counts,
        "empty_reason": NO_CAPITAL_BOOK,
        "na": {"initial_equity": NO_CAPITAL_BOOK, "current_equity": NO_CAPITAL_BOOK,
               "today_pnl": NO_CAPITAL_BOOK, "total_pnl": NO_CAPITAL_BOOK},
    }


def positions() -> list[dict[str, Any]]:
    """Always empty while no sizing contract exists. A WATCH is not a position."""
    return []


def trades() -> list[dict[str, Any]]:
    """H writes no trade row. Its records are decisions, served by the forward routes."""
    return []


def equity() -> dict[str, Any]:
    return {"strategy_id": C.STRATEGY_H, "currency": "USD", "source": SOURCE,
            "points": [], "baseline": None,
            "note": NO_CAPITAL_BOOK}


def card() -> dict[str, Any]:
    """H's dashboard card: states and counts where A and E show money."""
    cohort = CO.rows()
    counts = CO.counts(cohort)
    launch = CO.launch_state()
    return {
        "operational_status": "RUNNING" if launch["status"] == "LAUNCHED" else "PAUSED",
        "runtime_status": "FORWARD_SHADOW_RUNNING" if launch["status"] == "LAUNCHED" else "NOT_LAUNCHED",
        "evidence_status": None, "currency": "USD",
        "equity": None, "initial_equity": None,
        "open_positions": 0,
        "today_realized_pnl": None, "today_unrealized_pnl": None,
        "official": {"trades": 0, "net_pnl": None, "accounting_versions": []},
        "legacy": {"trades": 0, "net_pnl": None, "accounting_versions": []},
        "net_pnl": None, "trades": 0,
        "paper_clock": None,
        "forward": {
            "contract_id": C.contract_id(), "launch": launch,
            "decision_counts": counts,
            "watchlist": counts[C.WATCH], "rejected": counts[C.REJECT], "approved": counts[C.APPROVE],
            "issuers": len(cohort),
            "maturity": CO.maturity(cohort),
            "evaluation": CO.evaluation(cohort),
            "sizing_contract": C.sizing_contract(),
        },
        "last_signal_at": max((r.get("decision_time") or "" for r in cohort), default="") or None,
        "last_trade_at": None,          # H places no trade; see na.last_trade_at
        "na": {"equity": NO_CAPITAL_BOOK, "initial_equity": NO_CAPITAL_BOOK,
               "today_realized_pnl": NO_CAPITAL_BOOK, "today_unrealized_pnl": NO_CAPITAL_BOOK,
               "net_pnl": NO_CAPITAL_BOOK, "last_trade_at": NO_POSITION_YET},
    }


def forward() -> dict[str, Any]:
    """Everything H-specific, for H's own screen and for the combined performance board."""
    cohort = CO.rows()
    return {
        "strategy_id": C.STRATEGY_H,
        "contract": C.state(),
        "launch": CO.launch_state(),
        "decision_counts": CO.counts(cohort),
        "maturity": CO.maturity(cohort),
        "evaluation": CO.evaluation(cohort),
        "position_rule": {
            "watch_creates_position": C.creates_position(C.WATCH),
            "reject_creates_position": C.creates_position(C.REJECT),
            "approve_creates_position": C.creates_position(C.APPROVE),
            "approve_creates_entry_candidate": C.creates_entry_candidate(C.APPROVE),
            "sizing_contract": C.sizing_contract(),
            "why": C.contract()["position_rule"]["why_not_defined"],
        },
        "price_store": {"source": PR.SOURCE, "latest_session": PR.latest_session(),
                        "sessions_observed": len(observed_sessions())},
        "rows": cohort,
        "ledger_rows": len(ST.ledger()),
        "snapshot_rows": len(ST.snapshots()),
    }


def issuer(ticker: str) -> dict[str, Any] | None:
    """One issuer's full detail: thesis legs, decision history and forward outcomes."""
    row = next((r for r in CO.rows() if r["ticker"] == ticker), None)
    if row is None:
        return None
    snapshot = next((s for s in ST.launch_rows() if s.get("ticker") == ticker), {})
    return row | {
        "thesis": {
            "d3": snapshot.get("d3"), "d4": snapshot.get("d4"),
            "d5": snapshot.get("valuation"), "d6": snapshot.get("d6"),
        },
        "decision_history": ST.history(ticker),
        "launch_snapshot": snapshot,
    }
