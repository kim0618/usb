"""The one writer of a re-evaluated decision into H's forward ledger.

The forward shadow contract says a decision moves only through a D3->D4->D5->D6 re-evaluation on new
material evidence, never on price, and that thesis versions are immutable. Until now nothing could
write such a row: the ledger held only the launch state. This module is that writer, and it refuses
everything the contract refuses:

* a transition not in ``transitions.allowed`` (so ``REJECT->REJECT`` and ``APPROVE->APPROVE`` are
  not ledger rows: an unchanged re-evaluation of those is recorded in the research queue instead);
* a cause that is missing, ``PRICE``, or not one of ``CAUSES``;
* a D5 valuation under any contract other than ``D5_D2R_V1``;
* a thesis version the issuer already carries, or a decision time not after its newest row.

A row it accepts is the signal the paper book reads: an APPROVE after the paper launch becomes a
pending entry, an APPROVE->WATCH/REJECT closes a position, and cause ``THESIS_INVALIDATED`` closes
one regardless of the new decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.strategies.h_forward import contract as C
from app.strategies.h_forward import store as ST

MATERIAL_REFRESH, THESIS_INVALIDATED = "MATERIAL_REFRESH", "THESIS_INVALIDATED"
CAUSES = (MATERIAL_REFRESH, THESIS_INVALIDATED)


class TransitionRefused(ValueError):
    pass


def _parse(text: str) -> datetime:
    moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise TransitionRefused("decision_time must be timezone-aware")
    return moment


def check(ticker: str, *, decision: str, cause: str, thesis_version: str, decision_time: str,
          d5_contract: str) -> dict[str, Any]:
    """What the row would be relative to the issuer's current state, or raise ``TransitionRefused``."""
    current = ST.latest_per_ticker().get(ticker)
    if current is None:
        raise TransitionRefused(f"{ticker} is not in H's forward ledger")
    if cause not in CAUSES:
        raise TransitionRefused(f"cause {cause!r} is not an evidential re-evaluation cause")
    if d5_contract != C.required_d5_contract():
        raise TransitionRefused(f"D5 contract {d5_contract!r} is refused; {C.required_d5_contract()} is required")
    if decision not in C.DECISION_STATES:
        raise TransitionRefused(f"decision {decision!r} is not a decision state")
    previous = current.get("decision")
    if (previous, decision) not in C.allowed_transitions():
        raise TransitionRefused(f"{previous}->{decision} is not an allowed transition (D7 contract)")
    if any(r.get("thesis_version") == thesis_version for r in ST.history(ticker)):
        raise TransitionRefused(f"{ticker} already carries thesis version {thesis_version}")
    if _parse(decision_time) <= _parse(current["decision_time"]):
        raise TransitionRefused("a re-evaluation must be decided after the issuer's newest ledger row")
    return {"previous_decision": previous, "transition": f"{previous}->{decision}"}


def record(ticker: str, *, decision: str, cause: str, thesis_version: str, decision_time: str,
           effective_session: str, d5_contract: str, valuation: Mapping[str, Any],
           checksums: Mapping[str, str], evidence: Mapping[str, Any], key_binding_clause: str | None = None,
           invalidation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Append one re-evaluated decision. Returns the row written."""
    head = check(ticker, decision=decision, cause=cause, thesis_version=thesis_version,
                 decision_time=decision_time, d5_contract=d5_contract)
    if cause == THESIS_INVALIDATED and not (invalidation or {}).get("sources"):
        raise TransitionRefused("a THESIS_INVALIDATED row cites the evidence that broke the thesis")
    row = {
        "strategy_id": C.STRATEGY_H, "ticker": ticker, "thesis_version": thesis_version,
        "decision_time": decision_time, "record": "REEVALUATION",
        "cohort_tag": (ST.latest_per_ticker()[ticker]).get("cohort_tag"),
        "decision": decision, **head, "cause": cause,
        "effective_session": effective_session,
        "tp1": valuation.get("tp1"), "tp2": valuation.get("tp2"), "bear": valuation.get("bear"),
        "bear_refusal": valuation.get("bear_refusal"),
        "valuation_confidence": valuation.get("confidence"),
        "valuation_method": valuation.get("primary_method"),
        "valuation_window": valuation.get("contract_window"),
        "key_binding_clause": key_binding_clause,
        "d3_checksum": checksums.get("d3_output"), "d4_checksum": checksums.get("d4_output"),
        "d5_checksum": checksums.get("d5_row"), "d6_checksum": checksums.get("d6_decision"),
        "d5_contract": d5_contract,
        "evidence": dict(evidence),
        "invalidation": dict(invalidation) if invalidation else None,
        "position": None,
    }
    ST.append_ledger([row])
    return row
