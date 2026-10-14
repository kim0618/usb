"""Step 5 and 6 of H's daily job: sync the unified queue, then run budgeted research on it.

``queue_step`` merges the planner (``h_forward.research_queue.build``) into the persistent queue.
``research_step`` takes runnable items in contract order and, for each, checks the budget *before*
touching SEC (a deferred item costs nothing), marks it RUNNING, hands it to the executor and folds
the outcome back. Neither step can stop prices, outcomes, the SEC refresh, the paper book or NAV:
the daily runner calls them inside their own failure domain.

With no ``authorization.json`` the research step makes no model call and changes no item; it says
``RESEARCH_NOT_AUTHORIZED``. That is the state in which this code is deployed.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping

from app.strategies.h_auto import budget as BU
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import executor as EX
from app.strategies.h_auto import queue as Q
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import research_queue as RQ


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def next_run_floor(now: datetime) -> str:
    """The start of the next ET calendar day: an infra retry never runs twice in one daily run."""
    local = now.astimezone(BU.ET)
    return _iso(datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=BU.ET))


def queue_step(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    AC.contract()
    plan = RQ.build()
    plan_sha = RQ.write(plan)
    synced = Q.sync(plan, now=_iso(now))
    return {"state": "OK", "plan_sha256": plan_sha, "plan_queued": plan["queued"],
            "intake_state": plan["intake_state"], **synced, "counts": Q.counts()}


def research_step(*, now: datetime | None = None, deps: EX.Deps | None = None,
                  run_id: str | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    AC.contract()
    run_id = run_id or now.strftime("HAUTO-%Y%m%dT%H%M%SZ")
    recovered = Q.recover_interrupted()
    auth = BU.authorization()
    if auth is None:
        return {"state": BU.NOT_AUTHORIZED, "run_id": run_id, "model_calls": 0, "started": [],
                "recovered": recovered, "counts": Q.counts(), "budget": BU.summary(BU.et_day(now))}
    deps = deps or EX.Deps(now=lambda: datetime.now(timezone.utc))
    day = BU.et_day(now)
    results: list[dict[str, Any]] = []
    for item in Q.ordered(now=_iso(now)):
        admission = BU.check(day=day, reason=item.get("reason", ""))
        if not admission["admitted"]:
            if not (item["status"] == Q.BUDGET_DEFERRED and item.get("status_reason") == admission["why"]):
                Q.transition(item, "BUDGET_DEFERRED", Q.BUDGET_DEFERRED, status_reason=admission["why"])
            results.append({"queue_id": item["queue_id"], "status": Q.BUDGET_DEFERRED, "why": admission["why"]})
            continue
        attempts = int(item.get("attempt_count") or 0) + 1
        Q.transition(item, "STARTED", Q.RUNNING, attempt_count=attempts, run_id=run_id,
                     started_at=_iso(datetime.now(timezone.utc)), status_reason=None)
        outcome = EX.execute(item, deps, run_id=run_id)
        status, reason = outcome.status, outcome.reason
        fields = dict(outcome.fields)
        if outcome.retryable or status in (Q.QUEUED, Q.DATA_NOT_READY):
            fields["eligible_after"] = next_run_floor(now)
            if outcome.retryable and attempts >= AC.max_queue_attempts():
                status, reason = Q.FAILED, f"QUEUE_ATTEMPTS_EXHAUSTED:{reason}"
        fields["cost_usd"] = round(float(item.get("cost_usd") or 0.0) + float(fields.get("cost_usd") or 0.0), 6)
        fields.pop("model", None)
        keep = {k: v for k, v in fields.items() if k in Q.FIELDS}
        if status == Q.COMPLETE:
            keep.update({k: v for k, v in EX.VERSIONS.items()})
            keep["completed_at"] = _iso(datetime.now(timezone.utc))
        current = Q.fold()[item["queue_id"]]
        Q.transition(current, "FINISHED", status, status_reason=reason, **keep)
        results.append({"queue_id": item["queue_id"], "status": status, "why": reason,
                        "cost_usd": outcome.fields.get("cost_usd"),
                        "final_decision": fields.get("final_decision"),
                        "decision_transition": fields.get("decision_transition"),
                        "ledger_written": fields.get("ledger_written")})
    return {"state": "OK", "run_id": run_id, "results": results, "recovered": recovered,
            "counts": Q.counts(), "budget": BU.summary(day)}


#: ``usb-h-forward.timer``: ``OnCalendar=Mon..Fri 01:10 America/New_York``. Stated here only to show
#: the next run on screen; the timer unit stays the authority.
TIMER_WEEKDAYS, TIMER_AT = (0, 1, 2, 3, 4), time(1, 10)


def next_scheduled_run(now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone(BU.ET)
    day = local.date()
    for _ in range(8):
        moment = datetime.combine(day, TIMER_AT, tzinfo=BU.ET)
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
    return {"last_auto_run_at": run.get("last_auto_run_finished_at"),
            "latest_price_session": PR.latest_session(),
            "sec_refresh_at": refresh.get("checked_at") or run.get("last_sec_refresh_at"),
            "sec_refresh_state": refresh.get("refresh_run"),
            "research_queue_last_run_at": run.get("last_queue_run_at"),
            "research_last_run_at": run.get("last_research_run_at"),
            "next_auto_run_at": next_scheduled_run(),
            "source": "runtime files (auto/run_status.json, refresh_status.json, price store)"}


def status() -> dict[str, Any]:
    """What the screen shows: queue counts, open items, budget and the runtime clock. Pure read."""
    items = Q.fold()
    return {"contract": AC.state(), "counts": Q.counts(items), "items": Q.view(items)[:50],
            "budget": BU.summary(), "authorized": BU.authorization() is not None,
            "timestamps": runtime_timestamps()}


def record_run_status(steps: Mapping[str, Any], *, started_at: str, finished_at: str,
                      kind: str = "update") -> None:
    """``run_status.json``: the timestamps the H screen shows (never a build time)."""
    previous = AS.read_json(AS.RUN_STATUS) or {}
    body = dict(previous)
    if kind == "update":
        body.update({"last_auto_run_started_at": started_at, "last_auto_run_finished_at": finished_at,
                     "steps": dict(steps)})
    else:
        body.update({"last_research_unit_started_at": started_at,
                     "last_research_unit_finished_at": finished_at, "research_steps": dict(steps)})
    for key, step in (("last_research_run_at", "research"), ("last_queue_run_at", "queue"),
                      ("last_sec_refresh_at", "sec_refresh"), ("last_intake_run_at", "intake")):
        if (steps.get(step) or {}).get("ran_at"):
            body[key] = steps[step]["ran_at"]
    AS.write_json(AS.RUN_STATUS, body)
