"""A_MOVER_PM_VOLUME_V1: A's own 20-session premarket share-volume baseline.

E's ``rvol_store`` values cannot be reused, and the reason is not preference. E's denominator
is premarket *dollar* volume over ``[04:00, 09:24]``, requires a staged 09:30 bar, and takes a
median once at least five sessions exist. A's relative volume is *share* volume over
``[04:00, 09:15)``, counts a covered session with no prints as a zero, needs the full twenty,
and divides by ``max(median, 1,000 shares)``. Those are different numbers from different
windows; dividing A's numerator by E's denominator would produce a ratio that is not either
contract's.

What *is* reused is the machinery, not a new framework:

* the durable table ``premarket_volume_sessions``, whose identity already includes
  ``source`` and ``collector_version`` - so A's rows live beside the V2 rows without either
  reinterpreting the other, and no migration is needed;
* ``PremarketVolumeHistoryService``'s planning, retry classification, idempotent upsert and
  "a COMPLETE session is never overwritten by a worse answer" rule, through a subclass that
  changes exactly two things: the window ends at A's cut, and the planning window is A's.

**A's membership rule, stated once.** The denominator is the median share volume of the last
twenty *covered* sessions strictly before the entry session. Covered means the source answered
for that session: a session with prints contributes its volume, and a session the source
covered with no premarket print contributes zero - a symbol that normally does not trade
premarket must be able to read as abnormal on the morning it does, which a dropped session
would prevent. A session the source could not answer for is not covered and is skipped, and
an older covered session takes its place; the walk is bounded by ``max_lookback_sessions`` and
refuses rather than reaching arbitrarily far back. The entry session itself is never in the
window, because its own premarket volume is the numerator.

The 1,000-share floor is deliberately *not* applied here. It belongs to the scanner's own
``premarket_rvol_baseline_floor_shares`` and is applied at the division inside ``scan``, so
this module returns the median the contract asks for and one config owns the floor.

**The denominator is a provider mix, and the mix is the record.** Twenty Kiwoom sessions do not
exist on the morning Paper starts and cannot be conjured: A's rows arrive one per session from
the shared collector. So the walk asks each session for a Kiwoom observation first and falls
back to the Massive bootstrap row ``bootstrap`` materialized from the frozen local minute tape
for that same session - never the other way round, and never for the scan session itself, whose
premarket volume is the numerator. Twenty *combined* covered sessions is the whole precondition
for a live calculation; which provider supplied each one is recorded, not gated on. Every
result carries ``baseline_session_count``, ``kiwoom_session_count``, ``massive_session_count``
and ``baseline_mode`` (``MASSIVE_BOOTSTRAP`` -> ``MIXED_BOOTSTRAP`` -> ``KIWOOM_NATIVE``), so a
persisted run says what its denominator was made of instead of implying one source.

The replacement is automatic and needs no operator. :func:`record_forward_observations` stores
each morning's own A observation at the cut, so the next session's walk finds a Kiwoom answer
one session newer than yesterday's did; the newest Kiwoom session enters the twenty and the
oldest bootstrap session falls out of it. After at most twenty completed forward sessions the
window holds twenty Kiwoom observations and the mode is ``KIWOOM_NATIVE`` on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol
from app.models.analytics import PremarketVolumeSession
from app.services import premarket_volume_history as V
from app.services.premarket_volume_history import first_complete_session
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live.schedule import A_CUT, A_CUT_MINUTE

#: A's rows: the same minute source as E's, read to A's cut. Part of row identity.
SOURCE = "KIWOOM_USA06011_PM0915"
#: Part of row identity, so A's contract cannot reinterpret a V2 row or be reinterpreted by one.
COLLECTOR_VERSION = "a_mover_pm_volume_v1"
#: The bootstrap half's own identity. A separate source and collector version, so a bootstrap
#: session and a Kiwoom session are distinguishable rows rather than a blended one.
BOOTSTRAP_SOURCE = "MASSIVE_MINUTE_TAPE_PM0915"
BOOTSTRAP_COLLECTOR_VERSION = "a_mover_pm_volume_v1_bootstrap"
BASELINE_IDENTITY = LC.BASELINE_VERSION
BASELINE_METHOD = "MEDIAN"
BASELINE_SESSIONS = MoverScannerConfig().premarket_rvol_baseline_sessions
#: How far back the covered-session walk may reach for those twenty. Declared, not open-ended.
MAX_LOOKBACK_SESSIONS = 40

#: Stored qualities that mean "the source answered for this session".
COVERED_QUALITIES = frozenset({
    V.SessionQuality.COMPLETE.value,
    V.SessionQuality.NO_PREMARKET_BARS.value,
    V.SessionQuality.NO_REGULAR_BARS.value,
})


class BaselineStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    #: Fewer than twenty covered sessions inside the declared lookback, both providers counted.
    INSUFFICIENT_COVERED_SESSIONS = "INSUFFICIENT_COVERED_SESSIONS"
    UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"


class BaselineProvider(StrEnum):
    """Who supplied one session of the median. Stored per session, never averaged away."""

    KIWOOM = "KIWOOM"
    MASSIVE = "MASSIVE"


class BaselineMode(StrEnum):
    """What a whole denominator is made of, as section I's three names.

    Read it with the status: a mode is a statement about the sessions that *were* used, so an
    ``INSUFFICIENT_COVERED_SESSIONS`` result carrying ``MASSIVE_BOOTSTRAP`` is saying that no
    Kiwoom session was among the few it found, not that it has a usable bootstrap denominator.
    """

    #: No Kiwoom session in the window: the denominator is entirely the local tape.
    MASSIVE_BOOTSTRAP = "MASSIVE_BOOTSTRAP"
    #: Both providers present. The ordinary state while Kiwoom history accumulates.
    MIXED_BOOTSTRAP = "MIXED_BOOTSTRAP"
    #: Every session used is a Kiwoom observation. The end state, reached without an operator.
    KIWOOM_NATIVE = "KIWOOM_NATIVE"


def mode_for(kiwoom: int, massive: int) -> BaselineMode:
    """Section I's table, as one function both a symbol and a whole run are classified by."""
    if massive == 0 and kiwoom > 0:
        return BaselineMode.KIWOOM_NATIVE
    if kiwoom == 0:
        return BaselineMode.MASSIVE_BOOTSTRAP
    return BaselineMode.MIXED_BOOTSTRAP


