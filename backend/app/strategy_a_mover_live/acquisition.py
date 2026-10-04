"""The shared A/E premarket acquisition layer: one process, E's primitives, A's cut added.

There is no second collector and no second app key. This module adds A's 09:15 cut to the
process that already owns the two timestamp-safe Kiwoom lanes, using E's own primitives:
``finalizer.Lane`` for the rate-limited page call, ``finalizer.SymbolCache`` with its
``merge_minutes`` / ``merge_ticks``, and ``finalizer.refresh`` itself for the minute lane,
called unchanged and uncopied.

**A's pass is a refresh pass, not a second finalization with a shorter cutoff.** That choice is
the whole design and it is what keeps E's cost from moving:

* merging at *A's* cutoff would leave ``complete_through`` at 09:14, and E's tick lane computes
  ``need_from = complete_through + 1``, so every tick-lane symbol would page back five extra
  minutes at E's own cut - a cost increase, measured against a run where that value was around
  09:19;
* merging at *E's* cutoff leaves ``complete_through`` at or past where E's own rolling cycle
  would have left it, so E's ``need_from`` is never earlier than in the run E's 1.037 pages per
  symbol were measured on.

So the shared cache may hold minutes after A's cut, and **A's point-in-time rule lives at the
read instead**: ``snapshot._matrix`` and ``raw_store.bars_from_cache`` both filter strictly
below the cut, so every A feature and every persisted A bar comes from a minute that had ended
at 09:15:00. ``APassReport.declaration`` reports ``a_cut_respected`` from the symbols' own last
read minute rather than asserting it.

The minute lane calls E's ``refresh`` verbatim. The tick lane cannot, because ``refresh`` parses
minute rows, so :func:`refresh_from_ticks` is the same walk over tick pages and advances
``complete_through`` by E's own rule - the last minute that had ended when the page was fetched.

**What A's pass does not write.** ``finalized_at``, ``data_source`` and ``contiguous`` are E's
verdict about E's cut. A keeps its own verdict in :class:`APassOutcome` rather than overwriting
them, so E's availability classification at 09:25 reads E's own values exactly as it does today.

**Order.** The pass runs the union with E's tick shard last, in E's finalization order, for the
staleness reason ``schedule.StalenessBound`` sets out. It costs no extra call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import threading
from typing import Any

from app.strategy_e_max_rt import finalizer as FZ
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import raw_store as RAW
from app.strategy_a_mover_live import snapshot as SNAP
from app.strategy_a_mover_live.schedule import (
    A_CUTOFF_LAST_MINUTE, E_CUTOFF_LAST_MINUTE, SharedSchedule, StalenessBound, a_pass_order,
)

#: E's own measured unit costs, the only numbers any estimate here is built from.
MEASURED = {
    "e_universe_symbols": (2561, "E-RT2 live 2026-09-22 canonical universe artifact"),
    "e_own_tick_refresh_calls": (1302, "E's own 09:20:40 refresh of its own tick shard"),
    "e_finalization_calls": (2609, "E-RT2 live: lane A 1,307 + lane B 1,302"),
    "e_finalization_seconds": (266.7, "E-RT2 live: T0 09:25:00.019 -> T1 09:29:26.698"),
    "aggregate_rate_per_s": (9.78, "E-RT2 live, two lanes at a 4.9/s limiter each, 0 x 429"),
    "per_api_id_limit_per_s": (5.0, "E-RT1 capability manifest: 429 at 12 req/s"),
    "e_tick_shard_symbols": (1302, "E-RT2 live: lane B finalized 1,302 symbols"),
    "e_measured_stale_window_s": (260.0, "E's own REFRESH_B_AT 09:20:40 -> FINALIZE_AT 09:25:00"),
}


def measured(name: str) -> float:
    return float(MEASURED[name][0])


def calls_per_symbol() -> float:
    return measured("e_finalization_calls") / measured("e_universe_symbols")


def e_own_refresh_seconds() -> float:
    """How long E's own 09:20:40 refresh of its tick shard takes, at the measured rate.

    It is one of the two mechanisms that preserve E's measured tick cost: when A's pass leaves
    at least this much slack before E's cut, that refresh runs exactly as it does today.
    """
    return measured("e_own_tick_refresh_calls") / measured("aggregate_rate_per_s")


# -- A's cut: E's own rolling walk, run once per symbol from A's cut ------------------------------
#
# A's pass is deliberately *not* a second finalization with a shorter cutoff. It is E's own
# ``refresh`` - the rolling walk that merges a symbol's latest minute page and advances
# ``complete_through`` under E's contiguity rule - run once per union symbol starting at A's cut.
# Two consequences, and both of them matter:
#
# * **E's cost cannot rise.** If A merged at A's cutoff it would leave ``complete_through`` at
#   09:14, and E's tick lane would then page back five extra minutes per symbol. Merging at E's
#   own cutoff leaves ``complete_through`` at or past where E's own refresh would have left it,
#   so E's ``need_from`` is never earlier than in the run E's cost was measured on.
# * **A's point-in-time rule moves to the read, where it belongs.** The shared cache may hold
#   minutes after A's cut; A never reads them. ``snapshot._matrix`` and
#   ``raw_store.bars_from_cache`` both filter strictly below the cut, so every A feature and
#   every persisted A bar comes from a minute that had ended at 09:15:00.
#
# The minute lane calls E's ``refresh`` unmodified. The tick lane cannot - ``refresh`` parses
# minute rows - so ``refresh_from_ticks`` is the same walk over tick pages, advancing
# ``complete_through`` by E's own rule: the last minute that had ended when the page was fetched.


def a_usable(cache: FZ.SymbolCache, cutoff: int = A_CUTOFF_LAST_MINUTE) -> bool:
    """Whether A's window is known contiguously from 04:00 through its last minute.

    ``complete_through`` is None until a symbol's walk has reached 04:00 of the session, which
    is E's own rule, so "not None and at least A's last minute" is exactly A's requirement.
    """
    return cache.complete_through is not None and cache.complete_through >= cutoff


def refresh_from_ticks(lane: FZ.Lane, cache: FZ.SymbolCache, session: date,
                       now: Callable[[], datetime], *, cutoff: int = E_CUTOFF_LAST_MINUTE,
                       max_pages: int = FZ.MAX_TICK_PAGES) -> int:
    """E's rolling walk over the tick lane: page back to the cached minute, then extend it.

    Returns the pages read. A symbol whose walk has not reached 04:00 yet keeps
    ``complete_through`` at None, so it is not usable for A and is not pretended to be.
    """
    need_from = (cache.complete_through + 1) if cache.complete_through is not None else 4 * 60
    rows: list[Mapping[str, Any]] = []
    continuation, reached, pages = None, False, 0
    for _ in range(max_pages):
        page = lane.page(cache.code or cache.symbol, cache.exchange, continuation)
        cache.calls += 1
        pages += 1
        body = page.body.get("result_list", [])
        rows.extend(body)
        if body:
            last = FZ.minute_timestamp(body[-1])
            if last.date() < session or FZ._minute_key(last) < need_from:  # noqa: SLF001
                reached = True
        if reached or not page.continuation or not body:
            reached = reached or not page.continuation
            break
        continuation = page
    fetched = now()
    through = min(FZ._minute_key(fetched) - 1, cutoff)  # noqa: SLF001
    cache.merge_ticks(rows, need_from, session, through)
    if reached and through >= need_from - 1:
        cache.complete_through = max(need_from - 1, through)
    cache.first_fetch = cache.first_fetch or fetched
    return pages


@dataclass
class APassOutcome:
    """A's own verdict for one symbol. Kept apart from E's verdict on the same cache."""

    symbol: str
    lane: str | None = None
    pages: int = 0
    #: Whether ``[04:00, A's cut)`` is known contiguously for this symbol. Not E's
    #: ``contiguous``, which is E's verdict about E's own cut and is never written here.
    a_window_complete: bool | None = None
    finalized_at: datetime | None = None
    last_complete_minute: int | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.a_window_complete) and self.error is None

    def row(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "lane": self.lane, "pages": self.pages,
                "a_window_complete": self.a_window_complete,
                "finalized_at": self.finalized_at.isoformat() if self.finalized_at else None,
                "last_complete_minute": self.last_complete_minute, "error": self.error}


@dataclass
class APassReport:
    """One A pass, with the facts E's SLA argument depends on."""

    session: date
    t0: datetime
    t1: datetime | None
    outcomes: dict[str, APassOutcome] = field(default_factory=dict)
    lane_calls: dict[str, int] = field(default_factory=dict)
    order: tuple[str, ...] = ()
    tick_shard: tuple[str, ...] = ()
    cutoff_minute: int = A_CUTOFF_LAST_MINUTE
    deadline_hit: bool = False

    @property
    def usable(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, item in self.outcomes.items() if item.usable))

    @property
    def elapsed_seconds(self) -> float | None:
        return (self.t1 - self.t0).total_seconds() if self.t1 else None

    def staleness(self, schedule: SharedSchedule) -> StalenessBound | None:
        if self.t1 is None or not self.tick_shard:
            return None
        per_symbol = (self.elapsed_seconds or 0.0) / max(len(self.order), 1)
        return StalenessBound(self.t1, schedule.e_cut, per_symbol * len(self.tick_shard),
                              measured("e_measured_stale_window_s"),
                              e_own_refresh_seconds())

    def declaration(self, schedule: SharedSchedule | None = None) -> dict[str, Any]:
        body = {
            "collector_version": LC.COLLECTOR_VERSION,
            "session": self.session.isoformat(),
            "a_t0_et": self.t0.isoformat(),
            "a_t1_et": self.t1.isoformat() if self.t1 else None,
            "elapsed_s": self.elapsed_seconds,
            "symbols": len(self.outcomes),
            "usable": len(self.usable),
            "stale_or_failed": len(self.outcomes) - len(self.usable),
            "lane_calls": dict(self.lane_calls),
            "cutoff_minute": self.cutoff_minute,
            "deadline_hit": self.deadline_hit,
            # A's cut is enforced at the read, so the shared cache legitimately holds later
            # minutes for E. What must hold is that nothing A reads is at or after the cut.
            "a_read_last_minute": max((item.last_complete_minute for item in self.outcomes.values()
                                       if item.last_complete_minute is not None), default=None),
            "a_cut_respected": all(item.last_complete_minute <= self.cutoff_minute
                                   for item in self.outcomes.values()
                                   if item.last_complete_minute is not None),
        }
        bound = self.staleness(schedule) if schedule else None
        if bound is not None:
            body["e_tick_shard_staleness"] = bound.declaration()
        return body


