"""Step 5 and 6 of H's daily job: sync the candidate evaluation queue, then evaluate it with code.

``queue_step`` merges the planner (``h_forward.research_queue.build``) into the persistent queue.
``evaluation_step`` takes runnable items in contract order and, for each, marks it RUNNING, hands it
to the NO-AI executor and folds the outcome back. There is no authorization file, no budget and no
model: production evaluation is deterministic (``H_V2_AUTONOMOUS_PIPELINE_V2_NO_AI``). Neither step
can stop prices, outcomes, the SEC refresh, the paper book or NAV: the daily runner calls them
inside their own failure domain.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping

from app.strategies.h_auto import clock as CK
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import executor as EX
from app.strategies.h_auto import queue as Q
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import research_queue as RQ


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def next_run_floor(now: datetime) -> str:
    """The start of the next ET calendar day: an infra retry never runs twice in one daily run."""
    local = now.astimezone(CK.ET)
    return _iso(datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=CK.ET))


def queue_step(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    AC.contract()
    plan = RQ.build()
    plan_sha = RQ.write(plan)
    synced = Q.sync(plan, now=_iso(now))
    return {"state": "OK", "plan_sha256": plan_sha, "plan_queued": plan["queued"],
            "intake_state": plan["intake_state"], **synced, "counts": Q.counts()}


def evaluation_step(*, now: datetime | None = None, deps: EX.Deps | None = None,
                    run_id: str | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    AC.contract()
    run_id = run_id or now.strftime("HEVAL-%Y%m%dT%H%M%SZ")
    recovered = Q.recover_interrupted()
    deps = deps or EX.Deps(now=lambda: datetime.now(timezone.utc))
    results: list[dict[str, Any]] = []
    for item in Q.ordered(now=_iso(now)):
        attempts = int(item.get("attempt_count") or 0) + 1
        Q.transition(item, "STARTED", Q.RUNNING, attempt_count=attempts, run_id=run_id,
                     started_at=_iso(datetime.now(timezone.utc)), status_reason=None)
        outcome = EX.execute(item, deps, run_id=run_id)
        status, reason = outcome.status, outcome.reason
        fields = dict(outcome.fields)
        if outcome.retryable or status in (Q.QUEUED, Q.DATA_NOT_READY, Q.METHOD_NOT_READY):
            fields["eligible_after"] = next_run_floor(now)
            if outcome.retryable and attempts >= AC.max_queue_attempts():
                status, reason = Q.FAILED, f"QUEUE_ATTEMPTS_EXHAUSTED:{reason}"
        fields["cost_usd"] = 0.0
        keep = {k: v for k, v in fields.items() if k in Q.FIELDS}
        if status == Q.COMPLETE:
            keep.update({k: v for k, v in EX.VERSIONS.items()})
            keep["completed_at"] = _iso(datetime.now(timezone.utc))
        current = Q.fold()[item["queue_id"]]
        Q.transition(current, "FINISHED", status, status_reason=reason, **keep)
        results.append({"queue_id": item["queue_id"], "ticker": item["ticker"], "status": status, "why": reason,
                        "final_decision": fields.get("final_decision"),
                        "previous_decision": fields.get("previous_decision"),
                        "decision_transition": fields.get("decision_transition"),
                        "ledger_written": fields.get("ledger_written"),
                        "generators": fields.get("generators"),
                        "ai_calls": 0, "ai_calls_blocked": fields.get("ai_calls_blocked") or []})
    return {"state": "OK", "run_id": run_id, "mode": "DETERMINISTIC_NO_AI", "ai_calls": 0,
            "results": results, "recovered": recovered, "counts": Q.counts()}


#: ``usb-h-forward.timer``: ``OnCalendar=Mon..Fri 01:10 America/New_York``. Stated here only to show
#: the next run on screen; the timer unit stays the authority.
TIMER_WEEKDAYS, TIMER_AT = (0, 1, 2, 3, 4), time(1, 10)


def next_scheduled_run(now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone(CK.ET)
    day = local.date()
    for _ in range(8):
        moment = datetime.combine(day, TIMER_AT, tzinfo=CK.ET)
        if day.weekday() in TIMER_WEEKDAYS and moment > local:
            return _iso(moment)
        day += timedelta(days=1)
    raise RuntimeError("no scheduled run within a week")


def runtime_timestamps() -> dict[str, Any]:
    """The H screen's clock: every value is read from runtime files, never from a build time."""
    from app.strategies.h_forward import prices as PR
    from app.strategies.h_forward import sec_refresh as SR
    run = AS.read_json(AS.RUN_STATUS) or {}
    refresh = SR.read_status() or {}
    try:
        from app.strategies.h_auto import sources as SO
        snaps = SO.reference_snapshots()
        latest_universe = None if not snaps else {"as_of": snaps[-1].as_of.isoformat(), "owner": snaps[-1].owner}
    except Exception:                                          # a status read never fails the screen
        latest_universe = None
    return {"latest_universe": latest_universe, "last_auto_run_at": run.get("last_auto_run_finished_at"),
            "latest_price_session": PR.latest_session(),
            "sec_refresh_at": refresh.get("checked_at") or run.get("last_sec_refresh_at"),
            "sec_refresh_state": refresh.get("refresh_run"),
            "research_queue_last_run_at": run.get("last_queue_run_at"),
            "research_last_run_at": run.get("last_research_run_at"),
            "evaluation_last_run_at": run.get("last_evaluation_run_at"),
            "intake_last_run_at": run.get("last_intake_run_at"),
            "next_auto_run_at": next_scheduled_run(),
            "source": "runtime files (auto/run_status.json, refresh_status.json, price store)"}


