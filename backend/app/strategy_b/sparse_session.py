"""Sparse-session validation (HYBRID-S) and the synthetic minute-clock view.

Validation
----------
A's collector demands a bar in every minute (STRICT). Small caps never pass that. In the
feasibility audit one small cap had 0 of 251 complete regular sessions (median 80% of minutes
missing), and a symbol with $450M of dollar volume missed 68% of regular minutes while its
minute trade counts summed exactly to the daily trade count, so those minutes were real
silence, not loss. HYBRID-S accepts silence and checks the tape against the official daily
bar instead:

* first regular minute open == daily open, max minute high == daily high, min minute low ==
  daily low (relative ``price_tolerance``);
* Σ minute volume <= daily volume × (1 + ``volume_tolerance``);
* daily volume > 0 with an empty or zero-volume minute tape is suspect.

The official close is not compared: the last minute close and the official close legitimately
differ (observed 3.41 vs 2.80). Mismatches that line up with a split on the day, or with a
known split ratio, are ``CORPORATE_ACTION_SUSPECT``; other mismatches are ``API_LOSS_SUSPECT``.
The split-ratio match may use any split record the auditor holds, including later ones,
because an adjusted daily bar is exactly the thing it is trying to recognise; that is
acceptable only because the verdict is an audit, never a feature.

The verdict is **post-session only**. It needs the day's official daily bar, which does not
exist during the session, so it is an audit output and must never be an input to that day's
features or decisions. Intraday density lives in ``features.missing_minute_ratio``.

Synthetic clock view
--------------------
``minute_clock_view`` lays a tape onto a one-minute clock without changing it: actual bars
stay as they are, silent minutes become ``synthetic=True`` bars with the last close carried
forward and zero volume. Allowed uses: clock-based returns, volume windows, RVOL.
Forbidden uses: candlestick patterns, ATR, breakout/pullback bar detection, fill decisions;
those take actual bars and call ``models.require_actual_bars``.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.strategy_b.config import SparseValidationConfig
from app.strategy_b.features import SessionTape
from app.strategy_b.models import (
    BAR_INTERVAL, MomentumBar, OfficialDailyBar, Session, SessionVerdict, require_actual_bars,
)
from app.strategy_b.session import SessionBoundaries
from app.strategy_b.split_adjustment import SplitRecord


class ValidationFinding(StrEnum):
    MINUTE_TAPE_EMPTY = "MINUTE_TAPE_EMPTY"
    OPEN_MISMATCH = "OPEN_MISMATCH"
    HIGH_MISMATCH = "HIGH_MISMATCH"
    LOW_MISMATCH = "LOW_MISMATCH"
    VOLUME_EXCEEDS_DAILY = "VOLUME_EXCEEDS_DAILY"
    VOLUME_COVERAGE_LOW = "VOLUME_COVERAGE_LOW"
    MATCHES_SPLIT_RATIO = "MATCHES_SPLIT_RATIO"


@dataclass(frozen=True, slots=True)
class SessionValidation:
    session_date: date
    verdict: SessionVerdict
    findings: tuple[ValidationFinding, ...]
    minute_bar_count: int
    regular_minute_slots: int
    missing_minute_ratio: float
    volume_coverage: float | None
    split_on_day: bool


def validate_sparse_session(bars: Sequence[MomentumBar], daily: OfficialDailyBar,
                            boundaries: SessionBoundaries, *, splits: Iterable[SplitRecord] = (),
                            config: SparseValidationConfig) -> SessionValidation:
    """Audit one completed regular session against its official daily bar."""
    if daily.session_date != boundaries.session_date:
        raise ValueError("the daily bar and the session boundaries are for different dates")
    require_actual_bars(bars)
    regular = sorted((b for b in bars if boundaries.classify(b.timestamp) is Session.REGULAR),
                     key=lambda b: b.timestamp)
    if any(a.timestamp == b.timestamp for a, b in zip(regular, regular[1:])):
        raise ValueError("minute bars repeat a timestamp; the audit refuses to pick one")
    slots = int((boundaries.regular_close - boundaries.regular_open) // BAR_INTERVAL)
    day_splits = [s for s in splits if s.execution_date == boundaries.session_date]
    minute_volume = sum(b.volume for b in regular)

    def result(verdict: SessionVerdict, findings: list[ValidationFinding]) -> SessionValidation:
        return SessionValidation(
            session_date=boundaries.session_date, verdict=verdict, findings=tuple(findings),
            minute_bar_count=len(regular), regular_minute_slots=slots,
            missing_minute_ratio=1 - len(regular) / slots,
            volume_coverage=minute_volume / daily.volume if daily.volume > 0 else None,
            split_on_day=bool(day_splits))

    if not regular or minute_volume <= 0:
        if daily.volume <= 0:
            return result(SessionVerdict.EMPTY_SESSION, [])
        return result(SessionVerdict.API_LOSS_SUSPECT, [ValidationFinding.MINUTE_TAPE_EMPTY])

    findings: list[ValidationFinding] = []
    price_ratios: list[float] = []
    for finding, minute_value, daily_value in (
            (ValidationFinding.OPEN_MISMATCH, regular[0].open, daily.open),
            (ValidationFinding.HIGH_MISMATCH, max(b.high for b in regular), daily.high),
            (ValidationFinding.LOW_MISMATCH, min(b.low for b in regular), daily.low)):
        if abs(minute_value - daily_value) > config.price_tolerance * daily_value:
            findings.append(finding)
            price_ratios.append(minute_value / daily_value)
    volume_ratio = minute_volume / daily.volume if daily.volume > 0 else None
    if volume_ratio is None or volume_ratio > 1 + config.volume_tolerance:
        findings.append(ValidationFinding.VOLUME_EXCEEDS_DAILY)
    elif config.min_volume_coverage is not None and volume_ratio < config.min_volume_coverage:
        findings.append(ValidationFinding.VOLUME_COVERAGE_LOW)
    if not findings:
        return result(SessionVerdict.VERIFIED_SPARSE, [])

    split_factors = [f for s in splits for f in (s.price_factor, s.share_factor)]
    observed = price_ratios + ([volume_ratio] if volume_ratio is not None else [])
    if any(abs(r / f - 1) <= config.split_ratio_tolerance for r in observed for f in split_factors):
        findings.append(ValidationFinding.MATCHES_SPLIT_RATIO)
    if day_splits or ValidationFinding.MATCHES_SPLIT_RATIO in findings:
        return result(SessionVerdict.CORPORATE_ACTION_SUSPECT, findings)
    return result(SessionVerdict.API_LOSS_SUSPECT, findings)


def minute_clock_view(tape: SessionTape, *, start: datetime, as_of: datetime) -> tuple[MomentumBar, ...]:
    """Actual bars plus synthetic carry-forward minutes from ``start`` to what ``as_of`` can see.

    Only slots whose bar would be available at ``as_of`` are produced. A silent minute is
    omitted when no trade exists at or before it (on this tape, available at ``as_of``),
    because there is no price to carry. The last trade before ``start`` seeds the price.
    """
    boundaries = tape.boundaries
    slots = boundaries.elapsed_minute_slots(start, as_of)
    first, now = tape.first_index_at_or_after(start), tape.cut(as_of)
    price = tape.bars[first - 1].close if first > 0 and first - 1 < now else None
    view: list[MomentumBar] = []
    cursor = first
    for k in range(slots):
        moment = start + k * BAR_INTERVAL
        if cursor < now and tape.bars[cursor].timestamp == moment:
            bar = tape.bars[cursor]
            cursor += 1
            price = bar.close
            view.append(bar)
        elif price is not None:
            view.append(MomentumBar(moment, price, price, price, price, 0.0,
                                    boundaries.classify(moment), synthetic=True))
    return tuple(view)
