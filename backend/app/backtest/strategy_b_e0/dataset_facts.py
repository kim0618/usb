"""What the stored data can and cannot support, decided from the coverage index without loading bars.

This is data quality, not strategy data. Nothing here computes a feature, looks at a return or
decides a trade. It classifies (symbol, session) pairs into four kinds, and the preflight turns the
counts into a readiness status:

* **covered**: a COMPLETE ledger's range includes the session.
* **DATA_QUALITY_EXCLUSION**: covered, in scope, grouped daily shows volume, yet the regular tape is
  empty. The classification is the pure layer's own HYBRID-S verdict (``validate_sparse_session``
  returns API_LOSS_SUSPECT with MINUTE_TAPE_EMPTY for exactly this), reused rather than restated.
  The pair is withheld from the replay and never imputed (D5).
* **legitimate empty**: covered, empty tape, and grouped daily shows no volume or no row. It is a
  quiet day and is replayed as one.
* **POLICY_EXCLUSION**: data is fine, but a declared policy removes the pair, e.g. IPO_WARMUP from
  the first-appearance proxy (D3). These never count toward the data-quality gate.

A warmup session that is a data gap is excluded from the RVOL baseline rather than read as zero
volume (D6), because a zero read would lower the baseline and inflate RVOL.

Scope is never recomputed here. Membership is the universe artifact's, full stop (D1).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.backtest.strategy_b.dataset import boundaries_for
from app.backtest.strategy_b.preparation import warmup_dates
from app.backtest.strategy_b_e0.market_inputs import GroupedDaily, SplitSource
from app.backtest.strategy_b_e0.session_cache import SessionCache
from app.backtest.strategy_b_e0.universe import RunUniverse
from app.market.calendar import MarketCalendar
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.models import SessionVerdict
from app.strategy_b.sparse_session import ValidationFinding, validate_sparse_session

DATA_QUALITY = "DATA_QUALITY_EXCLUSION"
POLICY = "POLICY_EXCLUSION"
IPO_REASON = "IPO_WARMUP"
IPO_SOURCE = "FIRST_APPEARANCE_PROXY"
IPO_POLICY_VERSION = "b-e0-ipo-first-appearance-v1"
GAP_REASONS = ("API_LOSS_SUSPECT", "MINUTE_TAPE_EMPTY")
BASELINE_GAP_REASON = "MISSING_FULL_SESSION"


@dataclass(frozen=True, slots=True)
class Exclusion:
    symbol: str
    session: date
    kind: str
    reasons: tuple[str, ...]
    detail: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"symbol": self.symbol, "session": self.session.isoformat(), "kind": self.kind,
                "reasons": list(self.reasons), **dict(self.detail)}


class LocalDatasetFacts:
    """Static facts over the mirror + cache + market inputs. Deterministic for the same inputs."""

    def __init__(self, *, universe: RunUniverse, cache: SessionCache, grouped: GroupedDaily,
                 splits: SplitSource, calendar: MarketCalendar, manifest: Mapping,
                 mirror_problems: Sequence[str], config: StrategyBConfig | None = None) -> None:
        self.universe = universe
        self.cache = cache
        self.grouped = grouped
        self.splits = splits
        self.calendar = calendar
        self.manifest = manifest
        self.mirror_problems = tuple(mirror_problems)
        self.config = config or StrategyBConfig()
        self._warmup: dict[date, tuple[date, ...]] = {}
        self._gap_days = self._find_gap_days()

    def warmup_window(self, session: date) -> tuple[date, ...]:
        """The lookback sessions before ``session``, memoized: the calendar is slow and the same
        84 sessions are asked about hundreds of thousands of times."""
        if session not in self._warmup:
            self._warmup[session] = tuple(
                warmup_dates(self.calendar, session, self.config.rvol.lookback_sessions))
        return self._warmup[session]

    # ---- classification -------------------------------------------------------------------

    def _is_gap(self, symbol: str, session: date) -> bool | None:
        """True: data gap. False: fine or legitimately empty. None: not covered."""
        row = self.cache.coverage(symbol, session)
        if row is None:
            return None
        if row.regular_rows > 0:
            return False
        daily = self.grouped.official(symbol, session)
        if daily is None:
            return False  # no grouped row to contradict an empty tape: legitimately empty
        verdict = validate_sparse_session((), daily, boundaries_for(self.calendar, session),
                                          splits=(), config=self.config.sparse_validation)
        return (verdict.verdict is SessionVerdict.API_LOSS_SUSPECT
                and ValidationFinding.MINUTE_TAPE_EMPTY in verdict.findings)

    def _find_gap_days(self) -> frozenset[tuple[str, date]]:
        """Every covered (symbol, session) in any member's fetch range that is a data gap."""
        gaps = set()
        for symbol in self.universe.symbols:
            member = self.universe.members[symbol]
            for session in self.cache.sessions:
                if member.fetch_start <= session <= member.fetch_end and self._is_gap(symbol, session):
                    gaps.add((symbol, session))
        return frozenset(gaps)

    def gap_days(self) -> frozenset[tuple[str, date]]:
        return self._gap_days

    def ipo_prior_sessions(self, symbol: str, session: date) -> int | None:
        """The value handed to derive_corporate_action_flags as prior_sessions_since_listing."""
        return self.grouped.sessions_since_first_appearance(symbol, session)

    def quality_exclusions(self) -> tuple[Exclusion, ...]:
        out = []
        for symbol, session in self.universe.required_pairs():
            if (symbol, session) in self._gap_days:
                out.append(Exclusion(symbol, session, DATA_QUALITY, GAP_REASONS, {
                    "grouped_volume": self.grouped.volume_on(symbol, session),
                    "minute_regular_rows": 0}))
        return tuple(sorted(out, key=lambda e: (e.session, e.symbol)))

    def policy_exclusions(self) -> tuple[Exclusion, ...]:
        threshold = self.config.corporate_actions.ipo_warmup_sessions
        out = []
        for symbol, session in self.universe.required_pairs():
            prior = self.ipo_prior_sessions(symbol, session)
            if prior is not None and prior < threshold:
                out.append(Exclusion(symbol, session, POLICY, (IPO_REASON,), {
                    "first_appearance": str(self.grouped.first_appearance(symbol)),
                    "prior_sessions_since_first_appearance": prior,
                    "source": IPO_SOURCE, "policy_version": IPO_POLICY_VERSION}))
        return tuple(sorted(out, key=lambda e: (e.session, e.symbol)))

    def baseline_sessions(self, symbol: str, session: date) -> tuple[date, ...]:
        """The prior sessions whose profiles may enter D's RVOL baseline: covered and not a gap."""
        return tuple(day for day in self.warmup_window(session)
                     if self.cache.coverage(symbol, day) is not None
                     and (symbol, day) not in self._gap_days)

    def warmup_gaps_in_use(self) -> tuple[dict, ...]:
        """Gap days that actually sit inside some scope session's baseline window (e.g. BACC)."""
        affected: dict[tuple[str, date], list[date]] = {}
        for symbol, session in self.universe.required_pairs():
            for day in self.warmup_window(session):
                if (symbol, day) in self._gap_days:
                    affected.setdefault((symbol, day), []).append(session)
        return tuple({"symbol": s, "gap_session": d.isoformat(), "reason": BASELINE_GAP_REASON,
                      "scope_sessions_affected": len(v), "first_affected": v[0].isoformat(),
                      "last_affected": v[-1].isoformat()}
                     for (s, d), v in sorted(affected.items()))

    def warmup_below_minimum(self) -> tuple[tuple[str, date, int], ...]:
        """Scope pairs whose usable baseline is under min_partial_sessions: RVOL will be UNKNOWN."""
        minimum = self.config.rvol.min_partial_sessions
        out = []
        for symbol, session in self.universe.required_pairs():
            usable = len(self.baseline_sessions(symbol, session))
            if usable < minimum:
                out.append((symbol, session, usable))
        return tuple(out)

    def warmup_partial_pairs(self) -> int:
        lookback = self.config.rvol.lookback_sessions
        minimum = self.config.rvol.min_partial_sessions
        return sum(1 for symbol, session in self.universe.required_pairs()
                   if minimum <= len(self.baseline_sessions(symbol, session)) < lookback)

    # ---- the preflight's DatasetFacts protocol ---------------------------------------------

    def missing_scope_pairs(self, pairs):
        return tuple((s, d) for s, d in pairs if self.cache.coverage(s, d) is None)

    def symbols_missing_warmup(self, universe, sessions: int):
        """Symbols with an uncovered session anywhere in their fetch range (warmup included)."""
        uncovered = self.cache.meta.get("uncovered_sessions") or {}
        missing = set(uncovered)
        missing |= set(self.manifest.get("members_without_ledgers") or ())
        return tuple(sorted(s for s in missing if s in universe.members))

    def symbols_missing_splits(self, symbols):
        """The dump covers every ticker; a symbol without records has no split. Only a missing
        or unreadable dump makes a symbol's split history unknown."""
        return () if self.splits.records_read > 0 else tuple(sorted(symbols))

    def sessions_missing_daily(self, sessions):
        wanted = set(sessions) | set(self.cache.sessions)
        return tuple(sorted(d for d in self.grouped.missing_sessions if d in wanted))

    def schema_mismatches(self):
        return {f"ledger:{i}": problem
                for i, problem in enumerate(self.manifest.get("ledger_schema_problems") or ())}

    def checksum_mismatches(self, pairs):
        return self.mirror_problems

    def collection_completeness(self):
        required = len(self.universe.required_pairs())
        covered = sum(1 for s, d in self.universe.required_pairs()
                      if self.cache.coverage(s, d) is not None)
        return {"required_pairs": required, "covered_pairs": covered,
                "completeness_pct": round(100.0 * covered / required, 6) if required else 0.0,
                "absent_symbols": sorted(self.universe.exclusions)}

    # ---- extra checks the preflight reads when present -------------------------------------

    def calendar_mismatches(self) -> tuple[str, ...]:
        from app.dev.fetch_strategy_c_selection_raw import sessions_between
        expected = sessions_between(self.calendar, self.universe.scope_start, self.universe.scope_end)
        if tuple(expected) == self.universe.sessions:
            return ()
        return (f"calendar has {len(expected)} sessions, universe has {len(self.universe.sessions)}",)

    def early_close_sessions(self) -> tuple[date, ...]:
        return tuple(d for d in self.cache.sessions if self.calendar.is_early_close(d))

    def split_conflicts_in_universe(self) -> tuple[str, ...]:
        return tuple(f"{t} {d}" for (t, d) in sorted(self.splits.conflicts_for(self.universe.symbols)))

    def warnings(self) -> tuple[str, ...]:
        out = []
        quality = self.quality_exclusions()
        if quality:
            out.append(f"{len(quality)} data-quality pair exclusions")
        gaps = self.warmup_gaps_in_use()
        if gaps:
            out.append(f"{len(gaps)} warmup gap sessions excluded from RVOL baselines")
        policy = self.policy_exclusions()
        if policy:
            out.append(f"{len(policy)} IPO_WARMUP policy exclusions ({IPO_SOURCE})")
        below = self.warmup_below_minimum()
        if below:
            out.append(f"{len(below)} scope pairs with RVOL baseline below the minimum")
        if self.splits.conflicts:
            out.append(f"{len(self.splits.conflicts)} conflicting split keys dropped "
                       f"({len(self.split_conflicts_in_universe())} in the universe)")
        out.append("DELISTING_WINDOW and SYMBOL_CHANGE are NOT_APPLIED (no PIT source)")
        return tuple(out)

    def limitations(self) -> tuple[str, ...]:
        return (
            "DELISTING_EVENT_FILTER = NOT_APPLIED: no point-in-time delisting announcement source",
            "TICKER_CHANGE_EVENT_FILTER = NOT_APPLIED: no symbol-change event source",
            f"IPO_WARMUP source = {IPO_SOURCE}: sessions since first grouped-daily appearance, "
            "not an authoritative list_date",
            "Daily authority = MASSIVE_GROUPED_DAILY for B-E0 (Strategy A does not use it)",
        )
