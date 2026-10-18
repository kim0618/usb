"""The common producer's session: rolling, A sweep, snapshot_0915, E finalization, snapshot_0924.

:func:`run_session` is the whole acquisition morning with its lanes, clock and sleeper injected,
so the tests drive it on a virtual clock and the CLI (``app.dev.run_ae_common_producer``) drives
it on the wall clock with the real Kiwoom lanes. The E side is E-RUNTIME-R1's own finalization
loop, started at 09:25:00 by the clock whatever the A side is doing; the A consumer is another
process and is never awaited.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
import json
import os
from pathlib import Path
import threading
import traceback
from typing import Any

from app.ae_common import e_shadow as ES
from app.ae_common import snapshot as SS
from app.ae_common import sweep as SW
from app.ae_common.deadline import DeadlineRateLimiter, DeadlineReached
from app.strategy_e_max_rt import finalizer as FZ

ROLLING_START = dtime(4, 0)
A_CUT = dtime(9, 15)
E_FINALIZE = dtime(9, 25)
E_DEADLINE = dtime(9, 29, 45)
OPEN = dtime(9, 30)
E_CUTOFF_MINUTE = 9 * 60 + 24


@dataclass
class Inputs:
    """What the morning is planned over, all fixed before 04:00."""

    session: date
    exchanges: Mapping[str, str]
    a_symbols: tuple[str, ...]                       # A union (A's own ``universe.build``)
    a_union_checksum: str
    e_art: Mapping[str, Any] | None                  # E's canonical D-1 artifact, identity-checked
    e_refusal: str | None = None                     # why E has no artifact, when it has none
    #: Permanent source refusals on record in E's RVOL store (E-RUNTIME-R1 DOCUMENTED_REFUSALS)
    documented_refusals: tuple[str, ...] = ()

    @property
    def e_symbols(self) -> tuple[str, ...]:
        return tuple(self.e_art["symbols"]) if self.e_art else ()

    def code(self, symbol: str) -> str:
        return FZ.kiwoom_code(symbol, self.exchanges)

    def mapped(self, symbol: str) -> bool:
        return self.code(symbol) in self.exchanges

    @property
    def a_unmapped(self) -> tuple[str, ...]:
        return tuple(s for s in self.a_symbols if not self.mapped(s))

    @property
    def e_unmapped(self) -> tuple[str, ...]:
        return tuple(s for s in self.e_symbols if not self.mapped(s))

    @property
    def union(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.a_symbols) | set(self.e_symbols)))


@dataclass
class Clock:
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    session: date

    def at(self, moment: dtime) -> datetime:
        return datetime.combine(self.session, moment, tzinfo=FZ.ET)

    def wait_until(self, moment: datetime, step: float = 0.2) -> None:
        while (left := (moment - self.now()).total_seconds()) > 0:
            self.sleep(min(step, left))


@dataclass
class EPaper:
    """E's official paper stage: E-RUNTIME-R1's ``_paper_stage`` on the V2 official book.

    ``book_root`` is E's run root; the book is ``<book_root>/paper_state_v2/OFFICIAL_KIWOOM_PAPER/
    STRATEGY_E_MAX_V1`` (execution ``e_cost_v2_exact_0934_close``, initial equity ``equity``), which
    no earlier runtime has written. ``provider_factory`` builds the entry/exit price provider on its
    own client and limiter - the producer's lanes are closed by then."""

    book_root: Path
    provider_factory: Callable[[], Any]
    calendar: Any
    equity: str = "10000"
    interval: float = 10.0


@dataclass
class Morning:
    """Everything the session leaves behind, for the run record and the gate."""

    report: dict[str, Any] = field(default_factory=dict)
    seal_0915: dict[str, Any] = field(default_factory=dict)
    e_shadow: dict[str, Any] = field(default_factory=dict)
    a_parity: dict[str, Any] = field(default_factory=dict)


