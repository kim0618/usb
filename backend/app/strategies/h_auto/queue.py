"""H's candidate evaluation queue (V1's unified research queue, same data structure): one persistent
list for REFRESH_DUE and NEW_CANDIDATE items, evaluated with code only since V2 (NO-AI).

``h_forward.research_queue.build`` is the planner: it reads the refresh scan and the intake file and
says, with the evidence check applied, what deserves research today. This module is the queue's
memory. It keeps every item's life as append-only events in ``<store>/auto/queue_events.jsonl``;
the queue at any moment is the fold of those events, so a crash can lose at most a torn final line
and never rewrites an item's history.

Item identity is ``<reason>:<ticker>:<seq>``. A ticker has at most one open item per reason; a new
filing that arrives while one is open is added to it. A terminal item (COMPLETE, FAILED,
EVIDENCE_ALREADY_IN_THESIS) is never reopened - a later filing the thesis has not read gets a new
item with the next sequence number, and the evidence check (``h_auto.dedup``) is what stops a
completed re-evaluation from being queued again for the evidence it already read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.strategies.h_auto import store as AS
from app.strategies.h_forward import research_queue as RQ

QUEUED, RUNNING, COMPLETE, FAILED = "QUEUED", "RUNNING", "COMPLETE", "FAILED"
BUDGET_DEFERRED, DATA_NOT_READY = "BUDGET_DEFERRED", "DATA_NOT_READY"
METHOD_NOT_READY = "METHOD_NOT_READY"
EVIDENCE_ALREADY_IN_THESIS = RQ.EVIDENCE_ALREADY_IN_THESIS
#: ``BUDGET_DEFERRED`` is V1 legacy (AI budget): kept so V1 events still fold and a deferred item is
#: still runnable, never produced by V2.
STATUSES = (QUEUED, RUNNING, COMPLETE, FAILED, BUDGET_DEFERRED, DATA_NOT_READY, METHOD_NOT_READY,
            EVIDENCE_ALREADY_IN_THESIS)
TERMINAL = frozenset({COMPLETE, FAILED, EVIDENCE_ALREADY_IN_THESIS})
RUNNABLE = frozenset({QUEUED, BUDGET_DEFERRED})

#: Fields an event may set on an item. Anything else in an event is ignored by the fold.
FIELDS = ("queue_id", "ticker", "cik", "reason", "priority_class", "priority_rank", "source_event",
          "source_accessions", "queued_at", "eligible_after", "status", "status_reason", "attempt_count",
          "cost_usd", "d2_version", "d3_version", "d4_version", "d5_version", "d6_version",
          "previous_decision", "final_decision", "decision_transition", "thesis_version",
          "previous_thesis_version", "e3_priority", "intake_order", "last_error", "run_id",
          "started_at", "completed_at", "artifacts", "ledger_written", "method_judgement",
          "valuation", "d6", "generators", "ai_calls_blocked")


class QueueIntegrityError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def events() -> list[dict[str, Any]]:
    return AS.read_jsonl(AS.QUEUE_EVENTS)


def fold(rows: Iterable[Mapping[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """queue_id -> current item. Raises on an event that would break the queue's own rules."""
    items: dict[str, dict[str, Any]] = {}
    for event in (events() if rows is None else rows):
        qid = event.get("queue_id")
        status = event.get("status")
        if not qid or status not in STATUSES:
            raise QueueIntegrityError(f"malformed queue event: {event}")
        item = items.get(qid)
        if item is None:
            if event.get("event") != "CREATED":
                raise QueueIntegrityError(f"{qid}: first event is not CREATED")
            item = {"attempt_count": 0, "cost_usd": 0.0, "history": []}
            items[qid] = item
        elif item["status"] in TERMINAL:
            raise QueueIntegrityError(f"{qid}: event after terminal status {item['status']}")
        for key in FIELDS:
            if key in event:
                item[key] = event[key]
        item["history"].append({"at": event.get("at"), "event": event.get("event"), "status": status,
                                "status_reason": event.get("status_reason")})
        item["updated_at"] = event.get("at")
    return items


def _append(event: Mapping[str, Any]) -> dict[str, Any]:
    row = {"at": _now(), **event}
    AS.append_jsonl(AS.QUEUE_EVENTS, [row])
    return row


def transition(item: Mapping[str, Any], event: str, status: str, **fields: Any) -> dict[str, Any]:
    if item.get("status") in TERMINAL:
        raise QueueIntegrityError(f"{item['queue_id']} is terminal ({item['status']})")
    return _append({"queue_id": item["queue_id"], "event": event, "status": status, **fields})


def _accessions(plan_row: Mapping[str, Any]) -> list[str]:
    return sorted({str(f.get("accession")) for f in plan_row.get("source_event") or [] if f.get("accession")})


def _plan_status(plan_row: Mapping[str, Any]) -> tuple[str, str | None]:
    state = plan_row.get("research_status")
    if state == RQ.QUEUED:
        return QUEUED, None
    if state == RQ.EVIDENCE_ALREADY_IN_THESIS:
        return EVIDENCE_ALREADY_IN_THESIS, "ALL_NEW_FILINGS_ALREADY_READ_BY_THESIS"
    return DATA_NOT_READY, str(state)