@dataclass(frozen=True)
class BaselineRule:
    """The rule as plain data, so a run record says which denominator it used."""

    identity: str = BASELINE_IDENTITY
    source: str = SOURCE
    collector_version: str = COLLECTOR_VERSION
    window_minutes: tuple[int, int] = (240, A_CUT_MINUTE)
    sessions: int = BASELINE_SESSIONS
    method: str = BASELINE_METHOD
    quantity: str = "SHARE_VOLUME"
    silent_covered_session: str = "CONTRIBUTES_ZERO"
    current_session_in_denominator: bool = False
    max_lookback_sessions: int = MAX_LOOKBACK_SESSIONS
    floor_applied_here: bool = False
    #: The bootstrap half's identity. Off, the walk reads Kiwoom rows only.
    bootstrap_source: str = BOOTSTRAP_SOURCE
    bootstrap_collector_version: str = BOOTSTRAP_COLLECTOR_VERSION
    bootstrap_enabled: bool = True

    def identities(self) -> tuple[tuple[BaselineProvider, str, str], ...]:
        """The row identities the walk may read, in provider priority order."""
        out = [(BaselineProvider.KIWOOM, self.source, self.collector_version)]
        if self.bootstrap_enabled:
            out.append((BaselineProvider.MASSIVE, self.bootstrap_source,
                        self.bootstrap_collector_version))
        return tuple(out)

    def declaration(self) -> dict[str, Any]:
        return {
            "identity": self.identity, "source": self.source,
            "collector_version": self.collector_version,
            "window_minutes": list(self.window_minutes), "sessions": self.sessions,
            "method": self.method, "quantity": self.quantity,
            "silent_covered_session": self.silent_covered_session,
            "current_session_in_denominator": self.current_session_in_denominator,
            "max_lookback_sessions": self.max_lookback_sessions,
            "floor_applied_here": self.floor_applied_here,
            "floor_owner": "MoverScannerConfig.premarket_rvol_baseline_floor_shares",
            "bootstrap_enabled": self.bootstrap_enabled,
            "bootstrap_source": self.bootstrap_source,
            "bootstrap_collector_version": self.bootstrap_collector_version,
            "provider_priority": [str(provider) for provider, _, _ in self.identities()],
            "live_precondition": f"{self.sessions} combined completed covered sessions; "
                                 "the provider mix is recorded, never a block condition",
        }


