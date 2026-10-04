"""Plan A's baseline backfill before spending 60+ hours of network on it. Nothing starts here.

A's 20-session premarket share-volume baseline has no rows: the stored Kiwoom snapshots hold
premarket *dollar* volume over one undivided ``[04:00, 09:24]`` window, so not one of the 159
stored sessions can be reconverted into A's quantity, window or statistic. The history exists
at the source - ``usa06011`` reaches back more than six months - so the baseline is buildable
by collection rather than by waiting twenty sessions for it to accumulate forward.

That collection is large, and this module exists so the size is a measured statement and not a
surprise. :func:`plan` answers, from the stores and the calendar alone and with no network
call at all:

* the exact sessions required, per symbol, as the covered-session walk defines them;
* the exact eligible symbols, from the union universe;
* the exact pages, calls and runtime, scaled from E-RT3's own measured per-symbol cost;
* what is already stored, so a resumed plan is smaller than a fresh one.

**Resumption is the durable table, not a file.** A stored row is final for its source and
collector version, and ``AMoverPremarketVolumeService.missing_sessions`` already returns only
the sessions with no final answer. So a killed run resumes by being started again, and the
progress file this module writes is a convenience for reporting, never the authority. Starting
twice cannot double-collect, because the second run plans against what the first one stored.

**The collection guard.** Collection must not run between 03:55 and 09:35 ET, the window in
which the live collector owns the lanes. :func:`in_guard_window` is that rule, and
:func:`execute`'s loop checks it before every symbol rather than once at the start.

Running the full backfill is a separate, explicitly requested act: :func:`execute` collects
only the symbols it is handed and refuses without ``confirm_network=True``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.market.calendar import MarketCalendar
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live import contract as LC

PROGRESS_ROOT = "data/runtime/strategy_a_mover_live/backfill"
#: E-RT3's measured collection cost, the only numbers any estimate here is built from.
MEASURED = {
    "pages_per_symbol": (223.0, "E-RT3: 571,103 pages for 2,561 symbols on usa06011"),
    "seconds_per_symbol": (63.0, "E-RT3 measured per-symbol collection time"),
    "e_sessions_per_symbol": (20, "the window E's 223 pages / 63 s per symbol covered: "
                                  "premarket_volume_history.BASELINE_SESSIONS"),
    "e_symbols": (2561, "E-RT3's collected universe"),
    "e_total_pages": (571103, "E-RT3 published total"),
    "e_hours_low": (32.4, "E-RT3 published range, fast end"),
    "e_hours_high": (40.3, "E-RT3 published range, slow end"),
    "lanes": (1, "usa06010 cannot page deep history, so the backfill is single-lane"),
}
#: Collection must not touch the lanes while the live collector owns them.
GUARD_START = time(3, 55)
GUARD_END = time(9, 35)


def measured(name: str) -> float:
    return float(MEASURED[name][0])


def in_guard_window(moment: datetime) -> bool:
    """True while the live premarket collector owns the lanes, in exchange time."""
    from app.strategy_a_mover_live.schedule import ET
    local = moment.astimezone(ET).time()
    return GUARD_START <= local < GUARD_END


@dataclass(frozen=True)
class SymbolPlan:
    """One symbol's exact remaining work, expected and worst case.

    The covered walk needs the newest **twenty** covered sessions, not the whole lookback, so
    the expected work is twenty minus what is already stored and covered. The lookback exists
    for the case where some of those twenty come back uncovered and an older session has to
    take their place, which is the worst case and is reported separately rather than charged
    to every symbol. Costing the whole lookback would overstate the backfill by about 2x.
    """

    symbol: str
    exchange: str
    #: Every session the covered walk may reach, oldest first.
    required_sessions: tuple[date, ...]
    #: Sessions inside the lookback with a final covered answer already stored.
    stored_covered: int
    #: What one run will fetch: the newest unanswered sessions, capped at what is still
    #: needed for the twenty. This is the collector's own plan, read from it, not recomputed.
    missing_sessions: tuple[date, ...]
    #: How many sessions the denominator needs in all.
    target_sessions: int = B.BASELINE_SESSIONS

    @property
    def expected_sessions(self) -> tuple[date, ...]:
        """One run's work, which is what the executor will actually ask for."""
        return self.missing_sessions

    @property
    def worst_case_session_count(self) -> int:
        """Every unanswered session in the lookback, if the newest ones come back uncovered.

        That case costs more *runs*, not more requests per run, because each run asks only for
        what is still needed and the stored answers are the checkpoint between them.
        """
        return max(len(self.required_sessions) - self.stored_covered, 0)

    @property
    def done(self) -> bool:
        return not self.expected_sessions


