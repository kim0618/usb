"""The concrete SessionSource: one session's SymbolSessions, filled completely, from the local mirror.

The engine reads nothing but ``SymbolSession`` and several of its fields have defaults that fail
silently: an empty ``rvol_history`` makes every scan RVOL_UNKNOWN (no candidate ever opens), empty
``corporate_action_flags`` pass every eligibility check, and ``scope=None`` rejects everything.
So this module never relies on a default. ``complete_symbol_session`` takes every field as a
required keyword and refuses a missing or wrongly typed one.

What each field is, and where the decision behind it lives:

* ``tape``: the session's actual bars from the cache, never filled or synthetic.
* ``scope``: the universe artifact says the symbol is in scope on D (D1). The ScopeDecision is
  built directly with ``included=True``; nothing here recomputes scope.
* ``rvol_history``: minute profiles of the covered prior sessions, excluding data-gap sessions
  (D6). A profile is built once and reused; D's own profile only exists for D+1 onwards.
* ``splits``: records executed on or before D, raw. The pure layer applies them.
* ``corporate_action_flags``: ``derive_corporate_action_flags`` with the first-appearance count
  (D3) and D-1's HYBRID-S verdict against grouped daily (D2). Delisting and symbol-change inputs are
  NOT_APPLIED (D4) and are passed as empty on purpose, recorded as a limitation.

A (symbol, session) the facts classify as a data-quality exclusion (D5) is withheld: it is not
handed to the engine, and it is logged. It is never imputed and never replayed as a quiet day.
"""

from collections.abc import Sequence
from datetime import date
import statistics

from app.backtest.strategy_b.dataset import boundaries_for
from app.backtest.strategy_b.engine import SymbolSession
from app.backtest.strategy_b_e0.dataset_facts import LocalDatasetFacts
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.corporate_actions import PriorSessionAudit, derive_corporate_action_flags
from app.strategy_b.features import SessionTape
from app.strategy_b.models import CorporateActionFlag
from app.strategy_b.rvol import VolumeProfile, build_volume_profile
from app.strategy_b.scope import ScopeDecision
from app.strategy_b.session import SessionBoundaries
from app.strategy_b.sparse_session import validate_sparse_session
from app.strategy_b.split_adjustment import SplitRecord, known_splits

SOURCE_VERSION = "b-e0-local-session-source-v1"


class MissingSessionInput(ValueError):
    """A SymbolSession field was not supplied, or not in the form the engine requires."""


class NotInScope(ValueError):
    """The runner asked for a symbol the universe artifact does not have in scope on that session."""


def complete_symbol_session(*, tape: SessionTape, scope: ScopeDecision,
                            rvol_history: tuple[VolumeProfile, ...], splits: tuple[SplitRecord, ...],
                            corporate_action_flags: frozenset[CorporateActionFlag]) -> SymbolSession:
    """Build a SymbolSession with every field stated. No field may fall back to a default."""
    if not isinstance(tape, SessionTape):
        raise MissingSessionInput("tape must be a SessionTape")
    if not isinstance(scope, ScopeDecision):
        raise MissingSessionInput("scope must be a ScopeDecision; None rejects every candidate")
    if scope.symbol != tape.symbol or scope.decision_date != tape.boundaries.session_date:
        raise MissingSessionInput("scope and tape describe different symbol-sessions")
    if not isinstance(rvol_history, tuple) or not all(isinstance(p, VolumeProfile) for p in rvol_history):
        raise MissingSessionInput("rvol_history must be a tuple of VolumeProfile (possibly empty "
                                  "only when no covered prior session exists)")
    if any(p.session_date >= tape.boundaries.session_date for p in rvol_history):
        raise MissingSessionInput("rvol_history may only hold sessions before D")
    if not isinstance(splits, tuple) or not all(isinstance(s, SplitRecord) for s in splits):
        raise MissingSessionInput("splits must be a tuple of SplitRecord")
    if any(s.execution_date > tape.boundaries.session_date for s in splits):
        raise MissingSessionInput("splits may only hold records executed by D")
    if not isinstance(corporate_action_flags, frozenset):
        raise MissingSessionInput("corporate_action_flags must be an explicit frozenset")
    return SymbolSession(tape=tape, scope=scope, rvol_history=rvol_history, splits=splits,
                         corporate_action_flags=corporate_action_flags)