@dataclass(frozen=True)
class AMoverBaseline:
    """One symbol's denominator for one entry session, with the sessions it is a median over."""

    status: BaselineStatus
    symbol: str
    exchange: str
    entry_session_date: date
    #: The covered sessions used, oldest first. Exactly ``sessions`` long when AVAILABLE.
    used_sessions: tuple[date, ...]
    #: Sessions inside the lookback the source has no final covered answer for, newest first.
    missing_sessions: tuple[date, ...]
    #: Share volume per used session, same order; a silent covered session is 0.
    volumes: tuple[float, ...]
    median_volume: float | None
    rule: BaselineRule = BaselineRule()
    #: Who supplied each used session, same order as ``used_sessions``.
    providers: tuple[BaselineProvider, ...] = ()

    def __post_init__(self) -> None:
        # One provider per used session, always. A denominator that knows its sessions but not
        # who supplied them would let ``kiwoom_session_count`` read 0 for twenty real Kiwoom
        # sessions, which is a false claim rather than a missing one.
        if len(self.providers) != len(self.used_sessions):
            raise ValueError(
                f"{len(self.used_sessions)} used sessions carry {len(self.providers)} "
                "providers; every session in a median names the provider that supplied it")

    @property
    def available(self) -> bool:
        return self.status is BaselineStatus.AVAILABLE

    @property
    def baseline_session_count(self) -> int:
        return len(self.used_sessions)

    @property
    def kiwoom_session_count(self) -> int:
        return sum(1 for item in self.providers if item is BaselineProvider.KIWOOM)

    @property
    def massive_session_count(self) -> int:
        return sum(1 for item in self.providers if item is BaselineProvider.MASSIVE)

    @property
    def mode(self) -> BaselineMode:
        return mode_for(self.kiwoom_session_count, self.massive_session_count)

    @property
    def oldest_used_session(self) -> date | None:
        return self.used_sessions[0] if self.used_sessions else None

    @property
    def newest_used_session(self) -> date | None:
        return self.used_sessions[-1] if self.used_sessions else None

    def provider_sessions(self, provider: BaselineProvider) -> tuple[date, ...]:
        return tuple(day for day, item in zip(self.used_sessions, self.providers, strict=False)
                     if item is provider)

    def declaration(self) -> dict[str, Any]:
        return {"status": str(self.status), "symbol": self.symbol, "exchange": self.exchange,
                "entry_session_date": self.entry_session_date.isoformat(),
                "used_sessions": [day.isoformat() for day in self.used_sessions],
                "covered_sessions": len(self.used_sessions),
                "missing_sessions": [day.isoformat() for day in self.missing_sessions],
                "median_volume": self.median_volume,
                "baseline_session_count": self.baseline_session_count,
                "kiwoom_session_count": self.kiwoom_session_count,
                "massive_session_count": self.massive_session_count,
                "baseline_mode": str(self.mode),
                "providers": [str(item) for item in self.providers],
                "oldest_used_session": (self.oldest_used_session.isoformat()
                                        if self.used_sessions else None),
                "newest_used_session": (self.newest_used_session.isoformat()
                                        if self.used_sessions else None),
                "rule": self.rule.declaration()}


def usable_volume(row: PremarketVolumeSession) -> float | None:
    """A's reading of one stored row: a number for a covered session, None for an uncovered one."""
    if row.quality_status not in COVERED_QUALITIES:
        return None
    if row.quality_status == V.SessionQuality.NO_PREMARKET_BARS.value:
        return 0.0                 # covered, nothing printed: a real zero, not a gap
    return float(row.premarket_volume or 0)


def lookback_sessions(calendar: MarketCalendar, entry_session_date: date,
                      *, limit: int = MAX_LOOKBACK_SESSIONS) -> tuple[date, ...]:
    """The XNYS sessions the covered walk may consider, newest first, never the entry session."""
    if not calendar.is_trading_day(entry_session_date):
        raise ValueError(f"{entry_session_date} is not an XNYS session")
    out: list[date] = []
    day = entry_session_date
    for _ in range(limit):
        day = calendar.previous_trading_day(day)
        out.append(day)
    return tuple(out)


def _stored_rows(session: Session, symbol: str, exchange: str, days: Sequence[date], *,
                 source: str = SOURCE, collector_version: str = COLLECTOR_VERSION,
                 ) -> dict[date, PremarketVolumeSession]:
    rows = session.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.symbol == symbol,
        PremarketVolumeSession.exchange == exchange,
        PremarketVolumeSession.source == source,
        PremarketVolumeSession.collector_version == collector_version,
        PremarketVolumeSession.trading_date.in_(list(days))))
    return {row.trading_date: row for row in rows}


