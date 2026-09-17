"""Research-only halt inference from gaps in the minute tape.

Basic data has no halt feed, so an exact halt history cannot be reproduced. This module
raises a flag when a tape looks like a LULD pause: a gap of ``min_gap_minutes`` to
``max_gap_minutes`` between two actual bars in the same session, a large reopening move and a
heavy reopening bar. The thresholds are research defaults in ``HaltInferenceConfig``, not a
strategy rule, and an illiquid tape can trip them without any halt.

``gap_minutes`` is the bar-open difference, so consecutive bars have gap 1.

Liquidity is not judged here. A thin tape is never rejected by this module; it just tends to
yield ``UNKNOWN`` (no earlier bar volume to compare, or a silence that could be a halt in
progress). Whether low liquidity excludes a symbol is a separate filter on the dollar-volume
and density features, applied later by a strategy adapter. ``UNKNOWN`` is not a halt and is
not "no halt".

PIT: a gap is judged only once its post-gap bar is available. While the tape is silent for
a halt-length stretch and the next bar has not arrived, the answer is ``UNKNOWN``, because
a halt could be in progress. Gap events are indexed once per tape and config, and a query
is a few bisects rather than a rescan.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.strategy_b.config import HaltInferenceConfig
from app.strategy_b.features import SessionTape
from app.strategy_b.models import AVAILABILITY_DELAY, BAR_INTERVAL, HaltInference, HaltStatus


@dataclass(frozen=True, slots=True)
class HaltGapEvidence:
    """The rule contract. Anything that can supply these four numbers can be judged."""

    gap_minutes: int
    post_gap_return_pct: float
    post_gap_volume: float
    reference_bar_volume: float | None
    """Mean actual-bar volume earlier in the same session, or None when there is none."""


@dataclass(frozen=True, slots=True)
class HaltGapEvent:
    gap_end: datetime
    available_at: datetime
    status: HaltStatus


@dataclass(frozen=True, slots=True)
class _GapIndex:
    events: tuple[HaltGapEvent, ...]
    gap_ends: tuple[datetime, ...]
    available: tuple[datetime, ...]
    last_inferred: tuple[int, ...]
    """``last_inferred[i]`` is the index of the last HALT_INFERRED event among events[:i+1], or -1."""
    unknown_count: tuple[int, ...]
    """Prefix count of UNKNOWN events, length len(events) + 1."""


def judge_gap(evidence: HaltGapEvidence, config: HaltInferenceConfig) -> HaltStatus:
    if not config.min_gap_minutes <= evidence.gap_minutes <= config.max_gap_minutes:
        return HaltStatus.NO_HALT_SIGNAL
    if evidence.reference_bar_volume is None or evidence.reference_bar_volume <= 0:
        return HaltStatus.UNKNOWN
    if (abs(evidence.post_gap_return_pct) >= config.min_abs_post_gap_return_pct
            and evidence.post_gap_volume / evidence.reference_bar_volume >= config.min_post_gap_volume_ratio):
        return HaltStatus.HALT_INFERRED
    return HaltStatus.NO_HALT_SIGNAL


def gap_events(tape: SessionTape, config: HaltInferenceConfig) -> tuple[HaltGapEvent, ...]:
    """Every same-session gap of at least ``min_gap_minutes``, each judged from its own past."""
    events: list[HaltGapEvent] = []
    session_first = 0
    bars = tape.bars
    for index in range(1, len(bars)):
        before, after = bars[index - 1], bars[index]
        if after.session is not before.session:
            session_first = index
            continue
        gap = int((after.timestamp - before.timestamp) // BAR_INTERVAL)
        if gap < config.min_gap_minutes:
            continue
        evidence = HaltGapEvidence(
            gap_minutes=gap,
            post_gap_return_pct=(after.open / before.close - 1) * 100,
            post_gap_volume=after.volume,
            reference_bar_volume=tape.volume(session_first, index) / (index - session_first),
        )
        events.append(HaltGapEvent(after.timestamp, after.available_at, judge_gap(evidence, config)))
    return tuple(events)


def _gap_index(config: HaltInferenceConfig) -> Callable[[SessionTape], _GapIndex]:
    def build(tape: SessionTape) -> _GapIndex:
        events = gap_events(tape, config)
        last_inferred: list[int] = []
        unknown = [0]
        latest = -1
        for i, event in enumerate(events):
            if event.status is HaltStatus.HALT_INFERRED:
                latest = i
            last_inferred.append(latest)
            unknown.append(unknown[-1] + (event.status is HaltStatus.UNKNOWN))
        return _GapIndex(events, tuple(e.gap_end for e in events),
                         tuple(e.available_at for e in events), tuple(last_inferred), tuple(unknown))
    return build


def infer_halt(tape: SessionTape, as_of: datetime, config: HaltInferenceConfig) -> HaltInference:
    """Halt flag for the session ``as_of`` is in, from bars available at ``as_of`` only."""
    session = tape.boundaries.classify(as_of)
    start = tape.boundaries.session_start(session)
    if start is None:
        return HaltInference(HaltStatus.UNKNOWN, None, None)
    first, now = tape.first_index_at_or_after(start), tape.cut(as_of)
    if now <= first:
        return HaltInference(HaltStatus.UNKNOWN, None, None)

    index: _GapIndex = tape.derived(("halt_gap_index", config), _gap_index(config))
    lo, hi = bisect_left(index.gap_ends, start), bisect_right(index.available, as_of)
    if hi > lo and index.last_inferred[hi - 1] >= lo:
        return HaltInference(HaltStatus.HALT_INFERRED, index.events[index.last_inferred[hi - 1]].gap_end, None)

    last = tape.bars[now - 1]
    latest_slot = (as_of - AVAILABILITY_DELAY).replace(second=0, microsecond=0)
    open_gap = int((latest_slot - last.timestamp) // BAR_INTERVAL) + 1
    if config.min_gap_minutes <= open_gap <= config.max_gap_minutes:
        return HaltInference(HaltStatus.UNKNOWN, None, open_gap)
    if hi > lo and index.unknown_count[hi] - index.unknown_count[lo]:
        return HaltInference(HaltStatus.UNKNOWN, None, None)
    return HaltInference(HaltStatus.NO_HALT_SIGNAL, None, None)