@dataclass(frozen=True)
class BackfillPlan:
    """The whole plan, with its estimate and the guard it must respect."""

    entry_session_date: date
    identity: str
    symbols: tuple[SymbolPlan, ...]
    rule: B.BaselineRule
    progress_path: Path | None = None

    @property
    def pending(self) -> tuple[SymbolPlan, ...]:
        return tuple(item for item in self.symbols if not item.done)

    @property
    def sessions_to_fetch(self) -> int:
        return sum(len(item.expected_sessions) for item in self.symbols)

    @property
    def worst_case_sessions_to_fetch(self) -> int:
        return sum(item.worst_case_session_count for item in self.symbols)

    @property
    def sessions_stored(self) -> int:
        return sum(item.stored_covered for item in self.symbols)

    def estimate(self) -> dict[str, Any]:
        """Pages, calls and runtime, scaled from E-RT3's own per-symbol measurement.

        E's 223 pages and 63 seconds per symbol covered E's own twenty-session requirement, so
        the unit is per *twenty sessions*; a plan that needs fewer or more sessions per symbol
        is scaled by that ratio rather than charged the whole unit.
        """
        symbols = len(self.pending)
        unit = measured("e_sessions_per_symbol")
        per_page = measured("pages_per_symbol") / unit
        per_hour_low = measured("e_hours_low") / measured("e_symbols") / unit
        per_hour_high = measured("e_hours_high") / measured("e_symbols") / unit
        expected, worst = self.sessions_to_fetch, self.worst_case_sessions_to_fetch
        return {
            "pending_symbols": symbols,
            "sessions_to_fetch": expected,
            "worst_case_sessions_to_fetch": worst,
            "sessions_already_stored": self.sessions_stored,
            "estimated_pages": round(expected * per_page),
            "estimated_calls": round(expected * per_page),   # one page is one call on this lane
            "estimated_hours_low": expected * per_hour_low,
            "estimated_hours_high": expected * per_hour_high,
            "worst_case_hours_low": worst * per_hour_low,
            "worst_case_hours_high": worst * per_hour_high,
            "lanes": int(measured("lanes")),
            "guard_window_et": [GUARD_START.isoformat(), GUARD_END.isoformat()],
            "guard_free_hours_per_day": 24 - (GUARD_END.hour + GUARD_END.minute / 60
                                              - GUARD_START.hour - GUARD_START.minute / 60),
            "inputs": {key: {"value": value, "source": source}
                       for key, (value, source) in MEASURED.items()},
        }

    def declaration(self) -> dict[str, Any]:
        return {
            "stage": "A_MOVER_LIVE_BASELINE_BACKFILL_PLAN",
            "identity": self.identity,
            "baseline_rule": self.rule.declaration(),
            "entry_session_date": self.entry_session_date.isoformat(),
            "eligible_symbols": len(self.symbols),
            "complete_symbols": len(self.symbols) - len(self.pending),
            "estimate": self.estimate(),
            "resumption": "the durable rows are the checkpoint; a stored final answer is never "
                          "re-fetched, so starting again resumes rather than repeats",
            "progress_path": str(self.progress_path) if self.progress_path else None,
        }


def plan(session_db: Session, entry_session_date: date, symbols: Sequence[tuple[str, str]], *,
         calendar: MarketCalendar | None = None, rule: B.BaselineRule | None = None,
         repo: Path | None = None) -> BackfillPlan:
    """The exact remaining work. Reads the database and the calendar; no network."""
    rule = rule or B.BaselineRule()
    calendar = calendar or MarketCalendar()
    service = B.AMoverPremarketVolumeService(session_db, None, calendar=calendar, rule=rule)
    plans: list[SymbolPlan] = []
    for symbol, exchange in symbols:
        required = service.required_sessions(entry_session_date)
        missing = service.missing_sessions(symbol, exchange, entry_session_date)
        covered = B.covered_count(session_db, symbol, exchange, entry_session_date,
                                  calendar=calendar, rule=rule)
        plans.append(SymbolPlan(symbol, exchange, required, covered, tuple(missing),
                                target_sessions=rule.sessions))
    progress = (Path(repo) / PROGRESS_ROOT / f"plan_{entry_session_date.isoformat()}.json"
                if repo else None)
    return BackfillPlan(entry_session_date, rule.identity, tuple(plans), rule, progress)