def _by_provider(session: Session, symbol: str, exchange: str, days: Sequence[date],
                 rule: BaselineRule,
                 ) -> dict[BaselineProvider, dict[date, PremarketVolumeSession]]:
    """Every identity the rule may read, each as its own ``day -> row`` map."""
    return {provider: _stored_rows(session, symbol, exchange, days, source=source,
                                   collector_version=version)
            for provider, source, version in rule.identities()}


def _resolve(day: date, by_provider: Mapping[BaselineProvider, Mapping[
        date, PremarketVolumeSession]], rule: BaselineRule,
        ) -> tuple[BaselineProvider, float] | None:
    """One session's answer, in provider priority order. Kiwoom first, always.

    A session Kiwoom has a covered answer for is a Kiwoom session even when a bootstrap row for
    the same session is stored beside it, which is what makes the bootstrap half shrink on its
    own as Kiwoom history arrives.
    """
    for provider, _, _ in rule.identities():
        row = by_provider.get(provider, {}).get(day)
        if row is None:
            continue
        value = usable_volume(row)
        if value is not None:
            return provider, value
    return None


def _walk(symbol: str, exchange: str, entry_session_date: date, days: Sequence[date],
          by_provider: Mapping[BaselineProvider, Mapping[date, PremarketVolumeSession]],
          rule: BaselineRule) -> AMoverBaseline:
    """The covered walk itself: newest first, Kiwoom preferred, stopping at ``rule.sessions``.

    One implementation, so the per-symbol read and the batch read cannot produce different
    denominators from the same rows.
    """
    used: list[date] = []
    volumes: list[float] = []
    providers: list[BaselineProvider] = []
    missing: list[date] = []
    for day in days:                                   # newest first; the walk skips uncovered
        answer = _resolve(day, by_provider, rule)
        if answer is None:
            missing.append(day)
            continue
        provider, value = answer
        used.append(day)
        volumes.append(value)
        providers.append(provider)
        if len(used) == rule.sessions:
            break
    ordered = tuple(reversed(used))
    values = tuple(reversed(volumes))
    found = tuple(reversed(providers))
    if len(used) < rule.sessions:
        return AMoverBaseline(BaselineStatus.INSUFFICIENT_COVERED_SESSIONS, symbol, exchange,
                              entry_session_date, ordered, tuple(missing), values, None, rule,
                              found)
    return AMoverBaseline(BaselineStatus.AVAILABLE, symbol, exchange, entry_session_date,
                          ordered, tuple(missing), values, float(median(values)), rule, found)


def load_baseline(session: Session, symbol: str, exchange: str, entry_session_date: date, *,
                  calendar: MarketCalendar | None = None, rule: BaselineRule | None = None,
                  ) -> AMoverBaseline:
    """A's denominator from durable rows only. Never a market-data request.

    The walk is newest first over the declared lookback, takes the first ``rule.sessions``
    sessions either provider has a covered answer for, and prefers Kiwoom within each session.
    The entry session is structurally absent from ``lookback_sessions``, so its own premarket
    volume cannot reach its own denominator whatever either provider holds.

    A whole universe goes through :func:`load_baselines` instead; this is the single-symbol
    read, and both run the same walk over the same rows.
    """
    rule = rule or BaselineRule()
    calendar = calendar or MarketCalendar()
    symbol = normalize_symbol(symbol)
    days = lookback_sessions(calendar, entry_session_date, limit=rule.max_lookback_sessions)
    return _walk(symbol, exchange, entry_session_date, days,
                 _by_provider(session, symbol, exchange, days, rule), rule)


#: Symbols per ``IN`` clause. Well inside SQLite's parameter ceiling once the sessions and the
#: identity columns are counted, and large enough that the whole universe is a handful of
#: queries rather than two per symbol.
SYMBOL_CHUNK = 400


