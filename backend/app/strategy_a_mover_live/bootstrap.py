"""The Massive half of A's denominator: the frozen local minute tape, read to A's own cut.

A's twenty-session denominator has to exist on the morning Paper starts, and Kiwoom cannot
supply it on that morning: A's rows accumulate one session per day from the shared collector,
so a Kiwoom-only denominator is twenty trading days of waiting, or the 60-plus-hour historical
collection ``backfill`` plans. Neither is what the strategy is waiting for. The sessions behind
today are already on this machine - the same Massive minute tape the V1.2 research arm was
measured on - so the baseline's older half is read from there and replaced by Kiwoom as Kiwoom
observations arrive.

**What makes this a bootstrap and not a second contract.** The quantity is A's and only A's:
premarket *share* volume summed over ``[04:00, 09:15)``, over the session's real ET instants.
It is produced by ``mover_scanner_v1.premarket.build_panel`` - the research arm's own function,
at the research arm's own cut - rather than by an arithmetic restated here, so a bootstrap
session and a Kiwoom session are the same measurement of different bars. That they are
different bars is the whole reason the provider of each session travels with the median: the
parity audit put the two sources' premarket dollar-volume Spearman at 0.822, so a denominator
is a mix and says so rather than presenting itself as one source's.

**Coverage is the tape's ledger, never an assumption.** ``SessionCache.coverage`` answers for a
(symbol, session) pair or returns None, and only ``R.select_cache``'s complete cache - one with
no uncovered symbol-session - is read, so a quiet session and an unfetched one cannot be
confused. A covered session whose premarket held no print contributes a zero, which is A's
stated rule and the reason a symbol that normally does not trade premarket can read as abnormal
on the morning it does. A pair the tape does not cover produces **no row at all** and is
reported by name: ``SESSION_NOT_IN_TAPE``, ``SYMBOL_NOT_IN_TAPE`` or
``SYMBOL_SESSION_NOT_COVERED``. Nothing here turns absent coverage into a zero.

**No network, ever.** :func:`materialize` reads the tape and writes durable rows; it holds no
provider and makes no request. The rows go into ``premarket_volume_sessions`` under their own
identity - ``source = MASSIVE_MINUTE_TAPE_PM0915``, ``collector_version =
a_mover_pm_volume_v1_bootstrap`` - beside A's Kiwoom rows and the V2 rows, so no row is
reinterpreted, no migration is needed and the provider of every session in a median is a stored
fact rather than an inference. The write goes through the V2 service's own idempotent upsert,
so running it twice writes the same rows once.

**Why the tape is read ahead of the cut rather than at it.** A's cut has about five minutes
before E's own finalization, and a panel over the twenty-session grid is tens of thousands of
memory-mapped slices. Materializing beforehand turns the 09:15 read back into the single
indexed query ``baseline`` already does, and a past session's premarket volume does not change,
so there is nothing for the cut to recompute.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.backtest.mover_scanner_v1 import premarket as P
from app.backtest.mover_scanner_v1 import run as R
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.strategy_b_e0.session_cache import SessionCache
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol
from app.services import premarket_volume_history as V
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live.schedule import A_CUT_MINUTE

#: Recorded on every bootstrap row, so a reader sees which pass wrote it.
QUALITY_REASON = "MASSIVE_BOOTSTRAP_AT_A_CUT"


class TapeUnavailable(RuntimeError):
    """No complete local minute tape exists, so the bootstrap half cannot be read."""


class Coverage(StrEnum):
    """Why one (symbol, session) pair has a value, or precisely why it has none."""

    COVERED = "COVERED"
    #: The tape holds this session but not this symbol in it.
    SYMBOL_SESSION_NOT_COVERED = "SYMBOL_SESSION_NOT_COVERED"
    #: The tape's session range does not include this session at all.
    SESSION_NOT_IN_TAPE = "SESSION_NOT_IN_TAPE"
    #: The symbol is not a member of the tape's universe.
    SYMBOL_NOT_IN_TAPE = "SYMBOL_NOT_IN_TAPE"


@dataclass(frozen=True)
class Observation:
    """One bootstrap reading. ``volume`` is None unless ``coverage`` is COVERED."""

    symbol: str
    session: date
    coverage: Coverage
    volume: float | None
    bar_count: int

    @property
    def covered(self) -> bool:
        return self.coverage is Coverage.COVERED

    def declaration(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "session": self.session.isoformat(),
                "coverage": str(self.coverage), "volume": self.volume,
                "bar_count": self.bar_count}


@dataclass(frozen=True)
class Tape:
    """The selected local tape and what it covers. Read-only; the digest travels on a report."""

    directory: Path
    digest: str
    sessions: tuple[date, ...]
    symbols: frozenset[str]
    cache: SessionCache = field(repr=False)

    @property
    def oldest_session(self) -> date | None:
        return self.sessions[0] if self.sessions else None

    @property
    def newest_session(self) -> date | None:
        return self.sessions[-1] if self.sessions else None

    def declaration(self) -> dict[str, Any]:
        return {"directory": str(self.directory), "cache_digest": self.digest,
                "sessions": len(self.sessions), "symbols": len(self.symbols),
                "oldest_session": self.oldest_session.isoformat() if self.sessions else None,
                "newest_session": self.newest_session.isoformat() if self.sessions else None}


def select_tape(repo: Path, *, cache_directory: Path | None = None) -> Tape:
    """The complete minute tape to bootstrap from, or a named refusal.

    ``select_cache`` accepts only a cache with no uncovered symbol-session, which is what makes
    "the tape did not cover this pair" a statement about the pair rather than about the cache.
    """
    try:
        selected = R.select_cache(Path(repo), cache_directory)
    except R.StudyHardFail as error:
        raise TapeUnavailable(str(error)) from error
    cache = SessionCache(selected.directory)
    return Tape(directory=selected.directory, digest=selected.digest,
                sessions=tuple(cache.sessions), symbols=frozenset(cache.symbols), cache=cache)


def observe(tape: Tape, symbols: Sequence[str], sessions: Sequence[date], *,
            config: MoverScannerConfig | None = None) -> dict[tuple[str, date], Observation]:
    """A's premarket share volume per pair over the grid, by the research arm's own function.

    One ``build_panel`` pass over exactly the requested grid; a pair outside the tape never
    reaches the panel and is answered from the ledger instead, so an absent pair is named
    rather than read as a zero column.
    """
    config = config or MoverScannerConfig()
    if config.scan_cut_minute != A_CUT_MINUTE:
        raise ValueError("the bootstrap must be read at A's declared cut, "
                         f"not minute {config.scan_cut_minute}")
    wanted = [normalize_symbol(symbol) for symbol in symbols]
    in_tape = set(tape.sessions)
    in_tape_sessions = [day for day in sessions if day in in_tape]
    members = [symbol for symbol in wanted if symbol in tape.symbols]
    out: dict[tuple[str, date], Observation] = {}
    for symbol in wanted:
        for day in sessions:
            if symbol not in tape.symbols:
                out[(symbol, day)] = Observation(symbol, day, Coverage.SYMBOL_NOT_IN_TAPE,
                                                 None, 0)
            elif day not in in_tape:
                out[(symbol, day)] = Observation(symbol, day, Coverage.SESSION_NOT_IN_TAPE,
                                                 None, 0)
    if not members or not in_tape_sessions:
        return out
    panel = P.build_panel(tape.cache, config, sessions=tuple(in_tape_sessions),
                          symbols=tuple(members))
    rows, columns = panel.symbol_index, panel.session_index
    for symbol in members:
        row = rows[symbol]
        for day in in_tape_sessions:
            column = columns[day]
            if not bool(panel.covered[row, column]):
                out[(symbol, day)] = Observation(
                    symbol, day, Coverage.SYMBOL_SESSION_NOT_COVERED, None, 0)
                continue
            volume = float(panel.values["pm_volume"][row, column])
            bars = float(panel.values["pm_bars"][row, column])
            if not np.isfinite(volume):
                # A covered pair with a non-finite sum is a cache defect, not a quiet zero.
                out[(symbol, day)] = Observation(
                    symbol, day, Coverage.SYMBOL_SESSION_NOT_COVERED, None, 0)
                continue
            out[(symbol, day)] = Observation(symbol, day, Coverage.COVERED, volume,
                                             int(bars) if np.isfinite(bars) else 0)
    return out


def session_volume(observation: Observation, exchange: str, *, collected_at: datetime,
                   rule: B.BaselineRule | None = None) -> V.SessionVolume:
    """One covered observation as the durable record the V2 upsert takes.

    ``regular_bar_count`` is 0 because A's window ends at the cut and nothing after it is read;
    ``quality_reason`` names the pass, so a row never claims a regular-session observation it
    did not make. A covered pair with no print by the cut is ``NO_PREMARKET_BARS`` with a zero,
    which ``baseline.usable_volume`` reads as the contract's zero rather than as a gap.
    """
    if not observation.covered:
        raise ValueError(f"{observation.symbol} {observation.session} is "
                         f"{observation.coverage}; only a covered pair becomes a row")
    rule = rule or B.BaselineRule()
    quality = (V.SessionQuality.COMPLETE if observation.bar_count > 0
               else V.SessionQuality.NO_PREMARKET_BARS)
    return V.SessionVolume(
        observation.symbol, exchange, observation.session, rule.bootstrap_source,
        rule.bootstrap_collector_version, quality, QUALITY_REASON,
        int(round(observation.volume or 0.0)), observation.bar_count, 0, None, None, None, None,
        collected_at)


@dataclass(frozen=True)
class MaterializeReport:
    """What one bootstrap materialization wrote, and every pair it refused to invent."""

    tape: dict[str, Any]
    entry_session_date: date
    requested_symbols: int
    requested_sessions: tuple[date, ...]
    written: int
    inserted: int
    updated: int
    kept_complete: int
    covered_pairs: int
    uncovered: dict[str, int]
    uncovered_examples: tuple[dict[str, Any], ...]

    def declaration(self) -> dict[str, Any]:
        return {
            "stage": "A_MOVER_BOOTSTRAP_MATERIALIZE",
            "tape": self.tape,
            "entry_session_date": self.entry_session_date.isoformat(),
            "requested_symbols": self.requested_symbols,
            "requested_sessions": [day.isoformat() for day in self.requested_sessions],
            "requested_pairs": self.requested_symbols * len(self.requested_sessions),
            "covered_pairs": self.covered_pairs,
            "rows_written": self.written,
            "inserted": self.inserted, "updated": self.updated,
            "kept_complete": self.kept_complete,
            "uncovered_pairs": dict(sorted(self.uncovered.items())),
            "uncovered_examples": [dict(item) for item in self.uncovered_examples],
            "network_requests": 0,
            "provider": "MASSIVE_LOCAL_MINUTE_TAPE",
        }


def materialize(session: Session, repo: Path, candidates: Iterable[tuple[str, str]],
                entry_session_date: date, *, calendar: MarketCalendar | None = None,
                rule: B.BaselineRule | None = None,
                config: MoverScannerConfig | None = None,
                cache_directory: Path | None = None,
                clock: Callable[[], datetime] | None = None,
                tape: Tape | None = None,
                examples: int = 20) -> MaterializeReport:
    """Write the bootstrap rows A's denominator may read for ``entry_session_date``.

    The sessions are A's own lookback window, so this writes exactly what the covered walk can
    reach and nothing beyond it. Pairs the tape does not cover are counted and named; they are
    never written, and a symbol with too few covered sessions stays
    ``INSUFFICIENT_COVERED_SESSIONS`` at read time instead of acquiring invented zeros.
    """
    rule = rule or B.BaselineRule()
    calendar = calendar or MarketCalendar()
    clock = clock or (lambda: datetime.now(timezone.utc))
    tape = tape if tape is not None else select_tape(repo, cache_directory=cache_directory)
    days = tuple(reversed(B.lookback_sessions(calendar, entry_session_date,
                                              limit=rule.max_lookback_sessions)))
    pairs = [(normalize_symbol(symbol), exchange) for symbol, exchange in candidates]
    exchange_of = dict(pairs)
    observations = observe(tape, [symbol for symbol, _ in pairs], days, config=config)
    service = V.PremarketVolumeHistoryService(
        session, None, calendar=calendar, clock=clock, source_name=rule.bootstrap_source,
        collector_version=rule.bootstrap_collector_version)
    now = clock()
    counts = {V.HistoryWrite.INSERTED: 0, V.HistoryWrite.UPDATED: 0,
              V.HistoryWrite.KEPT_COMPLETE: 0}
    uncovered: dict[str, int] = {}
    shown: list[dict[str, Any]] = []
    covered = 0
    for (symbol, day), observation in sorted(observations.items(),
                                             key=lambda item: (item[0][0], item[0][1])):
        if not observation.covered:
            name = str(observation.coverage)
            uncovered[name] = uncovered.get(name, 0) + 1
            if len(shown) < examples:
                shown.append(observation.declaration())
            continue
        covered += 1
        outcome = service.upsert(session_volume(observation, exchange_of.get(symbol, ""),
                                                collected_at=now, rule=rule))
        counts[outcome] = counts.get(outcome, 0) + 1
    session.commit()
    written = counts[V.HistoryWrite.INSERTED] + counts[V.HistoryWrite.UPDATED]
    return MaterializeReport(
        tape=tape.declaration(), entry_session_date=entry_session_date,
        requested_symbols=len(pairs), requested_sessions=days, written=written,
        inserted=counts[V.HistoryWrite.INSERTED], updated=counts[V.HistoryWrite.UPDATED],
        kept_complete=counts[V.HistoryWrite.KEPT_COMPLETE], covered_pairs=covered,
        uncovered=uncovered, uncovered_examples=tuple(shown))


def coverage_of(repo: Path, candidates: Iterable[tuple[str, str]], entry_session_date: date, *,
                calendar: MarketCalendar | None = None, rule: B.BaselineRule | None = None,
                config: MoverScannerConfig | None = None,
                cache_directory: Path | None = None,
                tape: Tape | None = None) -> dict[str, Any]:
    """How much of the bootstrap half the tape can supply, without writing anything.

    This is the honest answer to "can Paper start": it reports, per symbol, how many of A's
    twenty sessions the tape covers, so a denominator that would come up short is visible
    before a run rather than as a refusal at the cut.
    """
    rule = rule or B.BaselineRule()
    calendar = calendar or MarketCalendar()
    tape = tape if tape is not None else select_tape(repo, cache_directory=cache_directory)
    days = tuple(reversed(B.lookback_sessions(calendar, entry_session_date,
                                              limit=rule.max_lookback_sessions)))
    symbols = [normalize_symbol(symbol) for symbol, _ in candidates]
    observations = observe(tape, symbols, days, config=config)
    per_symbol = {symbol: sum(1 for day in days if observations[(symbol, day)].covered)
                  for symbol in symbols}
    enough = [symbol for symbol, count in per_symbol.items() if count >= rule.sessions]
    reasons: dict[str, int] = {}
    for observation in observations.values():
        if not observation.covered:
            reasons[str(observation.coverage)] = reasons.get(str(observation.coverage), 0) + 1
    return {
        "tape": tape.declaration(),
        "entry_session_date": entry_session_date.isoformat(),
        "lookback_sessions": [day.isoformat() for day in days],
        "sessions_required": rule.sessions,
        "symbols": len(symbols),
        "symbols_with_enough_bootstrap_sessions": len(enough),
        "symbols_short": len(symbols) - len(enough),
        "covered_sessions_per_symbol": dict(sorted(per_symbol.items())),
        "uncovered_reasons": dict(sorted(reasons.items())),
        "sessions_in_window_absent_from_tape": [
            day.isoformat() for day in days if day not in set(tape.sessions)],
        "network_requests": 0,
    }


def rows_statistics(session: Session, *, rule: B.BaselineRule | None = None) -> dict[str, Any]:
    """How much of the bootstrap half is already stored. Read-only."""
    rule = rule or B.BaselineRule()
    return B.rows_statistics(session, rule=rule, provider=B.BaselineProvider.MASSIVE)


def declaration(rule: B.BaselineRule | None = None) -> Mapping[str, Any]:
    """The bootstrap's own identity, for a run record."""
    rule = rule or B.BaselineRule()
    return {"source": rule.bootstrap_source,
            "collector_version": rule.bootstrap_collector_version,
            "quantity": "SHARE_VOLUME", "window_minutes": [240, A_CUT_MINUTE],
            "authority": "MASSIVE_LOCAL_MINUTE_TAPE",
            "built_by": "mover_scanner_v1.premarket.build_panel",
            "network_requests": 0,
            "uncovered_pair_becomes_zero": False}
