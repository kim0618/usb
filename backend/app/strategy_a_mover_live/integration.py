"""The one seam the shared collector worker needs, so the worker's own edit stays four lines.

The acquisition process that owns the two Kiwoom lanes is E's worker. A's cut has to run inside
it, because a second process cannot have either lane: both are already held at 4.9 req/s
against a measured 5 req/s per-API-ID limit. This module is the whole A side of that, behind
one handle, so the edit on E's side is an import and three guarded expressions, which is what
``app.dev.run_e_rt2_dryrun`` now contains:

    a_live = integration.attach_isolated(...)          # never raises; handle None unless on
    if a_live.handle is not None:
        order = a_live.handle.rolling_order(order)
    rolling_deadline = (a_live.handle.rolling_until() if a_live.handle is not None
                        else at(FZ.REFRESH_B_AT))      # one deadline for the cycle and its loop
    while now() < rolling_deadline:
        ...
    if a_live.active:
        report["a_mover_live"] = a_live.run_cut()       # fail-closed; A never takes E down

With the flag off, ``active`` is False and ``handle`` is None, every expression evaluates to
the object it evaluates to today, no extra symbol enters any cache, no block runs and no key is
added to E's report. That is what makes "E unchanged when A is off" a property of the code
rather than a promise.

``attach_isolated`` rather than ``attach`` because ``attach`` is where A reads its data
authority - the dated reference cache, the grouped daily panel, the split calendar, E's staged
artifact - and an exception there unwound E's worker, which is official paper trading. Both
phases now sit behind ``isolation``: an expected data or authority failure makes A fail closed
with a named reason and zero candidates, an unexpected one is recorded with its traceback, and
either way E's acquisition, refresh, finalization and paper path run exactly as they do when A
is off. ``isolation`` states why the unexpected case is audited rather than re-raised.

**A's symbols never enter E's cache dictionary.** E aggregates its availability, its status
counts and its CSV over ``caches``; adding union-only symbols there would change every one of
those numbers. So the handle keeps its own ``extra_caches`` and A's pass walks the two
dictionaries together, while every E-side aggregation stays over E's own dictionary exactly as
today.

**A's pass leaves the cache fresher, not staler.** It runs E's own ``refresh``, merging at E's
cutoff, so ``complete_through`` after A's pass is at or past where E's own rolling cycle would
have left it. E's tick lane therefore pages back no further than in the run its cost was
measured on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.market.calendar import MarketCalendar
from app.strategy_e_max_rt import finalizer as FZ
from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import features as FEAT
from app.strategy_a_mover_live import gpt_handoff as GPT
from app.strategy_a_mover_live import isolation as ISO
from app.strategy_a_mover_live import raw_store as RAW
from app.strategy_a_mover_live import scanner as SCAN
from app.strategy_a_mover_live import universe as UNI
from app.services.mover_scanner_source import LiveRuntimeSource
from app.strategy_a_mover_live.schedule import A_CUT, SharedSchedule, a_pass_order, at


@dataclass
class SharedCollectorIntegration:
    """A's cut inside the shared collector process. Owns only A's state."""

    session: date
    repo: Path
    caches: Mapping[str, FZ.SymbolCache]
    shard_minute: tuple[str, ...]
    shard_tick: tuple[str, ...]
    lane_minute: FZ.Lane
    lane_tick: FZ.Lane
    now: Callable[[], datetime]
    union: UNI.UnionUniverse
    exchanges: Mapping[str, str]
    log: Callable[[str], None] = print
    session_factory: Callable[[], Any] | None = None
    calendar: MarketCalendar | None = None
    extra_caches: dict[str, FZ.SymbolCache] = field(default_factory=dict)
    unmapped: tuple[str, ...] = ()
    report: dict[str, Any] = field(default_factory=dict)
    #: The denominator rule in force, bootstrap half included. One object, so the read, the
    #: forward write and the recorded mix cannot disagree about which identities are A's.
    baseline_rule: B.BaselineRule = field(default_factory=B.BaselineRule)

    # -- rolling phase
    def rolling_until(self) -> datetime:
        """A's cut. The rolling cycle stops here instead of at E's own tick-shard refresh."""
        return at(self.session, A_CUT)

    def rolling_order(self, order: Sequence[Any]) -> list[Any]:
        """E's rolling order with A's extra symbols added and E's tick shard moved to the tail.

        The tail placement is what bounds E's tick-shard staleness at its cut; the head keeps
        E's own order for everything else, so no E symbol is read later than it is today
        relative to the others.
        """
        tick = set(self.shard_tick)
        head = [item for item in order if _symbol_of(item) not in tick]
        tail = [item for item in order if _symbol_of(item) in tick]
        extras = [self.extra_caches[name] for name in sorted(self.extra_caches)]
        return head + extras + tail

    # -- A's cut
    def all_caches(self) -> dict[str, FZ.SymbolCache]:
        return {**self.caches, **self.extra_caches}

    def pass_order(self) -> tuple[str, ...]:
        union = [name for name in self.union.union
                 if name in self.caches or name in self.extra_caches]
        return a_pass_order([name for name in union if name not in set(self.shard_tick)],
                            [name for name in self.shard_tick if name in self.caches])

    def run_cut(self) -> dict[str, Any]:
        """A's finalization, snapshot, raw persistence, baseline, scan and handoff."""
        schedule = SharedSchedule(self.session)
        schedule.validate()
        caches = self.all_caches()
        order = self.pass_order()
        tick = tuple(name for name in self.shard_tick if name in caches)
        observed = self.now()
        passed = AQ.run_a_pass(self.lane_minute, self.lane_tick, caches, session=self.session,
                               order=order, tick_shard=tick, now=self.now,
                               deadline=schedule.e_cut)
        self.log(f"A cut: {len(passed.usable)}/{len(passed.outcomes)} usable, "
                 f"T1 {passed.t1.time() if passed.t1 else None}")
        snapshots = AQ.snapshots_from(caches, passed, session=self.session, observed_at=observed)
        raw = AQ.raw_session_from(caches, passed, session=self.session, observed_at=observed)
        raw_path = RAW.write_session(self.repo, raw)
        body: dict[str, Any] = {
            "contract": LC.current().metadata(),
            "schedule": schedule.declaration(),
            "a_pass": passed.declaration(schedule),
            "raw_persistence": {"path": str(raw_path), "bars": len(raw),
                                "symbols": len(raw.symbols()), "accepted": raw.accepted,
                                "deduped": raw.deduped,
                                "quarantined": len(raw.quarantine)},
            "universe": self.union.declaration(),
            "unmapped_union_symbols": len(self.unmapped),
            "snapshots": len(snapshots),
        }
        body["forward_observation"] = self._record_forward(snapshots, observed)
        baselines = self._baselines(snapshots)
        body["baseline"] = {
            "identity": B.BASELINE_IDENTITY,
            "rule": self.baseline_rule.declaration(),
            "requested": len(snapshots),
            "available": sum(1 for item in baselines.values() if item.available),
            "database_available": self.session_factory is not None,
            "provider_mix": B.provider_mix(baselines, rule=self.baseline_rule),
        }
        try:
            panels = FEAT.assemble(self.repo, self.session, snapshots, baselines, self.union)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            # A named data refusal is A failing closed and is recorded with its reason. Anything
            # else is A's own code being wrong, and is re-raised to the isolation boundary,
            # which records it with a traceback instead of filing a bug under DATA_UNAVAILABLE.
            failure = ISO.classify(error, phase=ISO.Phase.CUT)
            if not failure.expected:
                raise
            body["scan"] = {"status": str(failure.refusal), "reason": str(failure.reason),
                            "detail": failure.detail, "error": failure.error,
                            "gpt_calls": 0, "candidates": 0}
            self.report = body
            return body
        body["features"] = panels.declaration()
        # Register the staged panels as the runtime's live premarket feed and then reach them
        # through ``LiveRuntimeSource``, which is the production source. Going through the
        # registry rather than handing the panels straight to the scanner is what makes this
        # the same path a later caller takes, instead of one that only works here. The
        # registration stays in place for the session, so the production source stops answering
        # NO_LIVE_PREMARKET_SOURCE once a feed actually exists.
        body["live_feed"] = {"registered_as": FEAT.register(panels),
                             "source": "mover_scanner_source.LIVE_PREMARKET_FEEDS"}
        try:
            live = SCAN.run_from_source(LiveRuntimeSource(), self.session,
                                        observed_at=observed)
        except SCAN.LiveScanRefused as refusal:
            body["scan"] = {"status": str(refusal.refusal),
                            "reason": str(ISO.classify(refusal, phase=ISO.Phase.CUT).reason),
                            "detail": refusal.detail, "gpt_calls": 0, "candidates": 0}
            self.report = body
            return body
        body["scan"] = live.metadata() | {"candidates": live.candidate_count}
        body["candidates"] = GPT.candidate_metadata(live)
        body["handoff"] = self._handoff(live)
        self.report = body
        return body

    # -- helpers
    def _exchange_of(self, symbol: str) -> str:
        return self.exchanges.get(FZ.kiwoom_code(symbol, self.exchanges), "")

    def _record_forward(self, snapshots: Mapping[str, Any],
                        observed: datetime) -> dict[str, Any]:
        """Store this morning's own observations, which is the whole forward replacement.

        Nothing an operator does moves the mix: tomorrow's walk finds one more Kiwoom session
        than today's did because this ran, and the oldest bootstrap session leaves the twenty
        on its own. The session written is the scan session, which ``lookback_sessions``
        excludes, so it cannot reach its own denominator.
        """
        if self.session_factory is None:
            return {"status": "NOT_RECORDED_NO_DATABASE", "written": 0}
        exchanges = {symbol: self._exchange_of(symbol) for symbol in snapshots}
        with self.session_factory() as database:
            body = B.record_forward_observations(
                database, snapshots, exchanges, collected_at=observed,
                rule=self.baseline_rule, calendar=self.calendar or MarketCalendar())
        return {"status": "RECORDED"} | body

    def _baselines(self, snapshots: Mapping[str, Any]) -> dict[str, B.AMoverBaseline]:
        """A's denominators from durable rows. No database means no denominator, not a guess.

        Both identities are read here: Kiwoom's observation for a session when there is one and
        the materialized bootstrap row otherwise. Twenty combined covered sessions is the
        precondition, so the first morning is calculable rather than the twenty-first.
        """
        if self.session_factory is None:
            return {}
        calendar = self.calendar or MarketCalendar()
        candidates = [(symbol, self._exchange_of(symbol)) for symbol in snapshots]
        with self.session_factory() as database:
            return B.load_baselines(database, candidates, self.session, calendar=calendar,
                                    rule=self.baseline_rule)

    def _handoff(self, live: SCAN.LiveScan) -> dict[str, Any]:
        if live.candidate_count == 0:
            return {"status": str(CFG.Refusal.NO_CANDIDATES), "gpt_calls": 0,
                    "persisted": False,
                    "detail": "the scan ran and admitted nobody; no prompt is rendered"}
        if self.session_factory is None:
            return {"status": "NOT_PERSISTED_NO_DATABASE", "gpt_calls": 0, "persisted": False,
                    "candidates": live.candidate_count}
        with self.session_factory() as database:
            result = GPT.persist(database, live, now=self.now())
        return {"status": "PERSISTED", "run_id": result.run_id, "reused": result.reused,
                "candidates": result.candidate_count, "prompt_chars": result.prompt_chars,
                "gpt_calls": result.gpt_calls_expected, "persisted": True,
                "human_approval_required": True}