def load_baselines(session: Session, candidates: Sequence[tuple[str, str]],
                   entry_session_date: date, *, calendar: MarketCalendar | None = None,
                   rule: BaselineRule | None = None) -> dict[str, AMoverBaseline]:
    """Every candidate's denominator, in a handful of queries rather than two per symbol.

    This exists for one measured reason. A's cut has about five minutes before E's own
    finalization and the margin on E's 09:29:45 deadline was measured at seconds, not minutes;
    reading two identities one symbol at a time costs about 11 s over a 4,947-symbol universe
    against 6.6 s for the single-identity read it replaces. Batching the rows and keeping the
    walk returns that: the queries are per chunk, and the arithmetic is :func:`_walk`, the same
    function :func:`load_baseline` uses, so a batch denominator cannot differ from a
    single-symbol one.
    """
    rule = rule or BaselineRule()
    calendar = calendar or MarketCalendar()
    days = lookback_sessions(calendar, entry_session_date, limit=rule.max_lookback_sessions)
    pairs = [(normalize_symbol(symbol), exchange) for symbol, exchange in candidates]
    rows: dict[tuple[str, str], dict[BaselineProvider, dict[date, PremarketVolumeSession]]] = {
        pair: {provider: {} for provider, _, _ in rule.identities()} for pair in pairs}
    by_exchange: dict[str, list[str]] = {}
    for symbol, exchange in pairs:
        by_exchange.setdefault(exchange, []).append(symbol)
    for provider, source, version in rule.identities():
        for exchange, symbols in by_exchange.items():
            for start in range(0, len(symbols), SYMBOL_CHUNK):
                chunk = symbols[start:start + SYMBOL_CHUNK]
                found = session.scalars(select(PremarketVolumeSession).where(
                    PremarketVolumeSession.symbol.in_(chunk),
                    PremarketVolumeSession.exchange == exchange,
                    PremarketVolumeSession.source == source,
                    PremarketVolumeSession.collector_version == version,
                    PremarketVolumeSession.trading_date.in_(list(days))))
                for row in found:
                    held = rows.get((row.symbol, exchange))
                    if held is not None:
                        held[provider][row.trading_date] = row
    return {symbol: _walk(symbol, exchange, entry_session_date, days,
                          rows[(symbol, exchange)], rule)
            for symbol, exchange in pairs}


def covered_count(session: Session, symbol: str, exchange: str, entry_session_date: date, *,
                  calendar: MarketCalendar | None = None,
                  rule: BaselineRule | None = None,
                  provider: BaselineProvider | None = None) -> int:
    """How many sessions in the lookback already have a final covered answer stored.

    With no ``provider`` the count is the combined one the live precondition reads; naming a
    provider counts only that identity, which is how the forward replacement is measured.
    """
    rule = rule or BaselineRule()
    calendar = calendar or MarketCalendar()
    days = lookback_sessions(calendar, entry_session_date, limit=rule.max_lookback_sessions)
    by_provider = _by_provider(session, normalize_symbol(symbol), exchange, days, rule)
    if provider is not None:
        rows = by_provider.get(provider, {})
        return sum(1 for day in days
                   if day in rows and usable_volume(rows[day]) is not None)
    return sum(1 for day in days if _resolve(day, by_provider, rule) is not None)


def assess_session_at_cut(symbol: str, exchange: str, day: date, window, fetch, *,
                          collected_at: datetime, cut: time = A_CUT,
                          source: str = SOURCE,
                          collector_version: str = COLLECTOR_VERSION) -> V.SessionVolume:
    """``premarket_volume_history.assess_session`` with A's window end.

    The parent classifies minutes against the regular open; A's window ends at the scan cut, so
    a bar at or after the cut is a regular-session-side bar for A's purposes and is counted
    towards ``regular_bar_count`` instead of the premarket sum. Everything else - the symbol,
    date and session-label identity checks, the truncation rule and the quality vocabulary - is
    the parent's and is not restated.
    """
    zone = window.market_open.tzinfo
    start = datetime.combine(day, V.PREMARKET_START, zone)
    cut_at = datetime.combine(day, cut, zone)
    premarket: list[Any] = []
    after_cut = 0
    mismatch: str | None = None
    from app.market.domain import MarketSession
    for bar in sorted(fetch.bars, key=lambda item: item.timestamp):
        local = bar.timestamp.astimezone(zone)
        expected = (None if local < start
                    else MarketSession.PREMARKET if local < window.market_open
                    else MarketSession.REGULAR if local < window.market_close
                    else MarketSession.POSTMARKET)
        if bar.symbol != symbol or local.date() != day or bar.session is not expected:
            mismatch = mismatch or ("SYMBOL" if bar.symbol != symbol
                                    else "DATE" if local.date() != day else "SESSION_LABEL")
            continue
        if expected is MarketSession.PREMARKET and local < cut_at:
            premarket.append(bar)
        elif expected in (MarketSession.PREMARKET, MarketSession.REGULAR):
            after_cut += 1
    quality, reason = (
        (V.SessionQuality.SESSION_IDENTITY_MISMATCH, mismatch) if mismatch
        else (V.SessionQuality.TARGET_NOT_REACHED, None) if not fetch.target_reached
        else (V.SessionQuality.NO_PREMARKET_BARS, None) if not premarket
        else (V.SessionQuality.NO_REGULAR_BARS, None) if not after_cut
        else (V.SessionQuality.COMPLETE, None))
    return V.SessionVolume(
        symbol, exchange, day, source, collector_version, quality, reason,
        sum(bar.volume for bar in premarket) if premarket else None, len(premarket), after_cut,
        premarket[0].timestamp if premarket else None,
        premarket[-1].timestamp if premarket else None,
        fetch.pages_used, fetch.target_reached, collected_at)