def write_progress(path: Path, body: Mapping[str, Any]) -> Path:
    """Atomic progress write. A convenience for reporting; the rows remain the authority."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_progress(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class BackfillRefused(RuntimeError):
    """Collection was asked for without the explicit network confirmation, or inside the guard."""


@dataclass
class BackfillRun:
    """What one collection pass actually did."""

    entry_session_date: date
    started_at: datetime
    symbols_done: list[str] = field(default_factory=list)
    symbols_failed: dict[str, str] = field(default_factory=dict)
    sessions_fetched: int = 0
    guard_stops: int = 0
    finished_at: datetime | None = None

    def declaration(self) -> dict[str, Any]:
        return {"entry_session_date": self.entry_session_date.isoformat(),
                "started_at": self.started_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
                "symbols_done": len(self.symbols_done),
                "symbols_failed": dict(self.symbols_failed),
                "sessions_fetched": self.sessions_fetched,
                "guard_stops": self.guard_stops,
                "collector_version": LC.COLLECTOR_VERSION}


def execute(service: B.AMoverPremarketVolumeService, plan_: BackfillPlan, *,
            confirm_network: bool, now: Callable[[], datetime],
            limit_symbols: int | None = None,
            progress_path: Path | None = None) -> BackfillRun:
    """Collect the planned symbols. Refuses without an explicit confirmation.

    ``limit_symbols`` is how a representative dry run is taken: the same code path over a few
    symbols, so the measured cost of the full run is this path's own cost and not an analogy.
    """
    if not confirm_network:
        raise BackfillRefused("the baseline backfill makes network calls and needs "
                              "confirm_network=True from an explicit request")
    run = BackfillRun(plan_.entry_session_date, now())
    targets = plan_.pending if limit_symbols is None else plan_.pending[:limit_symbols]
    # ``collect_symbol`` plans against the durable rows itself, so a symbol whose newest
    # sessions all come back covered stops at twenty without this loop deciding anything.
    for item in targets:
        if in_guard_window(now()):
            run.guard_stops += 1
            break                             # the live collector owns the lanes right now
        try:
            collected = service.collect_symbol(item.symbol, item.exchange,
                                               plan_.entry_session_date)
        except Exception as error:            # one symbol never ends the pass
            service.session.rollback()
            run.symbols_failed[item.symbol] = type(error).__name__
            continue
        run.sessions_fetched += len(collected.fetched_sessions)
        if collected.failure:
            run.symbols_failed[item.symbol] = collected.failure
        else:
            run.symbols_done.append(item.symbol)
        if progress_path is not None:
            write_progress(progress_path, run.declaration())
    run.finished_at = now()
    if progress_path is not None:
        write_progress(progress_path, run.declaration())
    return run


def validate(session_db: Session, entry_session_date: date, symbols: Sequence[tuple[str, str]],
             *, calendar: MarketCalendar | None = None,
             rule: B.BaselineRule | None = None) -> dict[str, Any]:
    """After a pass: how many symbols now have A's denominator, and what is still short."""
    rule = rule or B.BaselineRule()
    calendar = calendar or MarketCalendar()
    available, short = [], {}
    for symbol, exchange in symbols:
        item = B.load_baseline(session_db, symbol, exchange, entry_session_date,
                               calendar=calendar, rule=rule)
        if item.available:
            available.append(symbol)
        else:
            short[symbol] = {"status": str(item.status),
                             "covered_sessions": len(item.used_sessions),
                             "needed": rule.sessions}
    return {"identity": rule.identity, "entry_session_date": entry_session_date.isoformat(),
            "symbols": len(symbols), "baseline_available": len(available),
            "baseline_short": len(short),
            "baseline_short_examples": dict(list(short.items())[:10]),
            "rows": B.rows_statistics(session_db, rule=rule)}