def attach_isolated(*, session: date, repo: Path, caches: Mapping[str, FZ.SymbolCache],
                    shard_minute: Sequence[str], shard_tick: Sequence[str],
                    lane_minute: FZ.Lane, lane_tick: FZ.Lane, now: Callable[[], datetime],
                    exchanges: Mapping[str, str], log: Callable[[str], None] = print,
                    session_factory: Callable[[], Any] | None = None,
                    calendar: MarketCalendar | None = None,
                    environ: dict[str, str] | None = None, strict: bool = False,
                    status_root: str = ISO.STATUS_ROOT) -> ISO.IsolatedAttach:
    """``attach`` behind A's failure boundary. The call E's worker makes.

    With the flag off this does exactly what ``attach`` does - reads one environment variable
    and returns - and touches no file, so a disabled A leaves no trace at all, not even an
    audit record saying it was disabled.
    """
    repo_path = Path(repo)
    if not CFG.enabled(environ):
        return ISO.IsolatedAttach(active=False, handle=None, session=session, repo=repo_path,
                                  log=log, status_root=status_root)
    try:
        handle = attach(session=session, repo=repo, caches=caches, shard_minute=shard_minute,
                        shard_tick=shard_tick, lane_minute=lane_minute, lane_tick=lane_tick,
                        now=now, exchanges=exchanges, log=log,
                        session_factory=session_factory, calendar=calendar, environ=environ)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        failure = ISO.classify(error, phase=ISO.Phase.ATTACH)
        if strict and not failure.expected:
            raise
        isolated = ISO.IsolatedAttach(active=True, handle=None, session=session,
                                      repo=repo_path, strict=strict, log=log,
                                      status_root=status_root)
        isolated.status = isolated.fail(failure)
        return isolated
    return ISO.IsolatedAttach(active=handle is not None, handle=handle, session=session,
                              repo=repo_path, strict=strict, log=log, status_root=status_root)


