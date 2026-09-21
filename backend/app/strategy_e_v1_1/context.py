"""The forward feature-context gate: a 09:25 decision may be built only from complete, PIT inputs.

Frozen in ``strategy_e_forward_feature_context_v1.json``. This module fetches and loads no market
data; a future builder describes what it loaded as a ``ForwardFeatureContext`` and this gate
either accepts it or refuses it.

Two refusals, deliberately distinct:

``FutureContextViolation``
    an input is dated after its cutoff (a daily row for D or later, a reference snapshot after
    D-1, a LIVE seal after 09:30). The session is not a valid forward observation.
``FeatureContextIncomplete`` (status ``FEATURE_CONTEXT_INCOMPLETE``)
    something required was never collected. The decision is not made; nothing is filled, and no
    H5 flag is set to False on the builder's behalf.

A value that is *legitimately* undefined under Research semantics (fewer than five prior
premarket sessions for RVOL, fewer than two bars in [09:00, 09:24], a zero premarket range, SPY
printing nothing premarket) is not incomplete context: the source was collected and says so, the
feature is NaN, and Research's mask excludes the row exactly as it did in E1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = (REPO_ROOT
                 / "docs/backtest/strategy_e_candidate/strategy_e_forward_feature_context_v1.json")
CONTRACT_CANONICAL_SHA256 = (
    "78bb90b44b2d6eeec9fa945ef2fe7547178f525b4719cb81fc8171e14b5c3fad"
)
FEATURE_CONTEXT_INCOMPLETE = "FEATURE_CONTEXT_INCOMPLETE"
LIVE = "LIVE"
RECONSTRUCTED = "RECONSTRUCTED"
ET = ZoneInfo("America/New_York")
DECISION_CUTOFF_ET = time(9, 25)
FIRST_REGULAR_BAR_ET = time(9, 30)
#: [D-21, D-2] liquidity median window (20 sessions) plus the D-1 row itself.
REQUIRED_DAILY_SESSIONS = 21


class FutureContextViolation(RuntimeError):
    """An input dated after its PIT cutoff reached the decision."""


class FeatureContextIncomplete(RuntimeError):
    def __init__(self, session: date, missing: tuple[str, ...]):
        self.status = FEATURE_CONTEXT_INCOMPLETE
        self.session = session
        self.missing = missing
        super().__init__(f"{FEATURE_CONTEXT_INCOMPLETE} {session.isoformat()}: {'; '.join(missing)}")


@dataclass(frozen=True)
class MinuteCoverage:
    """What the minute source says about one symbol, as of the decision.

    ``session_page_collected``: D's page was fetched (an empty page means 'no prints', which is
    data; a missing page means 'not fetched', which is not).
    ``uncollected_history_sessions``: XNYS sessions inside the RVOL history range (back from D-1
    until 20 qualifying sessions or the symbol's first collected session) without a fetched page.
    """

    session_page_collected: bool
    uncollected_history_sessions: tuple[date, ...] = ()


@dataclass(frozen=True)
class ForwardFeatureContext:
    session: date
    provenance: str
    daily_sessions: tuple[date, ...]
    daily_missing_sessions: tuple[date, ...]
    reference_as_of: date | None
    splits_published_at: datetime | None
    splits_execution_through: date | None
    spy_close_previous: float | None
    spy_minute: MinuteCoverage | None
    symbols: Mapping[str, MinuteCoverage] = field(default_factory=dict)
    sealed_at: datetime | None = None


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    """The frozen context contract; a drifted file is refused, not read."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    found = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if found != CONTRACT_CANONICAL_SHA256:
        raise FutureContextViolation(
            f"forward feature context contract digest mismatch: {found}")
    if (payload["daily_snapshot"]["minimum_sessions_ending_at_d_minus_1"] != REQUIRED_DAILY_SESSIONS
            or payload["principle"]["fail_closed_status"] != FEATURE_CONTEXT_INCOMPLETE):
        raise FutureContextViolation("forward feature context constants differ from the contract")
    return payload


def _at(session: date, moment: time) -> datetime:
    return datetime.combine(session, moment, tzinfo=ET)


def require_complete(context: ForwardFeatureContext, *,
                     calendar: MarketCalendar | None = None) -> None:
    """Accept the context or raise. Future-dated inputs are checked before completeness."""
    market = calendar or MarketCalendar("America/New_York")
    session = context.session
    if market.session(session) is None:
        raise FeatureContextIncomplete(session, ("session is not an XNYS trading session",))
    if context.provenance not in (LIVE, RECONSTRUCTED):
        raise FutureContextViolation(f"unknown provenance {context.provenance!r}")
    previous = market.previous_trading_day(session)

    future = []
    if any(day >= session for day in context.daily_sessions):
        future.append("a daily row dated on or after D")
    if context.reference_as_of is not None and context.reference_as_of > previous:
        future.append("a CS reference snapshot dated after D-1")
    if context.provenance == LIVE:
        if context.sealed_at is None or context.sealed_at >= _at(session, FIRST_REGULAR_BAR_ET):
            future.append("a LIVE seal not written before 09:30 ET")
        if (context.splits_published_at is not None
                and context.splits_published_at > _at(session, DECISION_CUTOFF_ET)):
            future.append("a LIVE split list published after 09:25 ET")
    if future:
        raise FutureContextViolation(f"{session.isoformat()}: " + "; ".join(future))

    missing = []
    if not context.daily_sessions or context.daily_sessions[-1] != previous:
        missing.append(f"grouped daily through D-1 = {previous.isoformat()}")
    elif len(context.daily_sessions) < REQUIRED_DAILY_SESSIONS:
        missing.append(f"{REQUIRED_DAILY_SESSIONS} daily sessions ending at D-1")
    if context.daily_missing_sessions:
        missing.append(f"daily files for {len(context.daily_missing_sessions)} XNYS sessions")
    if context.reference_as_of is None:
        missing.append("a CS reference snapshot dated on or before D-1")
    if (context.splits_published_at is None or context.splits_execution_through is None
            or context.splits_execution_through < session):
        missing.append("a split list covering executions through D")
    if (context.spy_close_previous is None or not math.isfinite(context.spy_close_previous)
            or context.spy_close_previous <= 0):
        missing.append("SPY close(D-1)")
    if context.spy_minute is None or not context.spy_minute.session_page_collected:
        missing.append("SPY minute page for D")
    if not context.symbols:
        missing.append("minute coverage for the D-1 daily-eligible universe")
    uncollected = sorted(symbol for symbol, cover in context.symbols.items()
                         if not cover.session_page_collected or cover.uncollected_history_sessions)
    if uncollected:
        missing.append(f"minute pages for {len(uncollected)} symbols ({', '.join(uncollected[:5])})")
    if missing:
        raise FeatureContextIncomplete(session, tuple(missing))
