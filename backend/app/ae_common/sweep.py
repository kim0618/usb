"""A's 09:15 sweep over both chart lanes, with the tick lane stealing A-only symbols.

Production (A-in-E, 10/05-10/16) ran ``acquisition.run_a_pass``: the tick worker walked only E's
tick shard (about 1,254 symbols, about 270 s) and then stopped, while the minute worker walked
the rest of the union *alphabetically*. Every session hit 09:25:00 with about 849 symbols - the
R..Z tail - never read. This sweep keeps every per-symbol primitive of that pass and changes only
who takes which symbol, in which order:

* ``minute_order`` is the A union without E's tick shard: E-overlap symbols first, then A-only
  symbols, each group in sha256 order (outcome-free, no alphabetical bias);
* the minute lane walks ``minute_order`` from the front and then E's tick shard from its end,
  exactly as before;
* the tick lane walks E's tick shard first, in E's finalization order (E's freshness at 09:25 is
  unchanged), and then takes A-only symbols from the *end* of ``minute_order``. It never takes an
  E minute-shard symbol, so E's per-symbol lane pattern is the one E was measured on.

Each symbol is claimed once. The minute lane runs E's ``finalizer.refresh`` and the tick lane
``acquisition.refresh_from_ticks``, both merging at E's cutoff and leaving E's verdict fields
untouched; A's verdict is ``acquisition.APassOutcome`` with ``a_usable``, as before. A request is
refused at the limiter once the deadline is reached (:mod:`deadline`); a symbol whose walk is cut
by that refusal is recorded ``INTERRUPTED``, never usable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import threading
from typing import Any

from app.ae_common import snapshot as SS
from app.ae_common.deadline import DeadlineReached
from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_a_mover_live.schedule import A_CUTOFF_LAST_MINUTE
from app.strategy_e_max_rt import finalizer as FZ
from app.core import provider_errors as PE

INTERRUPTED = DeadlineReached.code
#: Legacy sanitized message retained for fixtures; classification uses explicit metadata now.
REJECTED_MESSAGE = "Kiwoom market-data query was rejected"
SOURCE_REFUSAL_PREFIX = SS.SOURCE_REFUSAL_PREFIX


def error_label(error: BaseException) -> str:
    """The outcome's error string. A source refusal is named as one; anything else keeps its kind."""
    from app.core.exceptions import MarketDataError
    if isinstance(error, MarketDataError):
        detail = PE.classify(error)
        return (SOURCE_REFUSAL_PREFIX if detail.source_refusal else "MarketDataError:") + detail.error_code
    return type(error).__name__


def hash_order(symbols: Iterable[str]) -> list[str]:
    """Symbols ordered by sha256, the same key ``finalizer.hash_lanes`` orders its shards by."""
    return [s for _, s in sorted((hashlib.sha256(s.encode()).hexdigest(), s) for s in set(symbols))]


@dataclass(frozen=True)
class SweepPlan:
    minute_order: tuple[str, ...]
    tick_shard: tuple[str, ...]
    steal_pool: frozenset[str]

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.minute_order + tuple(s for s in self.tick_shard if s not in set(self.minute_order))

    def declaration(self) -> dict[str, Any]:
        return {"minute_order": len(self.minute_order), "tick_shard": len(self.tick_shard),
                "steal_pool_a_only": len(self.steal_pool), "symbols": len(self.symbols),
                "order": "E-overlap then A-only, each in sha256 order; tick lane: E tick shard first, "
                         "then A-only from the end of the minute order"}


def plan(a_mapped: Iterable[str], e_mapped: Iterable[str], e_tick_shard: Sequence[str]) -> SweepPlan:
    """The sweep over A's mapped union plus E's tick shard. E-only minute-shard symbols are not
    swept: A does not read them and E's own finalization covers them at 09:25."""
    a_set, e_set, tick = set(a_mapped), set(e_mapped), tuple(e_tick_shard)
    tick_set = set(tick)
    overlap = hash_order(s for s in a_set if s in e_set and s not in tick_set)
    a_only = hash_order(s for s in a_set if s not in e_set and s not in tick_set)
    return SweepPlan(tuple(overlap + a_only), tick, frozenset(a_only))


@dataclass
class SweepReport:
    """The A pass report the A consumer rebuilds, plus what only this sweep knows."""

    a_pass: AQ.APassReport
    plan: SweepPlan
    not_reached: tuple[str, ...] = ()
    interrupted: tuple[str, ...] = ()
    steals: int = 0
    lane_pages: dict[str, int] = field(default_factory=dict)
    #: Walkers still waiting on a request when the sweep closed at the deadline; their answers are
    #: discarded. Each one leaves its symbol NOT_REACHED.
    detached_walkers: int = 0

    def declaration(self) -> dict[str, Any]:
        outcomes = self.a_pass.outcomes
        return self.plan.declaration() | {
            "t0": self.a_pass.t0.isoformat(), "t1": self.a_pass.t1.isoformat() if self.a_pass.t1 else None,
            "elapsed_s": self.a_pass.elapsed_seconds, "deadline_hit": self.a_pass.deadline_hit,
            "reached": len(outcomes), "usable": len(self.a_pass.usable),
            "stale": sum(1 for o in outcomes.values() if o.error is None and not o.usable),
            "failed": sum(1 for o in outcomes.values() if o.error not in (None, INTERRUPTED)),
            "source_refusals": sorted(s for s, o in outcomes.items()
                                      if o.error and o.error.startswith(SOURCE_REFUSAL_PREFIX)),
            "interrupted": len(self.interrupted), "not_reached": len(self.not_reached),
            "not_reached_examples": list(self.not_reached[:50]),
            "tick_lane_steals": self.steals, "lane_pages": dict(self.lane_pages),
            "detached_walkers": self.detached_walkers,
            "lane_calls": dict(self.a_pass.lane_calls)}