class AMoverPremarketVolumeService(V.PremarketVolumeHistoryService):
    """The V2 collector with A's window and A's planning window. Two overrides, nothing else."""

    def __init__(self, session: Session, source=None, *, calendar: MarketCalendar | None = None,
                 clock=None, rule: BaselineRule | None = None) -> None:
        self.rule = rule or BaselineRule()
        super().__init__(session, source, calendar=calendar, clock=clock,
                         source_name=self.rule.source,
                         collector_version=self.rule.collector_version)

    def required_sessions(self, entry_session_date: date) -> tuple[date, ...]:
        """A's planning window, oldest first: every session the covered walk may reach."""
        return tuple(reversed(lookback_sessions(self.calendar, entry_session_date,
                                                limit=self.rule.max_lookback_sessions)))

    def missing_sessions(self, symbol: str, exchange: str,
                         entry_session_date: date) -> tuple[date, ...]:
        """Only what *Kiwoom* still owes, newest first - not the whole lookback.

        This service collects under the Kiwoom identity, so the walk here counts Kiwoom rows
        alone. A bootstrap row for a session is not a Kiwoom observation of it, and treating it
        as one would report the collection as finished the moment the bootstrap half existed.
        The live denominator's combined precondition is ``load_baseline``'s question, not this
        one.

        The parent returns every session in its window with no final answer, which for A's
        40-session lookback would be twice the work the twenty-session median actually needs.
        A asks for the newest unanswered sessions, as many as are still missing from twenty.
        If some of those come back uncovered the *next* run asks for the next older ones, so
        the worst case costs more runs rather than more requests per run; the durable rows stay
        the checkpoint either way.
        """
        symbol = normalize_symbol(symbol)
        days = lookback_sessions(self.calendar, entry_session_date,
                                 limit=self.rule.max_lookback_sessions)
        rows = _stored_rows(self.session, symbol, exchange, days, source=self.source_name,
                            collector_version=self.collector_version)
        covered, pending = 0, []
        for day in days:                                   # newest first
            row = rows.get(day)
            value = usable_volume(row) if row is not None else None
            if value is not None:
                covered += 1
            elif row is None or V.retryable(row, first_complete_session(
                    self.session, symbol, exchange, self.source_name, self.collector_version)):
                pending.append(day)
            if covered + len(pending) >= self.rule.sessions:
                break
        return tuple(pending)

    def collect_session(self, symbol: str, exchange: str, day: date) -> V.SessionVolume:
        window = self.calendar.session(day)
        if window is None:
            raise ValueError(f"{day} is not an XNYS session")
        now = self.clock()
        if now < window.market_close:
            raise ValueError(f"session {day} has not completed")
        if self.source is None:
            raise RuntimeError("collection needs a market-data source")
        start = datetime.combine(day, V.PREMARKET_START, window.market_open.tzinfo)
        try:
            fetch = self.source.fetch_session(symbol, exchange, start, window.market_close)
        except MarketDataError as exc:
            return V.SessionVolume(symbol, exchange, day, self.source_name,
                                   self.collector_version, V.SessionQuality.PROVIDER_FAILURE,
                                   exc.code, None, 0, 0, None, None, None, None, now)
        return assess_session_at_cut(symbol, exchange, day, window, fetch, collected_at=now,
                                     source=self.source_name,
                                     collector_version=self.collector_version)

    def baseline(self, symbol: str, exchange: str,
                 entry_session_date: date) -> AMoverBaseline:
        return load_baseline(self.session, symbol, exchange, entry_session_date,
                             calendar=self.calendar, rule=self.rule)


