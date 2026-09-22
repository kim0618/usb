"""Per (symbol, trading date D) preparation: scope, warmup, corporate-action flags, and the audit.

Everything a replay of D may read about a symbol is decided here, from information that
existed before D opened, and nothing else is handed to the adapter:

* **Scope** ``S(D)``: ``evaluate_research_scope`` over the latest ticker metadata observed
  before D and the raw daily history strictly before D. No metadata before D is its own status
  (``NO_METADATA`` / ``METADATA_UNAVAILABLE``), never a silent include.
* **Warmup**: the ``RvolConfig.lookback_sessions`` XNYS sessions before D. A session the minute
  dataset covers counts even when it has no bar (a silent session has zero volume, which is a
  real baseline); a session outside the dataset's range does not. FULL / PARTIAL / UNKNOWN is
  the expectation the RVOL function will reach, recorded up front.
* **Corporate-action flags** for D: splits executed by D, prior sessions since listing (from the
  pre-D metadata's list date), and ``CA_SUSPECT`` from D-1's post-session audit computed with
  the split records known by D-1 only - a later split record must not reach D's flags through
  the audit's split-ratio match.

The post-session audit of D itself (``audit_session``) needs D's official daily bar. It is a
report row, produced by a separate function whose output no replay input type accepts.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.backtest.strategy_b.dataset import SparseMinuteDataset, boundaries_for
from app.backtest.strategy_b.reference import ReferenceBundle
from app.market.calendar import MarketCalendar
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.corporate_actions import PriorSessionAudit, derive_corporate_action_flags
from app.strategy_b.features import SessionTape
from app.strategy_b.models import CorporateActionFlag, RvolStatus, Session, SessionVerdict
from app.strategy_b.scope import ScopeInputs, evaluate_research_scope
from app.strategy_b.sparse_session import validate_sparse_session
from app.strategy_b.split_adjustment import known_splits

PREPARATION_VERSION = "strategy-b-session-preparation-v1"


class ScopeStatus(StrEnum):
    IN_SCOPE = "IN_SCOPE"
    EXCLUDED = "EXCLUDED"
    NO_METADATA = "NO_METADATA"
    """No ticker-details observation dated before D."""
    METADATA_UNAVAILABLE = "METADATA_UNAVAILABLE"
    """The latest pre-D observation is a recorded failure (for example NOT_FOUND)."""


@dataclass(frozen=True)
class SymbolSessionPlan:
    symbol: str
    session_date: date
    scope_status: ScopeStatus
    scope_reasons: tuple[str, ...]
    metadata_as_of: date | None
    reference_price: float | None
    median_dollar_volume: float | None
    history_sessions: int
    warmup_required: int
    warmup_covered: int
    warmup_dates: tuple[date, ...]
    warmup_status: RvolStatus
    corporate_action_flags: frozenset[CorporateActionFlag]
    prior_audit_verdict: SessionVerdict | None

    @property
    def in_scope(self) -> bool:
        return self.scope_status is ScopeStatus.IN_SCOPE

    def row(self) -> dict[str, object]:
        return {"symbol": self.symbol, "session_date": self.session_date.isoformat(),
                "scope_status": self.scope_status.value, "scope_reasons": ",".join(self.scope_reasons),
                "metadata_as_of": None if self.metadata_as_of is None else self.metadata_as_of.isoformat(),
                "reference_price": self.reference_price, "median_dollar_volume": self.median_dollar_volume,
                "history_sessions": self.history_sessions, "warmup_required": self.warmup_required,
                "warmup_covered": self.warmup_covered, "warmup_status": self.warmup_status.value,
                "corporate_action_flags": ",".join(sorted(flag.value for flag in self.corporate_action_flags)),
                "prior_audit_verdict": None if self.prior_audit_verdict is None else self.prior_audit_verdict.value}


@dataclass(frozen=True)
class SessionAuditRecord:
    """AUDIT ONLY. Post-session HYBRID-S verdict of one (symbol, date)."""

    symbol: str
    session_date: date
    verdict: SessionVerdict | None
    status: str
    findings: tuple[str, ...]
    premarket_bars: int
    regular_bars: int
    after_bars: int
    regular_missing_ratio: float | None
    volume_coverage: float | None
    split_on_day: bool

    def row(self) -> dict[str, object]:
        return {"symbol": self.symbol, "session_date": self.session_date.isoformat(),
                "verdict": None if self.verdict is None else self.verdict.value, "status": self.status,
                "findings": ",".join(self.findings), "premarket_bars": self.premarket_bars,
                "regular_bars": self.regular_bars, "after_bars": self.after_bars,
                "regular_missing_ratio": self.regular_missing_ratio,
                "volume_coverage": self.volume_coverage, "split_on_day": self.split_on_day}


def warmup_dates(calendar: MarketCalendar, day: date, sessions: int) -> tuple[date, ...]:
    days: list[date] = []
    cursor = day
    for _ in range(sessions):
        cursor = calendar.previous_trading_day(cursor)
        days.append(cursor)
    return tuple(reversed(days))


def _sessions_since_listing(calendar: MarketCalendar, day: date, list_date: date | None,
                            cap: int) -> int | None:
    if list_date is None:
        return None
    count, cursor = 0, day
    while count < cap:
        cursor = calendar.previous_trading_day(cursor)
        if cursor < list_date:
            break
        count += 1
    return count


def _verdict(dataset: SparseMinuteDataset, bundle: ReferenceBundle, day: date, *,
             calendar: MarketCalendar, config: StrategyBConfig, splits_known_by: date | None):
    daily = bundle.official_daily(day)
    if daily is None or not dataset.covers(day):
        return None
    splits = bundle.splits if splits_known_by is None else known_splits(bundle.splits, splits_known_by)
    return validate_sparse_session(dataset.bars(day), daily, boundaries_for(calendar, day),
                                   splits=splits, config=config.sparse_validation)


def plan_symbol_session(symbol: str, day: date, *, dataset: SparseMinuteDataset | None,
                        bundle: ReferenceBundle, calendar: MarketCalendar,
                        config: StrategyBConfig) -> SymbolSessionPlan:
    observation = bundle.metadata_before(day)
    metadata = None if observation is None else observation.metadata
    lookback = config.rvol.lookback_sessions
    dates = warmup_dates(calendar, day, lookback)
    covered = tuple(item for item in dates if dataset is not None and dataset.covers(item))
    if len(covered) == lookback:
        warmup = RvolStatus.FULL
    elif len(covered) >= config.rvol.min_partial_sessions:
        warmup = RvolStatus.PARTIAL
    else:
        warmup = RvolStatus.UNKNOWN

    previous = calendar.previous_trading_day(day)
    prior = None
    if dataset is not None:
        validation = _verdict(dataset, bundle, previous, calendar=calendar, config=config,
                              splits_known_by=previous)
        prior = None if validation is None else validation.verdict
    flags = derive_corporate_action_flags(
        day, config=config.corporate_actions, splits=known_splits(bundle.splits, day),
        prior_sessions_since_listing=_sessions_since_listing(
            calendar, day, None if metadata is None else metadata.list_date,
            config.corporate_actions.ipo_warmup_sessions),
        prior_audit=None if prior is None else PriorSessionAudit(previous, prior))

    common = dict(symbol=symbol, session_date=day,
                  metadata_as_of=None if observation is None else observation.as_of_date,
                  warmup_required=lookback, warmup_covered=len(covered), warmup_dates=covered,
                  warmup_status=warmup, corporate_action_flags=flags, prior_audit_verdict=prior)
    if observation is None or metadata is None:
        status = ScopeStatus.NO_METADATA if observation is None else ScopeStatus.METADATA_UNAVAILABLE
        reason = status.value if observation is None else str(observation.unavailable_reason)
        return SymbolSessionPlan(**common, scope_status=status, scope_reasons=(reason,),
                                 reference_price=None, median_dollar_volume=None, history_sessions=0)
    decision = evaluate_research_scope(ScopeInputs(day, metadata, bundle.daily_before(day)), config.scope)
    return SymbolSessionPlan(
        **common, scope_status=ScopeStatus.IN_SCOPE if decision.included else ScopeStatus.EXCLUDED,
        scope_reasons=tuple(reason.value for reason in decision.exclusion_reasons),
        reference_price=decision.reference_price, median_dollar_volume=decision.median_dollar_volume,
        history_sessions=decision.history_sessions)


def audit_session(symbol: str, day: date, *, dataset: SparseMinuteDataset | None,
                  bundle: ReferenceBundle, calendar: MarketCalendar,
                  config: StrategyBConfig) -> SessionAuditRecord:
    """AUDIT ONLY: D's verdict against D's official daily bar, with every split record held."""
    bars = () if dataset is None or not dataset.covers(day) else dataset.bars(day)
    counts = {session: sum(1 for bar in bars if bar.session is session)
              for session in (Session.PREMARKET, Session.REGULAR, Session.AFTER)}
    base = dict(symbol=symbol, session_date=day, premarket_bars=counts[Session.PREMARKET],
                regular_bars=counts[Session.REGULAR], after_bars=counts[Session.AFTER])
    if dataset is None or not dataset.covers(day):
        return SessionAuditRecord(**base, verdict=None, status="NO_MINUTE_DATASET", findings=(),
                                  regular_missing_ratio=None, volume_coverage=None, split_on_day=False)
    validation = _verdict(dataset, bundle, day, calendar=calendar, config=config, splits_known_by=None)
    if validation is None:
        return SessionAuditRecord(**base, verdict=None, status="NO_OFFICIAL_DAILY_BAR", findings=(),
                                  regular_missing_ratio=None, volume_coverage=None, split_on_day=False)
    return SessionAuditRecord(**base, verdict=validation.verdict, status="AUDITED",
                              findings=tuple(item.value for item in validation.findings),
                              regular_missing_ratio=validation.missing_minute_ratio,
                              volume_coverage=validation.volume_coverage,
                              split_on_day=validation.split_on_day)


def session_tape(dataset: SparseMinuteDataset, day: date, calendar: MarketCalendar) -> SessionTape:
    return SessionTape(dataset.symbol, boundaries_for(calendar, day), dataset.bars(day))


def plans_by_symbol(plans: Mapping[str, SymbolSessionPlan]) -> tuple[SymbolSessionPlan, ...]:
    return tuple(plans[symbol] for symbol in sorted(plans))