class LocalSessionSource:
    """SessionSource over the local cache. Sessions must be asked for in date order."""

    def __init__(self, facts: LocalDatasetFacts, *, dataset_digest: str,
                 config: StrategyBConfig | None = None) -> None:
        self.facts = facts
        self.cache = facts.cache
        self.universe = facts.universe
        self.calendar = facts.calendar
        self.grouped = facts.grouped
        self.splits = facts.splits
        self.config = config or facts.config
        self._digest = dataset_digest
        self._quality = {(e.symbol, e.session) for e in facts.quality_exclusions()}
        self._bounds: dict[date, SessionBoundaries] = {}
        self._previous: dict[date, date] = {}
        self._bars: dict[tuple[str, date], tuple] = {}
        self._profiles: dict[tuple[str, date], VolumeProfile] = {}
        self._last_session: date | None = None
        self.withheld: list[dict] = []
        self.dynamic: list[dict] = []

    # ---- protocol --------------------------------------------------------------------------

    def boundaries(self, session: date) -> SessionBoundaries:
        if session not in self._bounds:
            self._bounds[session] = boundaries_for(self.calendar, session)
        return self._bounds[session]

    def dataset_identity(self) -> str:
        return f"{SOURCE_VERSION}:{self._digest[:16]}"

    def dataset_digest(self) -> str:
        return self._digest

    def symbol_sessions(self, session: date, symbols: Sequence[str]) -> list[SymbolSession]:
        if self._last_session is not None and session <= self._last_session:
            raise ValueError(f"sessions must be requested in order; {session} after {self._last_session}")
        out: list[SymbolSession] = []
        withheld = 0
        verdicts: dict[str, int] = {}
        flag_counts: dict[str, int] = {}
        baselines: list[int] = []
        rows = 0
        for symbol in sorted(symbols):
            member = self.universe.members.get(symbol)
            if member is None or session not in member.scope_sessions:
                raise NotInScope(f"{symbol} is not in the universe artifact's scope on {session}")
            if (symbol, session) in self._quality:
                withheld += 1
                self.withheld.append({"symbol": symbol, "session": session.isoformat(),
                                      "kind": "DATA_QUALITY_EXCLUSION",
                                      "reasons": ["API_LOSS_SUSPECT", "MINUTE_TAPE_EMPTY"]})
                continue
            tape = self._tape(symbol, session)
            rows += len(tape.bars)
            history = tuple(self._profile(symbol, day)
                            for day in self.facts.baseline_sessions(symbol, session))
            baselines.append(len(history))
            records = self.splits.records(symbol)
            flags, verdict = self._flags(symbol, session, records)
            if verdict is not None:
                verdicts[verdict] = verdicts.get(verdict, 0) + 1
            for flag in flags:
                flag_counts[str(flag)] = flag_counts.get(str(flag), 0) + 1
            out.append(complete_symbol_session(
                tape=tape,
                scope=ScopeDecision(symbol, session, True, (), None, None, 0),
                rvol_history=history,
                splits=known_splits(records, session),
                corporate_action_flags=flags))
        self._last_session = session
        self._prune(session)
        self.dynamic.append({
            "session": session.isoformat(), "requested": len(symbols), "returned": len(out),
            "withheld_quality": withheld, "rows_loaded": rows,
            "baseline_profiles_min": min(baselines) if baselines else None,
            "baseline_profiles_median": statistics.median(baselines) if baselines else None,
            "below_minimum": sum(1 for b in baselines if b < self.config.rvol.min_partial_sessions),
            "prior_audit_verdicts": dict(sorted(verdicts.items())),
            "corporate_action_flags": dict(sorted(flag_counts.items())),
        })
        return out

    # ---- internals -------------------------------------------------------------------------

    def _load(self, symbol: str, day: date) -> tuple:
        key = (symbol, day)
        if key not in self._bars:
            self._bars[key] = self.cache.bars(symbol, day, self.boundaries(day))
        return self._bars[key]

    def _tape(self, symbol: str, day: date) -> SessionTape:
        return SessionTape(symbol, self.boundaries(day), list(self._load(symbol, day)))

    def _profile(self, symbol: str, day: date) -> VolumeProfile:
        key = (symbol, day)
        if key not in self._profiles:
            self._profiles[key] = build_volume_profile(self._tape(symbol, day), self.config.rvol.scope)
        return self._profiles[key]

    def _previous_session(self, session: date) -> date:
        if session not in self._previous:
            self._previous[session] = self.calendar.previous_trading_day(session)
        return self._previous[session]

    def _flags(self, symbol: str, session: date, records: tuple[SplitRecord, ...]):
        """CA flags for D from inputs dated before D (the D-1 audit) and on D (split day)."""
        previous = self._previous_session(session)
        audit, verdict_name = None, None
        if self.cache.coverage(symbol, previous) is not None:
            daily = self.grouped.official(symbol, previous)
            if daily is not None:
                result = validate_sparse_session(
                    self._load(symbol, previous), daily, self.boundaries(previous),
                    splits=known_splits(records, previous), config=self.config.sparse_validation)
                audit = PriorSessionAudit(previous, result.verdict)
                verdict_name = str(result.verdict)
        flags = derive_corporate_action_flags(
            session, config=self.config.corporate_actions,
            splits=known_splits(records, session),
            prior_sessions_since_listing=self.facts.ipo_prior_sessions(symbol, session),
            delisting_notices=(), symbol_changes=(),  # D4: NOT_APPLIED, recorded as a limitation
            prior_audit=audit)
        return frozenset(flags), verdict_name

    def _prune(self, session: date) -> None:
        """Keep only what a later session can still ask for: the lookback window and D itself."""
        keep_from = min(self.facts.warmup_window(session) or (session,))
        self._bars = {k: v for k, v in self._bars.items() if k[1] >= session}
        self._profiles = {k: v for k, v in self._profiles.items() if k[1] >= keep_from}
