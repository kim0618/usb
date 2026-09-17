"""Corporate-action flags for a symbol on decision date D, from information known by D.

Flags are metadata for feature snapshots and, later, trade records. They do not exclude a
symbol by themselves; the research design decides what a flag means.

* ``SPLIT_ON_DAY`` / ``RECENT_SPLIT``: from split records with ``execution_date <= D``.
* ``IPO_WARMUP``: fewer than ``ipo_warmup_sessions`` prior sessions since listing.
* ``DELISTING_WINDOW``: only from a delisting notice whose ``announced_on`` is before D. A
  delisting date read from today's reference data is hindsight and is refused.
* ``SYMBOL_CHANGE``: a ticker change effective within the window, up to and including D.
* ``CA_SUSPECT``: the previous session's post-session audit was ``CORPORATE_ACTION_SUSPECT``.
  Only an earlier session's verdict is accepted; D's own verdict does not exist yet.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from app.strategy_b.config import CorporateActionConfig
from app.strategy_b.errors import PointInTimeViolation
from app.strategy_b.models import CorporateActionFlag, SessionVerdict
from app.strategy_b.split_adjustment import SplitRecord, split_adjustment


@dataclass(frozen=True, slots=True)
class DelistingNotice:
    announced_on: date
    effective_date: date


@dataclass(frozen=True, slots=True)
class SymbolChange:
    effective_date: date
    old_symbol: str
    new_symbol: str


@dataclass(frozen=True, slots=True)
class PriorSessionAudit:
    session_date: date
    verdict: SessionVerdict


def derive_corporate_action_flags(
    decision_date: date,
    *,
    config: CorporateActionConfig,
    splits: Sequence[SplitRecord] = (),
    prior_sessions_since_listing: int | None = None,
    delisting_notices: Sequence[DelistingNotice] = (),
    symbol_changes: Sequence[SymbolChange] = (),
    prior_audit: PriorSessionAudit | None = None,
) -> frozenset[CorporateActionFlag]:
    flags: set[CorporateActionFlag] = set()
    adjustment = split_adjustment(splits, observed_date=decision_date, current_date=decision_date,
                                  recent_split_calendar_days=config.recent_split_calendar_days)
    if adjustment.split_on_day:
        flags.add(CorporateActionFlag.SPLIT_ON_DAY)
    if adjustment.recent_split:
        flags.add(CorporateActionFlag.RECENT_SPLIT)

    if prior_sessions_since_listing is not None and prior_sessions_since_listing < config.ipo_warmup_sessions:
        flags.add(CorporateActionFlag.IPO_WARMUP)

    window_end = decision_date + timedelta(days=config.delisting_window_calendar_days)
    for notice in delisting_notices:
        if notice.announced_on >= decision_date:
            raise PointInTimeViolation(
                f"delisting notice announced {notice.announced_on} is not known before {decision_date}")
        if decision_date <= notice.effective_date <= window_end:
            flags.add(CorporateActionFlag.DELISTING_WINDOW)

    change_start = decision_date - timedelta(days=config.symbol_change_calendar_days)
    if any(change_start <= c.effective_date <= decision_date for c in symbol_changes):
        flags.add(CorporateActionFlag.SYMBOL_CHANGE)

    if prior_audit is not None:
        if prior_audit.session_date >= decision_date:
            raise PointInTimeViolation(
                f"session audit for {prior_audit.session_date} does not exist before {decision_date}")
        if prior_audit.verdict is SessionVerdict.CORPORATE_ACTION_SUSPECT:
            flags.add(CorporateActionFlag.CA_SUSPECT)
    return frozenset(flags)