def run(lane_minute: FZ.Lane, lane_tick: FZ.Lane, caches: Mapping[str, FZ.SymbolCache], sweep: SweepPlan, *,
        session: date, now: Callable[[], datetime], deadline: datetime,
        cutoff: int = A_CUTOFF_LAST_MINUTE, join_step: float = 0.05) -> SweepReport:
    t0 = now()
    report = AQ.APassReport(session=session, t0=t0, t1=None, order=sweep.symbols,
                            tick_shard=sweep.tick_shard, cutoff_minute=cutoff)
    taken: set[str] = set()
    guard = threading.Lock()
    interrupted: list[str] = []
    pages = {lane_minute.name: 0, lane_tick.name: 0}
    steals = [0]

    def claim(symbol: str) -> bool:
        with guard:
            if symbol in taken:
                return False
            taken.add(symbol)
            return True

    def record_locked(symbol: str, lane: FZ.Lane, used: int, error: str | None) -> None:
        cache = caches[symbol]
        last = max((minute for minute in cache.bars if minute <= cutoff), default=None)
        usable = AQ.a_usable(cache, cutoff) if error is None else None
        pages[lane.name] += used
        report.outcomes[symbol] = AQ.APassOutcome(symbol, lane.name, used, usable, now(), last, error,
                                                 cache.error_detail if error else None)

    def walk(lane: FZ.Lane, symbols: Iterable[str], step, *, stealing: frozenset[str] = frozenset()) -> None:
        for symbol in symbols:
            if now() >= deadline:
                report.deadline_hit = True
                return
            if symbol not in caches or not claim(symbol):
                continue
            live = caches[symbol]
            scratch, started = _scratch(live), live.calls
            error: str | None = None
            try:
                step(lane, scratch)
            except DeadlineReached:
                error = INTERRUPTED
            except Exception as failure:
                error = error_label(failure)
                scratch.record_error(failure)
            else:
                scratch.record_error(None)
            with guard:
                if closed[0]:
                    return                 # answered after the sweep closed: nothing is kept
                if error != INTERRUPTED:   # a refused page leaves the cache as it was
                    _commit(scratch, live)
                else:
                    report.deadline_hit = True
                    interrupted.append(symbol)
                record_locked(symbol, lane, scratch.calls - started, error)
                if error is None and symbol in stealing:
                    steals[0] += 1
            if error == INTERRUPTED:
                return

    def minute_step(lane: FZ.Lane, cache: FZ.SymbolCache) -> None:
        FZ.refresh(lane, cache, session, now)

    def tick_step(lane: FZ.Lane, cache: FZ.SymbolCache) -> None:
        AQ.refresh_from_ticks(lane, cache, session, now)

    stealable = [s for s in sweep.minute_order if s in sweep.steal_pool]
    closed = [False]
    threads = [
        threading.Thread(target=walk, args=(lane_minute, list(sweep.minute_order) + list(reversed(sweep.tick_shard)),
                                            minute_step), daemon=True, name="sweep-minute"),
        threading.Thread(target=walk, args=(lane_tick, list(sweep.tick_shard) + list(reversed(stealable)), tick_step),
                         kwargs={"stealing": sweep.steal_pool}, daemon=True, name="sweep-tick"),
    ]
    for thread in threads:
        thread.start()
    # Wait for the walkers only until the deadline. A request still in flight then (production saw
    # a 202 s answer) must not hold E's 09:25:00 start, and its late answer must not reach a cache E
    # is finalizing: it was read into a scratch copy, and nothing is committed once the sweep closes.
    for thread in threads:
        while thread.is_alive() and now() < deadline:
            thread.join(timeout=join_step)
    with guard:
        closed[0] = True
        detached = sum(thread.is_alive() for thread in threads)
        if detached:
            report.deadline_hit = True
        report.t1 = max((o.finalized_at for o in report.outcomes.values() if o.finalized_at), default=None)
        report.lane_calls = {lane_minute.name: lane_minute.calls, lane_tick.name: lane_tick.calls}
        not_reached = tuple(s for s in sweep.symbols if s in caches and s not in report.outcomes)
        return SweepReport(report, sweep, not_reached, tuple(interrupted), steals[0], dict(pages), detached)


def _scratch(live: FZ.SymbolCache) -> FZ.SymbolCache:
    """The fields ``refresh`` / ``refresh_from_ticks`` read and write, copied."""
    copy = FZ.SymbolCache(live.symbol, live.exchange, live.code)
    copy.bars = {m: list(v) for m, v in live.bars.items()}
    copy.complete_through, copy.first_fetch, copy.calls = live.complete_through, live.first_fetch, live.calls
    copy.error, copy.error_detail = live.error, live.error_detail
    copy.source_refusal_observations = live.source_refusal_observations
    return copy


def _commit(scratch: FZ.SymbolCache, live: FZ.SymbolCache) -> None:
    live.bars, live.complete_through = scratch.bars, scratch.complete_through
    live.first_fetch, live.calls = scratch.first_fetch, scratch.calls
    live.exchange = scratch.exchange
    live.error, live.error_detail = scratch.error, scratch.error_detail
    live.source_refusal_observations = scratch.source_refusal_observations