def rows_statistics(session: Session, *, rule: BaselineRule | None = None,
                    provider: BaselineProvider = BaselineProvider.KIWOOM) -> dict[str, Any]:
    """How much of one provider's half of A's baseline exists today. Read-only.

    The default is Kiwoom, which is what the backfill planner asks about; the bootstrap half
    is the same question under the other identity.
    """
    rule = rule or BaselineRule()
    source, version = ((rule.source, rule.collector_version)
                       if provider is BaselineProvider.KIWOOM
                       else (rule.bootstrap_source, rule.bootstrap_collector_version))
    rows = list(session.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.source == source,
        PremarketVolumeSession.collector_version == version)))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.quality_status] = counts.get(row.quality_status, 0) + 1
    sessions = sorted({row.trading_date for row in rows})
    return {"identity": rule.identity, "provider": str(provider), "source": source,
            "collector_version": version, "rows": len(rows),
            "symbols": len({row.symbol for row in rows}), "sessions": len(sessions),
            "oldest_session": sessions[0].isoformat() if sessions else None,
            "newest_session": sessions[-1].isoformat() if sessions else None,
            "quality_counts": counts,
            "covered_rows": sum(1 for row in rows
                                if row.quality_status in COVERED_QUALITIES)}


# -- forward replacement ---------------------------------------------------------------------------

#: Recorded on every forward row, so a reader sees it was written at the cut and not after it.
FORWARD_QUALITY_REASON = "A_CUT_FORWARD_OBSERVATION"


def forward_observation(snapshot: Any, exchange: str, *, collected_at: datetime,
                        rule: BaselineRule | None = None) -> V.SessionVolume:
    """Today's own A observation as the durable row tomorrow's denominator will read.

    The snapshot is already A's quantity over A's window: ``snapshots_from`` builds one only for
    a symbol the collector finalized contiguously, so ``[04:00, 09:15)`` was fully observed and
    nothing after the cut can change the sum. That is why this is a COMPLETE row rather than a
    provisional one, and why the forward path needs no second collection: the 60-plus-hour
    historical backfill exists to reach *backwards*, and reaching backwards is the bootstrap
    half's job.

    ``regular_bar_count`` is 0 and ``quality_reason`` names the pass, because at 09:15 there is
    no regular session to have observed. A symbol that printed nothing by the cut is
    ``NO_PREMARKET_BARS`` with a zero, which is A's covered-silent-session rule and not a gap.

    One consequence worth stating: a forward row and a ``backfill`` row for the same session are
    both COMPLETE under the same identity, so if the backfill is ever run over a session the
    forward pass already recorded, the upsert replaces the value rather than keeping the
    earlier one. Both read the same window from the same source, so they should agree; the
    backfill is no longer a precondition for anything, and running it over already-observed
    sessions is the case where a disagreement would be worth looking at rather than overwriting.
    """
    rule = rule or BaselineRule()
    if snapshot.session_date is None:
        raise ValueError("a forward observation needs the session it was taken on")
    if snapshot.cut_minute != rule.window_minutes[1]:
        raise ValueError(f"the snapshot cut is minute {snapshot.cut_minute}, not A's "
                         f"{rule.window_minutes[1]}")
    bars = int(snapshot.raw.get("pm_bar_count", 0.0) or 0)
    volume = float(snapshot.raw.get("pm_share_volume", 0.0) or 0.0)
    quality = (V.SessionQuality.COMPLETE if bars > 0 else V.SessionQuality.NO_PREMARKET_BARS)
    return V.SessionVolume(
        normalize_symbol(snapshot.symbol), exchange, snapshot.session_date, rule.source,
        rule.collector_version, quality, FORWARD_QUALITY_REASON, int(round(volume)), bars, 0,
        None, None, None, None, collected_at)


