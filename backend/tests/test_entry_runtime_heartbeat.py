"""The A runner's heartbeat means "this cycle ran and absorbed no error".

Liveness alone would be wrong in both directions: a process whose every cycle fails (the
2026-10-12 ``Exchange MISSING`` loop) must not look healthy, and a cycle with nothing to
evaluate must still count as a successful one.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.runtime import RuntimeStateRecord
from app.services.entry_management_runtime import EntryAction, EntryManagementRuntime, EntryOutcome
from tests.test_entry_management_runtime import OPEN, durable  # noqa: F401

def cands(*symbols):
    return tuple(SimpleNamespace(symbol=symbol) for symbol in symbols)


IN_WINDOW = OPEN + timedelta(minutes=30)
AFTER_HOURS = OPEN + timedelta(hours=9)


class Lifecycle:
    """The slice of EntryLifecycleService the runtime loop calls."""

    def __init__(self, *, candidates=(), explode_on_list=False, explode_on_evaluate=False) -> None:
        self.candidates = candidates
        self.explode_on_list = explode_on_list
        self.explode_on_evaluate = explode_on_evaluate
        self.errors: list[str] = []

    def expire_ended_signals(self, now): ...
    def finalize_pending(self, now): return ()

    def approved_candidates_for_entry_session(self, entry_session_date):
        if self.explode_on_list:
            raise RuntimeError("candidate lookup failed")
        return self.candidates

    def evaluate(self, candidate, provider, *, as_of):
        if self.explode_on_evaluate:
            raise RuntimeError("Exchange MISSING is not a supported US exchange")
        return EntryOutcome(candidate.symbol, EntryAction.HOLD, "ok")

    def record_candidate_error(self, candidate, entry_session_date, code, now):
        self.errors.append(code)


def beat(factory):
    with factory() as session:
        return session.scalar(select(RuntimeStateRecord.last_heartbeat_at).where(RuntimeStateRecord.id == 1))


async def one_cycle(runtime, lifecycle, at):
    """Run the real loop for exactly one cycle, then stop it."""
    owner = EntryManagementRuntime(runtime, lambda: object(), clock=lambda: at, lifecycle=lifecycle)
    task = asyncio.create_task(owner.run())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if owner._cycle_seq >= 1 and (owner._cycle_errors or True):
            await asyncio.sleep(0.05)
            break
    owner.stop()
    await asyncio.wait_for(task, timeout=5)
    return owner


@pytest.mark.asyncio
async def test_a_cycle_with_no_candidate_still_records_the_heartbeat(durable):
    runtime, factory = durable
    assert beat(factory) is None
    await one_cycle(runtime, Lifecycle(candidates=()), IN_WINDOW)
    assert beat(factory) == IN_WINDOW.astimezone(beat(factory).tzinfo)


@pytest.mark.asyncio
async def test_a_cycle_outside_the_entry_window_is_still_a_successful_cycle(durable):
    runtime, factory = durable
    await one_cycle(runtime, Lifecycle(candidates=cands("AAA")), AFTER_HOURS)
    assert beat(factory) is not None


@pytest.mark.asyncio
async def test_a_candidate_that_errors_every_cycle_does_not_stamp_a_heartbeat(durable):
    runtime, factory = durable
    lifecycle = Lifecycle(candidates=cands("AAA", "BBB"), explode_on_evaluate=True)
    owner = await one_cycle(runtime, lifecycle, IN_WINDOW)
    assert owner._cycle_errors == 2 and lifecycle.errors == ["RuntimeError", "RuntimeError"]
    assert beat(factory) is None                      # the failure stays visible as a missing time


@pytest.mark.asyncio
async def test_an_exception_out_of_the_cycle_does_not_stamp_a_heartbeat(durable):
    runtime, factory = durable
    await one_cycle(runtime, Lifecycle(explode_on_list=True), IN_WINDOW)
    assert beat(factory) is None


@pytest.mark.asyncio
async def test_a_good_cycle_after_a_failed_one_advances_the_heartbeat(durable):
    runtime, factory = durable
    await one_cycle(runtime, Lifecycle(candidates=cands("AAA"), explode_on_evaluate=True), IN_WINDOW)
    assert beat(factory) is None
    await one_cycle(runtime, Lifecycle(candidates=()), IN_WINDOW + timedelta(minutes=1))
    assert beat(factory) is not None


@pytest.mark.asyncio
async def test_a_heartbeat_write_failure_never_reaches_the_entry_path(durable):
    runtime, _ = durable

    def broken():
        raise RuntimeError("database is locked")

    from dataclasses import replace
    broken_runtime = replace(runtime, session_factory=broken)
    owner = await one_cycle(broken_runtime, Lifecycle(candidates=cands("AAA")), IN_WINDOW)
    assert owner._cycle_seq == 1 and owner._cycle_errors == 0      # evaluation still ran
