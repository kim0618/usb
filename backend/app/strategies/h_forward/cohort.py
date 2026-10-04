"""The read side of H's forward shadow: the cohort as the stored files state it.

This module assembles what a screen or an API caller asks for out of the launch snapshot, the
forward ledger and H's price store. It computes forward outcomes (``outcomes``) and nothing else: it
never values an issuer, never decides one, and never imports the research pipeline. A figure it
cannot read is ``None`` with a reason beside it, the same rule the A/E read layer keeps.

The valuation fields H carries - method, window, valuation confidence, Bear/TP1/TP2, upside, the
binding clause that held an APPROVE back - have no counterpart in A's or E's ledgers. They are
served here, under H's own keys, rather than being pushed into the shared trade record; the shared
record stays exactly the shape A and E already write.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.market.calendar import MarketCalendar
from app.strategies.h_forward import contract as C
from app.strategies.h_forward import outcomes as OUT
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import store as ST

DECISION_ORDER = {C.APPROVE: 0, C.WATCH: 1, C.REJECT: 2}


def launch_state() -> dict[str, Any]:
    """Whether the shadow is launched, and the one snapshot fact every screen needs."""
    rows = ST.launch_rows()
    if not rows:
        return {"status": "NOT_LAUNCHED", "launched_at": None, "baseline_session": None,
                "decision_session": C.decision_session(), "issuers": 0,
                "reason": "launch snapshot is empty"}
    return {"status": "LAUNCHED",
            "launched_at": min(str(r.get("launch_timestamp") or "") for r in rows) or None,
            "baseline_session": rows[0].get("launch_baseline_session"),
            "decision_session": rows[0].get("decision_session") or C.decision_session(),
            "d5_contract": rows[0].get("d5_contract"),
            "issuers": len(rows),
            "cohort_tags": sorted({str(r.get("cohort_tag")) for r in rows})}


def _bear(row: Mapping[str, Any]) -> tuple[float | None, str | None]:
    """The Bear leg, or why there is none. A refused Bear is never shown as 0."""
    valuation = row.get("valuation") or {}
    bear = valuation.get("bear")
    if bear is None:
        return None, valuation.get("bear_refusal") or C.NOT_AVAILABLE
    return float(bear), None


def levels_of(row: Mapping[str, Any]) -> OUT.Levels:
    valuation = row.get("valuation") or {}
    bear, reason = _bear(row)
    return OUT.Levels(tp1=valuation.get("tp1"), tp2=valuation.get("tp2"), bear=bear,
                      bear_unavailable_reason=reason)


def _distance(level: float | None, price: float | None) -> float | None:
    if level is None or not price:
        return None
    return level / price - 1.0


def rows(*, calendar: MarketCalendar | None = None) -> list[dict[str, Any]]:
    """One row per cohort issuer: its current decision, its live price and its forward outcomes."""
    cal = calendar or MarketCalendar()
    snapshot = {r["ticker"]: r for r in ST.launch_rows()}
    latest = ST.latest_per_ticker()
    bench = PR.series(C.benchmark())
    stored = PR.stored_sessions()
    out: list[dict[str, Any]] = []
    for ticker, snap in snapshot.items():
        current = latest.get(ticker, snap)
        baseline = snap.get("launch_baseline_session")
        series = PR.series(ticker, sessions=stored)
        last_session = max(series) if series else None
        price = series[last_session].close if last_session else None
        valuation = dict(snap.get("valuation") or {})
        bear, bear_reason = _bear(snap)
        decision = current.get("decision") or snap.get("decision")
        forward = (OUT.all_horizons(ticker=ticker, baseline=baseline, levels=levels_of(snap),
                                    security=series, benchmark=bench, calendar=cal)
                   if baseline else {})
        history = ST.history(ticker)
        out.append({
            "strategy_id": C.STRATEGY_H, "ticker": ticker,
            "cik": snap.get("cik"), "security_id": snap.get("security_id"),
            "cohort_tag": snap.get("cohort_tag"),
            "decision": decision,
            "previous_decision": current.get("previous_decision"),
            "decision_at_launch": snap.get("decision"),
            "decision_time": current.get("decision_time"),
            "effective_session": current.get("effective_session"),
            "thesis_version": current.get("thesis_version"),
            "decision_changes": max(0, len(history) - 1),
            "position": None, "open_positions": 0,
            "position_reason": position_reason(decision),
            "d4_expectation_gap": snap.get("d4_expectation_gap"),
            "d4_gap_confidence": snap.get("d4_gap_confidence"),
            "valuation_method": valuation.get("primary_method"),
            "valuation_window": valuation.get("contract_window"),
            "valuation_confidence": valuation.get("confidence"),
            "valuation_status": valuation.get("status"),
            "bear": bear, "bear_na_reason": bear_reason,
            "tp1": valuation.get("tp1"), "tp2": valuation.get("tp2"),
            "tp1_upside_at_decision": valuation.get("upside_to_tp1"),
            "tp2_upside_at_decision": valuation.get("upside_to_tp2"),
            "range_complete": valuation.get("range_complete"),
            "key_binding_clause": snap.get("key_binding_clause"),
            "approve_blockers": snap.get("approve_blockers") or [],
            "reject_fired": snap.get("reject_fired") or [],
            "watch_matched": snap.get("watch_matched") or [],
            "decision_close": snap.get("decision_close"),
            "current_price": price, "current_price_session": last_session,
            "tp1_distance": _distance(valuation.get("tp1"), price),
            "tp2_distance": _distance(valuation.get("tp2"), price),
            "bear_distance": _distance(bear, price),
            "pre_launch_drift": OUT.pre_launch_drift(
                decision_session=snap.get("decision_session") or C.decision_session(),
                decision_close=snap.get("decision_close"), baseline=baseline or "", security=series),
            "forward": forward,
            "checksums": snap.get("checksums") or {},
        })
    out.sort(key=lambda r: (DECISION_ORDER.get(r["decision"] or "", 9), r["ticker"]))
    return out


def position_reason(decision: str | None) -> str:
    if decision in (C.WATCH, C.REJECT):
        return f"{decision}는 포지션을 만들지 않는다 (D7 계약)"
    if decision == C.APPROVE:
        return (C.SIZING_CONTRACT_REQUIRED if not C.sizing_is_defined()
                else "APPROVE 진입 후보")
    return "결정 없음"


def counts(cohort: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """How many issuers sit in each decision state. Zero is a count, not an error."""
    body = cohort if cohort is not None else rows()
    out = {state: 0 for state in C.DECISION_STATES}
    for row in body:
        decision = row.get("decision")
        if decision in out:
            out[decision] += 1
    return out


def maturity(cohort: list[dict[str, Any]] | None = None) -> dict[str, dict[str, int]]:
    """Per horizon: how many issuers have matured, are pending, or have a gap in the window."""
    body = cohort if cohort is not None else rows()
    out: dict[str, dict[str, int]] = {}
    for horizon in C.horizons():
        key = f"{horizon}D"
        states = [(row.get("forward") or {}).get(key, {}).get("state") for row in body]
        out[key] = {
            "matured": sum(1 for s in states if s == OUT.MATURED),
            "pending": sum(1 for s in states if s == OUT.PENDING),
            "incomplete": sum(1 for s in states if s == OUT.INCOMPLETE),
            "no_baseline": sum(1 for s in states if s == OUT.NO_BASELINE),
            "maturity_session": next((r.get("forward", {}).get(key, {}).get("maturity_session")
                                      for r in body if r.get("forward")), None),
        }
    return out


def evaluation(cohort: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """D7's own state. It is not under the A/E paper gate and does not borrow its verdicts.

    No performance verdict is produced until the pre-registered sample is reached, which is the
    whole point of a forward shadow: an early read of eight issuers over a few sessions would be a
    number, not a result.
    """
    body = cohort if cohort is not None else rows()
    matured = maturity(body)
    needed = C.contract()["evaluation"]["sample_needed"]
    reached = {f"{h}D": matured.get(f"{h}D", {}).get("matured", 0)
               >= int(needed.get(f"matured_{h}d_issuers", 10 ** 9)) for h in C.horizons()
               if f"matured_{h}d_issuers" in needed}
    return {"strategy_id": C.STRATEGY_H, "contract_id": C.contract_id(),
            "state": C.RUNNING if ST.launched() else "NOT_LAUNCHED",
            "verdict": C.INCONCLUSIVE if not all(reached.values()) else "SAMPLE_REACHED",
            "reasons": ([] if all(reached.values()) else ["SAMPLE_NOT_REACHED"]),
            "sample_needed": needed, "sample_reached": reached,
            "maturity": matured,
            "under_ae_paper_gate": False,
            "note": "H는 A/E paper gate 대상이 아니다. 21D·63D 표본이 찰 때까지 INCONCLUSIVE가 정상이다"}