def latest_evaluation(items: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any] | None:
    """The most recently finished queue item: what the last automatic evaluation concluded."""
    done = [i for i in (Q.fold() if items is None else items).values()
            if i.get("status") in (Q.COMPLETE, Q.FAILED, Q.DATA_NOT_READY, Q.METHOD_NOT_READY) and i.get("run_id")]
    if not done:
        return None
    last = max(done, key=lambda i: str(i.get("updated_at") or ""))
    return {k: last.get(k) for k in ("queue_id", "ticker", "status", "status_reason", "previous_decision",
                                     "final_decision", "decision_transition", "ledger_written", "updated_at")}


def status() -> dict[str, Any]:
    """What the screen shows: queue counts, open items, the last evaluation and the runtime clock."""
    items = Q.fold()
    return {"contract": AC.state(), "mode": "DETERMINISTIC_NO_AI", "ai_calls": 0,
            "counts": Q.counts(items), "items": Q.view(items)[:50],
            "latest_evaluation": latest_evaluation(items), "timestamps": runtime_timestamps()}


def record_run_status(steps: Mapping[str, Any], *, started_at: str, finished_at: str,
                      kind: str = "update") -> None:
    """``run_status.json``: the timestamps the H screen shows (never a build time)."""
    previous = AS.read_json(AS.RUN_STATUS) or {}
    body = dict(previous)
    if kind == "update":
        body.update({"last_auto_run_started_at": started_at, "last_auto_run_finished_at": finished_at,
                     "steps": dict(steps)})
    else:
        body.update({"last_evaluation_unit_started_at": started_at,
                     "last_evaluation_unit_finished_at": finished_at, "evaluation_steps": dict(steps)})
    for key, step in (("last_evaluation_run_at", "evaluation"), ("last_queue_run_at", "queue"),
                      ("last_sec_refresh_at", "sec_refresh"), ("last_intake_run_at", "intake")):
        if (steps.get(step) or {}).get("ran_at"):
            body[key] = steps[step]["ran_at"]
    AS.write_json(AS.RUN_STATUS, body)
