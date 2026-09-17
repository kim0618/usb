"""Time-of-day RVOL from minute bars, numerator and denominator on the same basis.

    RVOL(as_of) = today's cumulative minute volume from the scope start to as_of
                  / mean over the last N earlier sessions of cumulative minute volume
                    from their scope start to the same elapsed time

Daily volume is never mixed in: minute volume sums to 0.74 to 1.0 of the daily volume
(closing auction and volume-only prints), so a daily denominator would bias RVOL low.

Status: ``FULL`` with ``lookback_sessions`` profiles, ``PARTIAL`` with at least
``min_partial_sessions``, otherwise ``UNKNOWN``. Historical share volume crossing a split is
rescaled with ``split_adjustment`` using only splits executed by today.

Profiles are built once per past session (``build_volume_profile``) and each RVOL query is
O(lookback × log n). The caller decides which past sessions to pass (for example, whether
an ``API_LOSS_SUSPECT`` session belongs in the baseline); this module only refuses ones that
are not strictly earlier than today.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.strategy_b.config import RvolConfig
from app.strategy_b.errors import PointInTimeViolation
from app.strategy_b.features import SessionTape
from app.strategy_b.models import Availability, RvolResult, RvolStatus, Session
from app.strategy_b.session import AggregationScope, SessionBoundaries
from app.strategy_b.split_adjustment import SplitRecord, split_adjustment


@dataclass(frozen=True, slots=True)
class CumulativeVolumeCurve:
    """Cumulative volume by seconds elapsed from a scope start, at each bar's availability."""

    elapsed_seconds: tuple[float, ...]
    cumulative: tuple[float, ...]

    def at(self, elapsed: timedelta) -> float:
        index = bisect_right(self.elapsed_seconds, elapsed.total_seconds())
        return self.cumulative[index - 1] if index else 0.0


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """One past session's cumulative volume curves, keyed by the session they start in."""

    session_date: date
    scope: AggregationScope
    curves: Mapping[Session, CumulativeVolumeCurve]


def _anchor(boundaries: SessionBoundaries, session: Session, scope: AggregationScope) -> datetime | None:
    if session is Session.OUTSIDE:
        return None
    start = boundaries.session_start(session)
    if start is None:
        return None
    return boundaries.scope_start(start, scope)


def build_volume_profile(tape: SessionTape, scope: AggregationScope) -> VolumeProfile:
    """Curves for a completed past session. Every bar of that day is legitimately past."""
    curves: dict[Session, CumulativeVolumeCurve] = {}
    for session in (Session.PREMARKET, Session.REGULAR, Session.AFTER):
        anchor = _anchor(tape.boundaries, session, scope)
        end = tape.boundaries.session_end(session)
        if anchor is None or end is None:
            raise ValueError(f"{session} has no boundaries")
        lo, hi = tape.first_index_at_or_after(anchor), tape.first_index_at_or_after(end)
        elapsed: list[float] = []
        cumulative: list[float] = []
        total = 0.0
        for bar in tape.bars[lo:hi]:
            total += bar.volume
            elapsed.append((bar.available_at - anchor).total_seconds())
            cumulative.append(total)
        curves[session] = CumulativeVolumeCurve(tuple(elapsed), tuple(cumulative))
    return VolumeProfile(tape.boundaries.session_date, scope, curves)


def time_of_day_rvol(tape: SessionTape, as_of: datetime, history: Sequence[VolumeProfile], *,
                     splits: Sequence[SplitRecord], config: RvolConfig) -> RvolResult:
    today = tape.boundaries.session_date
    seen: set[date] = set()
    for profile in history:
        if profile.session_date >= today:
            raise PointInTimeViolation(
                f"RVOL history session {profile.session_date} is not before {today}")
        if profile.session_date in seen:
            raise ValueError(f"RVOL history repeats session {profile.session_date}")
        if profile.scope is not config.scope:
            raise ValueError(f"RVOL history scope {profile.scope} does not match {config.scope}")
        seen.add(profile.session_date)

    required = config.lookback_sessions
    used = sorted(history, key=lambda p: p.session_date)[-required:]
    session = tape.boundaries.classify(as_of)
    anchor = _anchor(tape.boundaries, session, config.scope)
    if anchor is None:
        return RvolResult(None, RvolStatus.UNKNOWN, len(used), required, False, Availability.NO_DATA)
    if len(used) < config.min_partial_sessions:
        return RvolResult(None, RvolStatus.UNKNOWN, len(used), required, False,
                          Availability.INSUFFICIENT_HISTORY)

    elapsed = as_of - anchor
    numerator = tape.volume(tape.first_index_at_or_after(anchor), tape.cut(as_of))
    baseline = 0.0
    adjusted = False
    for profile in used:
        factor = split_adjustment(splits, observed_date=profile.session_date, current_date=today,
                                  recent_split_calendar_days=0).share_factor
        adjusted = adjusted or factor != 1
        baseline += profile.curves[session].at(elapsed) * factor
    baseline /= len(used)
    if baseline <= 0:
        return RvolResult(None, RvolStatus.UNKNOWN, len(used), required, adjusted,
                          Availability.ZERO_BASELINE)
    status = RvolStatus.FULL if len(used) == required else RvolStatus.PARTIAL
    return RvolResult(numerator / baseline, status, len(used), required, adjusted)
