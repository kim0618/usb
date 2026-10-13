"""Strategy H answered in the shapes the multi-strategy read layer already asks every strategy.

``app/api/strategies.py`` asks six questions of each strategy (status, account, positions, trades,
equity, card) and the frontend is built from the registry rather than from per-strategy branches.
H answers all six so that it appears beside A and E without either of them changing, and answers
them honestly. Before the H paper launch (``app.strategies.h_paper``) H has no capital book, so its
initial equity, its equity curve and its PnL are ``None`` with a reason, not zeros that would read as
a flat, break-even strategy. After the launch the same six answers come from H's own paper book;
the forward shadow below is unchanged and is never mixed into the paper money.

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
from app.strategies.h_paper import service as HP
from app.strategies.performance import Book

SOURCE = "H_FORWARD_FILES"
MODE = "FORWARD_SHADOW"
PAPER_SOURCE = "H_PAPER_EVENTS"

#: Why each money field is absent. H observes decisions; it does not hold capital, so these are
#: structural absences rather than "no data yet".
NO_CAPITAL_BOOK = "H는 자본 장부가 없다. forward shadow는 결정을 관찰한다"
NO_POSITION_YET = "APPROVE가 0이고, APPROVE라도 sizing 계약이 동결되기 전에는 포지션을 만들지 않는다"
NO_PAPER_LAUNCH = "H paper 계좌가 아직 시작되지 않았다(H_PAPER_V1_LAUNCH 없음). 시작 전 결정은 SHADOW_ONLY"
NO_TRADE_YET = "H paper 시작 이후 APPROVE가 없어 체결이 없다"


def observed_sessions() -> list[str]:
    """Sessions the price store holds on or after the launch baseline."""
    state = CO.launch_state()
    baseline = state.get("baseline_session")
    stored = PR.stored_sessions()
    return [s for s in stored if baseline and s >= baseline]


def book() -> Book:
    """H as the shared performance calculator sees it: its paper book once launched, else empty."""
    if HP.launched():
        return HP.book()
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
        "paper_status": (HP.mode() if HP.launched() else "FORWARD_SHADOW") if running else "NOT_LAUNCHED",
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
            "paper": paper_state(),
        },
    }


def paper_state() -> dict[str, Any]:
    """H's paper clock and contract, beside (never inside) the forward shadow's."""
    from app.strategies.h_paper import contract as PC
    from app.strategies.h_paper import store as PS
    launch = PS.launch()
    return {"mode": HP.mode(), "launched": launch is not None,
            "launched_at": None if launch is None else launch.get("launched_at"),
            "contract": PC.state(), "last_run": PS.status()}


def account() -> dict[str, Any]:
    cohort = CO.rows()
    counts = CO.counts(cohort)
    if HP.launched():
        paper = HP.account()
        return paper | {"source": PAPER_SOURCE, "evidence_status": None, "decision_counts": counts,
                        "closed_trades_today": sum(1 for t in HP.trades() if t["status"] == "CLOSED"
                                                   and t.get("exit_at") == paper.get("last_session")),
                        "empty_reason": None if paper["fills"] else NO_TRADE_YET,
                        "na": {k: "보유 종목 종가 없음" for k in ("current_equity", "total_pnl")
                               if paper.get(k) is None}}
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
    """H's open paper positions. A WATCH or REJECT is never a position."""
    return HP.positions() if HP.launched() else []


def trades() -> list[dict[str, Any]]:
    """H's paper position lifecycles in the shared trade-record shape (empty before the launch)."""
    return HP.trades() if HP.launched() else []


def fills() -> list[dict[str, Any]]:
    """Every paper execution (BUY, SELL_TP1, ...), newest first."""
    return list(reversed(HP.fills())) if HP.launched() else []


def equity() -> dict[str, Any]:
    if HP.launched():
        launch = HP.account()
        return {"strategy_id": C.STRATEGY_H, "currency": "USD", "source": PAPER_SOURCE,
                "baseline": launch["initial_equity"],
                "points": [{"session": e["session"], "equity": e["equity"], "cash": e["cash"],
                            "positions_value": e["positions_value"]} for e in HP.nav_points()],
                "note": "H paper NAV (종가 평가). forward shadow 성과와는 별개다"}
    return {"strategy_id": C.STRATEGY_H, "currency": "USD", "source": SOURCE,
            "points": [], "baseline": None,
            "note": NO_CAPITAL_BOOK}


def card() -> dict[str, Any]:
    """H's dashboard card: its paper money once launched, states and counts always."""
    cohort = CO.rows()
    counts = CO.counts(cohort)
    launch = CO.launch_state()
    body = _shadow_card(cohort, counts, launch)
    if not HP.launched():
        body["na"]["paper"] = NO_PAPER_LAUNCH
        return body
    paper = HP.account()
    rows = HP.trades()
    closed = [t for t in rows if t["status"] == "CLOSED"]
    summary = {"trades": len(closed), "net_pnl": paper["total_pnl"], "accounting_versions": ["V1"] if rows else []}
    na = {k: v for k, v in body["na"].items() if k not in (
        "equity", "initial_equity", "today_realized_pnl", "today_unrealized_pnl", "net_pnl", "last_trade_at")}
    if not HP.fills():
        na["last_trade_at"] = NO_TRADE_YET
    return body | {
        "equity": paper["current_equity"], "initial_equity": paper["initial_equity"],
        "cash": paper["cash"], "open_positions": paper["open_positions"],
        "today_realized_pnl": paper["today_pnl"], "today_unrealized_pnl": paper["unrealized_pnl"],
        "realized_pnl": paper["realized_pnl"], "unrealized_pnl": paper["unrealized_pnl"],
        "official": summary, "net_pnl": paper["total_pnl"], "trades": len(closed),
        "fills": paper["fills"], "win_rate": paper["win_rate"],
        "paper": {"mode": HP.mode(), "launched_at": paper["launched_at"], "last_session": paper["last_session"],
                  "pending_entries": paper["pending_entries"]},
        "last_trade_at": max((t.get("exit_at") or t.get("entry_at") or "" for t in rows), default="") or None,
        "na": na,
    }


def _shadow_card(cohort: list[dict[str, Any]], counts: dict[str, int], launch: dict[str, Any]) -> dict[str, Any]:
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
            "paper_mode": HP.mode(),
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
            "paper": paper_state(),
            "paper_rule": "APPROVE(paper 시작 이후) → PENDING_ENTRY → 다음 정규장 시가 BUY. WATCH·REJECT는 진입하지 않는다",
        },
        "paper_actions": HP.paper_action_by_signal() if HP.launched() else {},
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