def sync(plan: Mapping[str, Any], *, now: str | None = None) -> dict[str, Any]:
    """Merge today's planner rows into the queue. Never removes, never reopens a terminal item."""
    now = now or _now()
    items = fold()
    created, updated = [], []
    for row in plan.get("rows") or []:
        ticker, reason = row["ticker"], row["reason"]
        mine = sorted((i for i in items.values() if i["ticker"] == ticker and i["reason"] == reason),
                      key=lambda i: int(i["queue_id"].rsplit(":", 1)[1]))
        open_items = [i for i in mine if i["status"] not in TERMINAL]
        status, why = _plan_status(row)
        accessions = _accessions(row)
        if open_items:
            item = open_items[-1]
            if item["status"] == RUNNING:
                continue
            changed = {}
            if set(accessions) - set(item.get("source_accessions") or []):
                changed["source_accessions"] = sorted(set(accessions) | set(item.get("source_accessions") or []))
                changed["source_event"] = row.get("source_event")
            if item["status"] in (DATA_NOT_READY, METHOD_NOT_READY) and status == QUEUED:
                changed["status"] = QUEUED
            if changed:
                updated.append(transition(item, "UPDATED", changed.pop("status", item["status"]),
                                          status_reason=why if status != QUEUED else None, **changed))
            continue
        if reason == RQ.NEW_CANDIDATE and mine:
            continue                       # a candidate is admitted once; later it is a cohort refresh
        if mine and set(accessions) <= set().union(*(set(i.get("source_accessions") or []) for i in mine)):
            continue                       # nothing a previous item has not already handled
        seq = len(mine) + 1
        event = {"queue_id": f"{reason}:{ticker}:{seq}", "event": "CREATED", "status": status,
                 "status_reason": why, "ticker": ticker, "cik": row.get("cik"), "reason": reason,
                 "priority_class": row.get("priority_class"), "priority_rank": row.get("priority_rank"),
                 "source_event": row.get("source_event"), "source_accessions": accessions,
                 "queued_at": now, "eligible_after": now, "attempt_count": 0, "cost_usd": 0.0,
                 "previous_thesis_version": row.get("thesis_version"),
                 "e3_priority": row.get("e3_priority"), "intake_order": row.get("intake_order")}
        created.append(_append(event))
        items = fold()
    return {"created": [e["queue_id"] for e in created], "updated": [e["queue_id"] for e in updated],
            "open": sorted(q for q, i in fold().items() if i["status"] not in TERMINAL)}


def ordered(items: Mapping[str, Mapping[str, Any]] | None = None, *, now: str | None = None) -> list[dict[str, Any]]:
    """Runnable items in research order: class, then first filing / intake order, then ticker.

    An item whose ``eligible_after`` is later than ``now`` (an infra retry waits for the next daily
    run) is left out. No price, return or forward outcome takes part in the order.
    """
    items = fold() if items is None else items
    now = now or _now()

    def first_filing(item: Mapping[str, Any]) -> str:
        dates = [str(f.get("filingDate")) for f in item.get("source_event") or [] if f.get("filingDate")]
        return min(dates) if dates else "9999"

    runnable = [dict(i) for i in items.values()
                if i["status"] in RUNNABLE and str(i.get("eligible_after") or "") <= now]
    runnable.sort(key=lambda i: (RQ.CLASS_ORDER.get(str(i.get("priority_class")), 9), first_filing(i),
                                 i.get("intake_order") or 0, i["ticker"], i["queue_id"]))
    return runnable


def recover_interrupted(*, reason: str = "INTERRUPTED") -> list[str]:
    """Close items a crashed run left RUNNING (retryable until the attempt cap)."""
    out = []
    for item in fold().values():
        if item["status"] == RUNNING:
            transition(item, "INTERRUPTED", FAILED if item.get("attempt_count", 0) >= _max_attempts() else QUEUED,
                       status_reason=f"{reason}:RUN_INTERRUPTED", last_error=reason)
            out.append(item["queue_id"])
    return out


def _max_attempts() -> int:
    from app.strategies.h_auto import contract as AC
    return AC.max_queue_attempts()


def counts(items: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, int]:
    items = fold() if items is None else items
    out = {s: 0 for s in STATUSES}
    for item in items.values():
        out[item["status"]] += 1
    return out


def view(items: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every item, newest first, without the event history (for the API and the UI)."""
    items = fold() if items is None else items
    rows = [{k: v for k, v in i.items() if k != "history"} for i in items.values()]
    rows.sort(key=lambda i: (i.get("updated_at") or ""), reverse=True)
    return rows


def ids_with(status: str, items: Mapping[str, Mapping[str, Any]] | None = None) -> Sequence[str]:
    items = fold() if items is None else items
    return sorted(q for q, i in items.items() if i["status"] == status)