def _symbol_of(item: Any) -> str:
    return getattr(item, "symbol", item) if not isinstance(item, str) else item


def attach(*, session: date, repo: Path, caches: Mapping[str, FZ.SymbolCache],
           shard_minute: Sequence[str], shard_tick: Sequence[str], lane_minute: FZ.Lane,
           lane_tick: FZ.Lane, now: Callable[[], datetime], exchanges: Mapping[str, str],
           log: Callable[[str], None] = print,
           session_factory: Callable[[], Any] | None = None,
           calendar: MarketCalendar | None = None,
           environ: dict[str, str] | None = None) -> SharedCollectorIntegration | None:
    """The handle, or None when the flag is off. A None return changes nothing on E's side."""
    if not CFG.enabled(environ):
        return None
    calendar = calendar or MarketCalendar()
    union = UNI.build(repo, session, calendar=calendar)
    handle = SharedCollectorIntegration(
        session=session, repo=Path(repo), caches=caches, shard_minute=tuple(shard_minute),
        shard_tick=tuple(shard_tick), lane_minute=lane_minute, lane_tick=lane_tick, now=now,
        union=union, exchanges=exchanges, log=log, session_factory=session_factory,
        calendar=calendar)
    unmapped: list[str] = []
    for symbol in union.union:
        if symbol in caches:
            continue
        code = FZ.kiwoom_code(symbol, exchanges)
        exchange = exchanges.get(code)
        if exchange is None:
            unmapped.append(symbol)
            continue
        handle.extra_caches[symbol] = FZ.SymbolCache(symbol, exchange, code)
    handle.unmapped = tuple(unmapped)
    log(f"A-MOVER-LIVE-V1 attached: union {len(union.union)}, E {len(caches)}, "
        f"A extras {len(handle.extra_caches)}, unmapped {len(unmapped)}")
    return handle