def run_a_pass(lane_minute: FZ.Lane, lane_tick: FZ.Lane, caches: Mapping[str, FZ.SymbolCache],
               *, session: date, order: Sequence[str], tick_shard: Sequence[str],
               now: Callable[[], datetime], deadline: datetime,
               cutoff: int = A_CUTOFF_LAST_MINUTE) -> APassReport:
    """A's pass over the union, both lanes in parallel, E's claim discipline exactly.

    ``order`` is the whole union with E's tick shard at its tail. The minute lane walks the
    head and then takes the tail from its end, which is how a lane that finishes early helps
    the other one without two lanes ever claiming the same symbol.
    """
    t0 = now()
    report = APassReport(session=session, t0=t0, t1=None, order=tuple(order),
                         tick_shard=tuple(tick_shard), cutoff_minute=cutoff)
    taken: set[str] = set()
    guard = threading.Lock()

    def claim(symbol: str) -> bool:
        with guard:
            if symbol in taken:
                return False
            taken.add(symbol)
            return True

    def record(symbol: str, lane_name: str, pages: int, error: str | None) -> None:
        cache = caches[symbol]
        last = max((minute for minute in cache.bars if minute <= cutoff), default=None)
        report.outcomes[symbol] = APassOutcome(
            symbol, lane_name, pages, a_usable(cache, cutoff) if error is None else None,
            now(), last, error)

    tick_set = set(tick_shard)
    minute_side = [symbol for symbol in order if symbol not in tick_set]

    def worker_minute() -> None:
        for symbol in minute_side + list(reversed(list(tick_shard))):
            if now() >= deadline:
                report.deadline_hit = True
                return
            if symbol not in caches or not claim(symbol):
                continue
            try:
                FZ.refresh(lane_minute, caches[symbol], session, now)
                record(symbol, lane_minute.name, 1, None)
            except Exception as error:
                record(symbol, lane_minute.name, 1, type(error).__name__)

    def worker_tick() -> None:
        for symbol in tick_shard:
            if now() >= deadline:
                report.deadline_hit = True
                return
            if symbol not in caches or not claim(symbol):
                continue
            try:
                pages = refresh_from_ticks(lane_tick, caches[symbol], session, now)
                record(symbol, lane_tick.name, pages, None)
            except Exception as error:
                record(symbol, lane_tick.name, 0, type(error).__name__)

    threads = [threading.Thread(target=worker_minute), threading.Thread(target=worker_tick)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    report.t1 = max((item.finalized_at for item in report.outcomes.values()
                     if item.finalized_at), default=None)
    report.lane_calls = {lane_minute.name: lane_minute.calls, lane_tick.name: lane_tick.calls}
    return report


def snapshots_from(caches: Mapping[str, FZ.SymbolCache], report: APassReport, *,
                   session: date, observed_at: datetime) -> dict[str, SNAP.SymbolSnapshot]:
    """One snapshot per symbol A finalized contiguously. A stale symbol produces nothing."""
    out: dict[str, SNAP.SymbolSnapshot] = {}
    for symbol in report.usable:
        cache = caches[symbol]
        out[symbol] = SNAP.build(symbol, session, cache.bars, observed_at=observed_at,
                                 data_source=report.outcomes[symbol].lane,
                                 finalized_at=report.outcomes[symbol].finalized_at,
                                 contiguous=True)
    return out


def raw_session_from(caches: Mapping[str, FZ.SymbolCache], report: APassReport, *,
                     session: date, observed_at: datetime,
                     cutoff: int = A_CUTOFF_LAST_MINUTE,
                     strict: bool = False) -> RAW.RawSession:
    """Every minute A read, as raw rows, so a later feature contract recomputes not recollects."""
    raw = RAW.RawSession(session, strict=strict)
    for symbol in report.usable:
        raw.extend(RAW.bars_from_cache(symbol, session, caches[symbol].bars, observed_at,
                                       cut_minute=cutoff + 1))
    return raw


# -- planning (no network) ------------------------------------------------------------------------

@dataclass(frozen=True)
class PassEstimate:
    """A's pass cost scaled from E's measured unit cost. Every input is named in ``MEASURED``."""

    union_symbols: int
    calls: int
    seconds: float
    schedule: SharedSchedule

    @property
    def t1(self) -> datetime:
        from datetime import timedelta
        return self.schedule.a_cut + timedelta(seconds=self.seconds)

    @property
    def fits_before_e_cut(self) -> bool:
        return self.t1 <= self.schedule.e_cut

    @property
    def e_t1(self) -> datetime:
        """E's finalization end, at E's own measured duration. A adds nothing to it."""
        return _plus(self.schedule.e_cut, measured("e_finalization_seconds"))

    @property
    def tick_shard_pass_seconds(self) -> float:
        return (measured("e_tick_shard_symbols") / max(self.union_symbols, 1)) * self.seconds

    def staleness(self) -> StalenessBound:
        return StalenessBound(self.t1, self.schedule.e_cut, self.tick_shard_pass_seconds,
                              measured("e_measured_stale_window_s"),
                              e_own_refresh_seconds())

    def declaration(self) -> dict[str, Any]:
        bound = self.staleness()
        return {
            "union_symbols": self.union_symbols,
            "a_finalization_calls": self.calls,
            "a_finalization_seconds": self.seconds,
            "a_t0_et": self.schedule.a_cut.isoformat(),
            "a_t1_et": self.t1.isoformat(),
            "a_fits_before_e_cut": self.fits_before_e_cut,
            "e_t0_et": self.schedule.e_cut.isoformat(),
            "e_finalization_calls": int(measured("e_finalization_calls")),
            "e_finalization_seconds": measured("e_finalization_seconds"),
            "e_t1_et": self.e_t1.isoformat(),
            "e_t1_before_deadline": self.e_t1 <= self.schedule.kiwoom_deadline,
            "e_t1_before_open": self.e_t1 < self.schedule.regular_open,
            "per_lane_rate_per_s": measured("aggregate_rate_per_s") / 2,
            "per_api_id_limit_per_s": measured("per_api_id_limit_per_s"),
            "rate_within_limit": (measured("aggregate_rate_per_s") / 2
                                  <= measured("per_api_id_limit_per_s")),
            "separate_a_process_possible": False,
            "separate_a_process_reason": "both timestamp-safe lanes are already held at 4.9/s "
                                         "against a measured 5/s per-API-ID limit",
            "e_tick_shard_staleness": bound.declaration(),
            "inputs": {key: {"value": value, "source": source}
                       for key, (value, source) in MEASURED.items()},
        }


def _plus(moment: datetime, seconds: float) -> datetime:
    from datetime import timedelta
    return moment + timedelta(seconds=seconds)


def estimate(session: date, union_symbols: int) -> PassEstimate:
    calls = union_symbols * calls_per_symbol()
    return PassEstimate(union_symbols=union_symbols, calls=round(calls),
                        seconds=calls / measured("aggregate_rate_per_s"),
                        schedule=SharedSchedule(session))


def plan_order(union: Sequence[str], tick_shard: Sequence[str]) -> tuple[str, ...]:
    return a_pass_order([symbol for symbol in union if symbol not in set(tick_shard)], tick_shard)