def _lat(lane: FZ.Lane) -> dict[str, float] | None:
    values = sorted(lane.latencies)
    if not values:
        return None
    return {"p50": values[len(values) // 2], "p95": values[int(len(values) * 0.95)], "max": values[-1]}


def run_session(inputs: Inputs, *, lane_minute: FZ.Lane, lane_tick: FZ.Lane,
                limiters: Sequence[DeadlineRateLimiter], clock: Clock, snapshot_dir: Path,
                rvol_store: Path, log: Callable[[str], None] = print,
                on_phase: Callable[[str], None] | None = None, e_paper: EPaper | None = None) -> Morning:
    session, at = inputs.session, clock.at
    morning = Morning()
    report = morning.report
    phase = on_phase or (lambda name: None)

    def begin(name: str, deadline: datetime) -> None:
        for limiter in limiters:
            limiter.begin_phase(name, deadline)
        phase(name)

    # -- caches over the union -------------------------------------------------------------------
    caches = {s: FZ.SymbolCache(s, inputs.exchanges[inputs.code(s)], inputs.code(s))
              for s in inputs.union if inputs.mapped(s)}
    spy = FZ.SymbolCache("SPY", inputs.exchanges.get("SPY", "NA"))
    e_mapped = [s for s in inputs.e_symbols if s in caches]
    a_mapped = [s for s in inputs.a_symbols if s in caches]
    e_shard_minute, e_shard_tick = FZ.hash_lanes(e_mapped)
    a_only = SW.hash_order(s for s in a_mapped if s not in set(e_mapped))
    order = [spy] + [caches[s] for s in e_shard_minute] + [caches[s] for s in a_only] + [caches[s] for s in e_shard_tick]
    report["universe"] = {"a_symbols": len(inputs.a_symbols), "a_unmapped": list(inputs.a_unmapped),
                          "e_symbols": len(inputs.e_symbols), "e_unmapped": list(inputs.e_unmapped),
                          "e_refusal": inputs.e_refusal, "union": len(inputs.union), "union_mapped": len(caches),
                          "overlap": len(set(inputs.a_symbols) & set(inputs.e_symbols)),
                          "a_only": len(set(inputs.a_symbols) - set(inputs.e_symbols)),
                          "e_only": sorted(set(inputs.e_symbols) - set(inputs.a_symbols)),
                          "e_shard_minute": len(e_shard_minute), "e_shard_tick": len(e_shard_tick),
                          "a_union_checksum": inputs.a_union_checksum,
                          "documented_refusals": list(inputs.documented_refusals)}
    rolling_order = [c.symbol for c in order]
    report["rolling_order_unique"] = len(rolling_order) == len(set(rolling_order))

    # -- 04:00 - 09:15 shared rolling (minute lane) ----------------------------------------------
    clock.wait_until(at(ROLLING_START).replace(second=5))
    begin("rolling", at(A_CUT))
    report["rolling_started_at"] = clock.now().isoformat()
    cycles, errors, calls0 = 0, 0, lane_minute.calls
    stop = False
    while not stop and clock.now() < at(A_CUT):
        cycles += 1
        for cache in order:
            if clock.now() >= at(A_CUT):
                break
            try:
                FZ.refresh(lane_minute, cache, session, clock.now)
            except DeadlineReached:
                stop = True
                break
            except Exception as error:
                errors += 1
                cache.error = type(error).__name__
            else:
                cache.error = None
        log(f"rolling cycle {cycles}: minute calls {lane_minute.calls - calls0} errors {errors}")
    report["rolling"] = {"cycles": cycles, "minute_calls": lane_minute.calls - calls0, "errors": errors,
                         "symbols_per_cycle": len(order), "ended_at": clock.now().isoformat(),
                         "incomplete_start": sorted(s for s, c in caches.items() if c.complete_through is None)[:50]}

    # -- 09:15 - 09:25 A sweep (both lanes) ------------------------------------------------------
    seal_thread: threading.Thread | None = None
    if a_mapped:
        clock.wait_until(at(A_CUT))
        begin("a_sweep", at(E_FINALIZE))
        observed = clock.now()
        plan = SW.plan(a_mapped, e_mapped, e_shard_tick)
        calls_before = {lane_minute.name: lane_minute.calls, lane_tick.name: lane_tick.calls}
        swept = SW.run(lane_minute, lane_tick, caches, plan, session=session, now=clock.now, deadline=at(E_FINALIZE))
        report["a_sweep"] = swept.declaration() | {
            "calls": {name: lane.calls - calls_before[name] for name, lane in
                      ((lane_minute.name, lane_minute), (lane_tick.name, lane_tick))}}
        log(f"A sweep: {report['a_sweep']['usable']} usable, {report['a_sweep']['not_reached']} not reached, "
            f"T1 {report['a_sweep']['t1']}")
        frozen = SS.freeze_a(caches, swept.a_pass, plan.symbols, session=session, observed_at=observed)

        def seal_a() -> None:
            started = clock.now()
            try:
                body = SS.body_0915(frozen, a_symbols=inputs.a_symbols, union_symbols=inputs.a_symbols,
                                    union_checksum=inputs.a_union_checksum, unmapped=inputs.a_unmapped,
                                    exchanges=inputs.exchanges, sweep=report["a_sweep"],
                                    documented_refusals=inputs.documented_refusals)
                manifest = SS.seal(snapshot_dir, SS.SNAPSHOT_0915, body, sealed_at=clock.now(),
                                   summary=SS.summary_0915(body))
                morning.seal_0915.update(status="SEALED", started_at=started.isoformat(),
                                         sealed_at=manifest["sealed_at"], manifest=manifest)
            except Exception as error:                    # A fails closed; E carries on
                morning.seal_0915.update(status="FAILED", error=f"{type(error).__name__}: {error}",
                                         traceback=traceback.format_exc()[-4000:])
        seal_thread = threading.Thread(target=seal_a, name="seal-0915")
        seal_thread.start()
    else:
        frozen = None
        morning.seal_0915.update(status="NOT_SEALED", reason="NO_A_UNIVERSE")

    # -- 09:25:00 E finalization, by the clock alone ---------------------------------------------
    if e_mapped:
        clock.wait_until(at(E_FINALIZE))
        begin("e_finalization", at(E_DEADLINE))
        morning.e_shadow = _finalize_e(inputs, caches, spy, e_mapped, e_shard_minute, e_shard_tick,
                                       lane_minute, lane_tick, clock, snapshot_dir, rvol_store, report, log)
    else:
        morning.e_shadow = ES.no_decision("UNIVERSE_NOT_AVAILABLE", inputs.e_refusal or "no E universe")
    begin("closed", at(E_DEADLINE))

    # -- E official paper: only a sealed, gated decision from snapshot_0924 reaches SimBroker -----
    decision, record = morning.e_shadow.pop("_decision", None), morning.e_shadow.pop("_record", None)
    if e_paper is None:
        morning.e_shadow["paper_runtime"] = {"ran": False, "reason": "E_PAPER_NOT_ENABLED"}
    elif decision is None:
        morning.e_shadow["paper_runtime"] = {"ran": False, "reason": "NO_SEALED_DECISION",
                                             "book_written": False}
    else:
        morning.e_shadow["paper_runtime"] = _paper(e_paper, decision, record, morning.e_shadow, clock, log)

    # -- after both seals: A parity (file vs the frozen live cache) ------------------------------
    if seal_thread is not None:
        seal_thread.join()
        morning.a_parity = _a_parity(frozen, snapshot_dir, session, morning, clock)
    report["seal_0915"] = {k: v for k, v in morning.seal_0915.items() if k != "traceback"}
    report["limiters"] = [limiter.declaration() for limiter in limiters]
    report["lanes"] = {lane.name: {"api": lane.api_id, "calls": lane.calls, "errors": lane.errors,
                                   "latency_s": _lat(lane)} for lane in (lane_minute, lane_tick)}
    return morning


def _finalize_e(inputs: Inputs, caches: dict[str, FZ.SymbolCache], spy: FZ.SymbolCache, e_mapped: list[str],
                shard_a: list[str], shard_b: list[str], lane_a: FZ.Lane, lane_b: FZ.Lane, clock: Clock,
                snapshot_dir: Path, rvol_store: Path, report: dict[str, Any], log) -> dict[str, Any]:
    """E-RUNTIME-R1's finalization loop verbatim, then snapshot_0924 and the shadow decision."""
    session, at = inputs.session, clock.at
    deadline = at(E_DEADLINE)
    t0 = clock.now()
    taken, guard = set(), threading.Lock()

    def claim(symbol: str) -> bool:
        with guard:
            if symbol in taken:
                return False
            taken.add(symbol)
            return True

    def worker_a() -> None:
        for sym in ["SPY"] + shard_a + list(reversed(shard_b)):
            if clock.now() >= deadline:
                return
            cache = spy if sym == "SPY" else caches[sym]
            if not claim(sym):
                continue
            try:
                FZ.finalize_minute(lane_a, cache, session, clock.now)
                cache.error = None
            except DeadlineReached:
                return
            except Exception as error:
                cache.error = type(error).__name__

    def worker_b() -> None:
        for sym in shard_b:
            if clock.now() >= deadline:
                return
            if not claim(sym):
                continue
            try:
                FZ.finalize_ticks(lane_b, caches[sym], session, clock.now)
                caches[sym].error = None
            except DeadlineReached:
                return
            except Exception as error:
                caches[sym].error = type(error).__name__

    before = {lane_a.name: lane_a.calls, lane_b.name: lane_b.calls}
    threads = [threading.Thread(target=worker_a), threading.Thread(target=worker_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stopped = clock.now()
    e_caches = {s: caches[s] for s in e_mapped}
    finalized = [c.finalized_at for c in e_caches.values() if c.finalized_at]
    t1 = max(finalized) if finalized else None
    violations = sum(1 for c in e_caches.values() if any(m > E_CUTOFF_MINUTE for m in c.bars))
    tick_pages = sorted(c.final_pages for c in e_caches.values() if c.data_source == "KIWOOM_" + FZ.TICK_API)
    finalization = {"T0": t0.isoformat(), "T1": t1.isoformat() if t1 else None,
                    "T0_delay_s": (t0 - at(E_FINALIZE)).total_seconds(),
                    "elapsed_s": (t1 - t0).total_seconds() if t1 else None,
                    "margin_to_0930_s": (at(OPEN) - t1).total_seconds() if t1 else None,
                    "kiwoom_stopped_at": stopped.isoformat(),
                    "calls": {lane_a.name: lane_a.calls - before[lane_a.name],
                              lane_b.name: lane_b.calls - before[lane_b.name]},
                    "tick_pages": {"symbols": len(tick_pages), "total": sum(tick_pages),
                                   "avg": (sum(tick_pages) / len(tick_pages)) if tick_pages else None,
                                   "max": tick_pages[-1] if tick_pages else None},
                    "future_cutoff_violation_count": violations}
    report["e_finalization"] = finalization
    log(f"E finalization T0 {t0.time()} T1 {t1.time() if t1 else None} violations {violations}")
    art = inputs.e_art
    try:
        body = SS.body_0924(session, caches, spy, universe=inputs.e_symbols, unmapped=inputs.e_unmapped,
                            universe_digest=art["digest"], finalization=finalization)
        manifest = SS.seal(snapshot_dir, SS.SNAPSHOT_0924, body, sealed_at=clock.now())
    except Exception as error:
        return ES.no_decision("COMMON_SNAPSHOT_0924_NOT_SEALED", f"{type(error).__name__}: {error}") | {
            "finalization": finalization}
    out: dict[str, Any] = {"status": "DECIDING", "snapshot_0924": manifest, "finalization": finalization,
                           "real_orders": 0, "sim_entries": 0, "simbroker_used": False, "book_written": False}
    try:
        store = ES.ReadOnlyRvolStore(rvol_store)
        sealed = SS.load(snapshot_dir, SS.SNAPSHOT_0924, fmt=SS.FORMAT_0924, session=session)
        file_caches, file_spy = SS.e_caches_from(sealed.body)
        from_file = ES.decide(file_caches, file_spy, art, unmapped=inputs.e_unmapped, session=session,
                              deadline=deadline, store=store, decided_at=clock.now(),
                              source="KIWOOM_COMMON_PRODUCER_SNAPSHOT_0924")
        out["from_snapshot"] = ES.public(from_file)
        out["_decision"], out["_record"] = from_file["_decision"], from_file["_record"]
        out["session_record"] = from_file["_record"]
        out["status"] = "DECIDED"
        log(f"E decision: gate {from_file['gate']['status']} selected {from_file['decision']['selected']}")
        from_live = ES.decide(e_caches, spy, art, unmapped=inputs.e_unmapped, session=session, deadline=deadline,
                              store=store, decided_at=clock.now(), source="KIWOOM_COMMON_PRODUCER_LIVE_CACHE")
        out["live_cache_gate_status"] = from_live["gate"]["status"]
        out["parity"] = ES.parity(from_file, from_live)
        store.connection.close()
    except Exception as error:
        out.pop("_decision", None)
        out.pop("_record", None)
        out.update(status="NO_DECISION", reason="E_DECISION_FAILED", detail=f"{type(error).__name__}: {error}",
                   traceback=traceback.format_exc()[-4000:])
    return out


def _paper(e_paper: EPaper, decision: Any, record: dict[str, Any], e_out: dict[str, Any], clock: Clock,
           log) -> dict[str, Any]:
    """E-RUNTIME-R1's paper stage, unchanged: an unauthorized gate is recorded in the book and not
    traded; an authorized one enters at the 09:30 open and exits at the exact 09:34 close."""
    from app.dev import run_e_rt2_dryrun as RT2
    gate = e_out["from_snapshot"]["gate"]
    try:
        summary = RT2._paper_stage(decision, clock.session, record, Path(e_paper.book_root), e_paper.calendar,  # noqa: SLF001
                                   clock.now, clock.at, log, e_paper.equity,
                                   provider_factory=e_paper.provider_factory, interval=e_paper.interval,
                                   book=RT2.BOOK_OFFICIAL, gate=gate)
    except Exception as error:                       # E fails closed on its own; A is another process
        log(f"E_PAPER_FAILED {type(error).__name__}: {error}")
        return {"ran": False, "reason": "E_PAPER_FAILED", "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc()[-4000:]}
    e_out["sim_entries"] = summary.get("entries", 0)
    e_out["real_orders"] = summary.get("real_orders", 0)
    e_out["simbroker_used"] = bool(summary.get("ran"))
    e_out["book_written"] = True
    return summary


def _a_parity(frozen: SS.FrozenA | None, snapshot_dir: Path, session: date, morning: Morning,
              clock: Clock) -> dict[str, Any]:
    """Legacy A snapshots (filter at read, live cache) against A snapshots from the sealed file."""
    if frozen is None or morning.seal_0915.get("status") != "SEALED":
        return {"status": "NOT_COMPUTED", "reason": morning.seal_0915.get("status")}
    try:
        legacy = SS.row_digests(SS.a_snapshot_rows(frozen.caches, frozen.a_pass, session=session,
                                                   observed_at=frozen.observed_at))
        sealed = SS.load(snapshot_dir, SS.SNAPSHOT_0915, fmt=SS.FORMAT_0915, session=session)
        caches, passed, observed = SS.a_caches_from(sealed.body)
        from_file = SS.row_digests(SS.a_snapshot_rows(caches, passed, session=session, observed_at=observed))
        bars_after_cut = sum(1 for e in sealed.body["symbols"].values()
                             for row in e.get("bars", ()) if row[0] >= SS.A_CUT_MINUTE)
        result = {"status": "COMPUTED", "bars_at_or_after_cut_in_file": bars_after_cut,
                  "comparison": SS.compare_digests(legacy, from_file),
                  "usable_legacy": len(frozen.a_pass.usable), "usable_file": len(passed.usable)}
        SS.seal(snapshot_dir, SS.A_PARITY, {"format": SS.FORMAT_A_PARITY, "session": session.isoformat(),
                                            "legacy_row_digests": legacy}, sealed_at=clock.now(),
                summary={"exact": result["comparison"]["exact"]})
        return result
    except Exception as error:
        return {"status": "FAILED", "error": f"{type(error).__name__}: {error}",
                "traceback": traceback.format_exc()[-4000:]}


def documented_refusals(rvol_store: Path) -> tuple[tuple[str, ...], str | None]:
    """E's permanent source refusals on record, read before 04:00 over a read-only connection."""
    from app.dev import run_e_rt2_dryrun as RT2
    try:
        store = ES.ReadOnlyRvolStore(rvol_store)
        try:
            state = store.collection_state()
        finally:
            store.connection.close()
    except Exception as error:
        return (), f"{type(error).__name__}: {error}"
    return tuple(sorted(s for s, st in state.items() if st.get("last_error") in RT2.DOCUMENTED_REFUSALS)), None


def write_json(path: Path, body: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(body, indent=1, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)