def record_forward_observations(session: Session, snapshots: Mapping[str, Any],
                                exchanges: Mapping[str, str], *, collected_at: datetime,
                                rule: BaselineRule | None = None,
                                calendar: MarketCalendar | None = None) -> dict[str, Any]:
    """Store this session's A observations, so the next session's walk finds them.

    Idempotent by the V2 upsert's own identity, so a restart at the cut writes the same rows
    once rather than a second set: section Q's duplicate-retry case is a property of the write
    and not of the caller.
    """
    rule = rule or BaselineRule()
    service = V.PremarketVolumeHistoryService(
        session, None, calendar=calendar, clock=lambda: collected_at,
        source_name=rule.source, collector_version=rule.collector_version)
    counts: dict[str, int] = {}
    skipped: list[str] = []
    for symbol in sorted(snapshots):
        snapshot = snapshots[symbol]
        exchange = exchanges.get(symbol)
        if exchange is None:
            # An unmapped symbol is reported, never stored under a guessed exchange: the
            # exchange is part of the row identity, so a wrong one is a row nothing reads.
            skipped.append(symbol)
            continue
        outcome = service.upsert(forward_observation(snapshot, exchange,
                                                     collected_at=collected_at, rule=rule))
        counts[str(outcome)] = counts.get(str(outcome), 0) + 1
    session.commit()
    return {"provider": str(BaselineProvider.KIWOOM), "source": rule.source,
            "collector_version": rule.collector_version,
            "requested": len(snapshots), "written": sum(counts.values()),
            "outcomes": dict(sorted(counts.items())),
            "skipped_unmapped_exchange": skipped,
            "quality_reason": FORWARD_QUALITY_REASON,
            "enters_own_denominator": False,
            "note": "the session written here is the scan session, which lookback_sessions "
                    "excludes; it becomes a baseline candidate from the next session on"}


def provider_mix(baselines: Mapping[str, AMoverBaseline], *,
                 rule: BaselineRule | None = None) -> dict[str, Any]:
    """One run's denominator, aggregated over the symbols that got one.

    Two things are reported because they answer different questions, and conflating them is how
    a mix becomes misleading. ``kiwoom_session_count`` and ``massive_session_count`` are in
    section I's units - sessions out of twenty, for the typical symbol - so the mode table reads
    the way it is written; the totals across symbols are reported separately under their own
    names rather than borrowed into those two fields, where 6 of 20 over sixty symbols would
    print as 360.

    The mode is the strict aggregate, not the median one: it is KIWOOM_NATIVE only when no
    available denominator used a bootstrap session at all and MASSIVE_BOOTSTRAP only when none
    used a Kiwoom one, so one lagging symbol keeps the run honestly MIXED_BOOTSTRAP. The
    per-symbol spread is beside it, so a single number never hides a symbol that is still short.
    """
    rule = rule or BaselineRule()
    available = [item for item in baselines.values() if item.available]
    total_kiwoom = sum(item.kiwoom_session_count for item in available)
    total_massive = sum(item.massive_session_count for item in available)
    kiwoom_each = sorted(item.kiwoom_session_count for item in available)
    massive_each = sorted(item.massive_session_count for item in available)
    middle = len(kiwoom_each) // 2
    typical_kiwoom = kiwoom_each[middle] if kiwoom_each else 0
    typical_massive = massive_each[middle] if massive_each else 0
    modes: dict[str, int] = {}
    for item in baselines.values():
        modes[str(item.mode)] = modes.get(str(item.mode), 0) + 1
    oldest = [item.oldest_used_session for item in available if item.oldest_used_session]
    newest = [item.newest_used_session for item in available if item.newest_used_session]
    return {
        "baseline_identity": rule.identity,
        "requested": len(baselines),
        "available": len(available),
        "insufficient": len(baselines) - len(available),
        "sessions_required": rule.sessions,
        "baseline_session_count": rule.sessions if available else 0,
        "kiwoom_session_count": typical_kiwoom,
        "massive_session_count": typical_massive,
        "baseline_mode": str(mode_for(total_kiwoom, total_massive)),
        "counts_are": "sessions out of the required window, for the median symbol",
        "total_kiwoom_session_observations": total_kiwoom,
        "total_massive_session_observations": total_massive,
        "per_symbol_modes": dict(sorted(modes.items())),
        "per_symbol_kiwoom_sessions": {
            "min": kiwoom_each[0] if kiwoom_each else None,
            "max": kiwoom_each[-1] if kiwoom_each else None,
            "median": typical_kiwoom if kiwoom_each else None},
        "oldest_used_session": min(oldest).isoformat() if oldest else None,
        "newest_used_session": max(newest).isoformat() if newest else None,
        "sessions_until_kiwoom_native": max(
            (rule.sessions - item.kiwoom_session_count for item in available), default=None),
        "provider_priority": [str(provider) for provider, _, _ in rule.identities()],
    }
