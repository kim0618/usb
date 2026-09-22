"""The pre-registered lift gate: did the breakout moment predict anything the no-setup moment did not?

B-F0 2절 states the falsification: if candidates that passed the scanner gate but never formed
a setup do just as well, B-V1 is rejected. This module turns that sentence into a computation
that was fixed before any result existed.

Two choices here are worth naming, because both were made against the easier alternative:

* the treatment cohort is every candidate that *signalled*, not every trade that filled. A fill
  is filtered by account capacity, which is a fact about the wallet, not about the setup.
* the comparison is forward return, not profit. The control has no setup and therefore no stop,
  so giving it an R multiple would mean inventing a rule for it. Forward return needs no rule.

Forward return reads bars after the decision, so it is AUDIT ONLY: nothing here may ever flow
back into the FSM, the scanner or sizing.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from app.backtest.strategy_b_e0.contract import Contract, Statistics
from app.backtest.strategy_b_e0.gate import Interval, LiftOutcome
from app.backtest.strategy_b_e0.metrics import SessionSeries, difference_interval, draws, series
from app.strategy_b.features import SessionTape
from app.strategy_b.fsm import Candidate, DropReason
from app.strategy_b.models import CandidateState

#: How a candidate's forward return turned out, or why it has none.
class AnchorStatus(StrEnum):
    OK = "OK"
    DROPPED_NO_REFERENCE_BAR = "DROPPED_NO_REFERENCE_BAR"
    ZERO_BY_NO_FORWARD_BAR = "ZERO_BY_NO_FORWARD_BAR"


class Cohort(StrEnum):
    TREATMENT = "TREATMENT"
    CONTROL = "CONTROL"


@dataclass(frozen=True, slots=True)
class Anchor:
    symbol: str
    session: date
    cohort: Cohort
    at: datetime
    status: AnchorStatus
    forward_return_pct: float | None
    score: float

    @property
    def counts(self) -> bool:
        """Only a measured anchor enters the statistic; a dropped one never had a price."""
        return self.status is not AnchorStatus.DROPPED_NO_REFERENCE_BAR


def classify(candidate: Candidate) -> Cohort | None:
    """Which cohort a finished candidate belongs to, or None if it belongs to neither.

    The rule is exactly the contract's: signalling puts a candidate in treatment whatever
    happened next, and only a qualified candidate that ran out its candidate TTL without ever
    arming a setup is a control. Everything else either formed a setup, never qualified, or
    left for a reason that says nothing about the hypothesis.
    """
    if candidate.signal_at is not None:
        return Cohort.TREATMENT
    if candidate.setup_ready_at is not None:
        return None  # a setup did form; not a no-setup control
    if (candidate.state is CandidateState.EXPIRED
            and candidate.drop_reason is DropReason.CANDIDATE_TTL
            and candidate.qualified_at is not None):
        return Cohort.CONTROL
    return None


def anchor_time(candidate: Candidate, cohort: Cohort) -> datetime:
    if cohort is Cohort.TREATMENT:
        assert candidate.signal_at is not None
        return candidate.signal_at
    return candidate.updated_at


def last_close_at_or_before(tape: SessionTape, instant: datetime) -> float | None:
    """The close of the last actual bar available at ``instant``, or None if there is none.

    'Available' is B's one availability rule (bar open + AVAILABILITY_DELAY), the same cut every
    other read of a tape uses, so this measurement never sees a price a decision could not have
    seen at the same instant. F0 9.1 fixes the same convention for time-stop and EOD fills.
    """
    end = tape.cut(instant)
    return None if end <= 0 else tape.bars[end - 1].close


def measure(candidate: Candidate, cohort: Cohort, tape: SessionTape, *, horizon_minutes: int,
            eod_exit_at: datetime) -> Anchor:
    """One anchor's forward return, or the reason it has none."""
    at = anchor_time(candidate, cohort)
    reference = last_close_at_or_before(tape, at)
    if reference is None or reference <= 0:
        return Anchor(candidate.symbol, tape.boundaries.session_date, cohort, at,
                      AnchorStatus.DROPPED_NO_REFERENCE_BAR, None, candidate.score)
    horizon_end = min(at + timedelta(minutes=horizon_minutes), eod_exit_at)
    endpoint = last_close_at_or_before(tape, horizon_end)
    if endpoint is None or endpoint == reference:
        # No later observation: the tape went quiet rather than the price standing still. The
        # value is 0.0 so the anchor still counts, and the status keeps it visible in the report,
        # because a cohort built mostly of these is a silence, not an outcome.
        return Anchor(candidate.symbol, tape.boundaries.session_date, cohort, at,
                      AnchorStatus.ZERO_BY_NO_FORWARD_BAR, 0.0, candidate.score)
    return Anchor(candidate.symbol, tape.boundaries.session_date, cohort, at, AnchorStatus.OK,
                  (endpoint / reference - 1.0) * 100.0, candidate.score)


@dataclass(frozen=True, slots=True)
class LiftResult:
    outcome: LiftOutcome
    difference: Interval | None
    treatment_n: int
    control_n: int
    dropped: Mapping[str, int]
    zero_by_no_forward_bar: Mapping[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": str(self.outcome),
            "difference": None if self.difference is None else {
                "point": self.difference.point,
                "ci_lower": self.difference.lower,
                "ci_upper": self.difference.upper},
            "treatment_anchors": self.treatment_n,
            "control_anchors": self.control_n,
            "dropped_no_reference_bar": dict(self.dropped),
            "zero_by_no_forward_bar": dict(self.zero_by_no_forward_bar),
        }


def evaluate(anchors: Sequence[Anchor], sessions: Sequence[date], contract: Contract,
             drawn=None) -> LiftResult:
    """The declared comparison, in the declared order: sample first, then the interval."""
    stats: Statistics = contract.statistics
    counted = [anchor for anchor in anchors if anchor.counts]
    treatment = _cohort_series(counted, Cohort.TREATMENT, sessions)
    control = _cohort_series(counted, Cohort.CONTROL, sessions)
    dropped = _tally(anchors, AnchorStatus.DROPPED_NO_REFERENCE_BAR)
    zeros = _tally(anchors, AnchorStatus.ZERO_BY_NO_FORWARD_BAR)

    if treatment.n < contract.lift_min_treatment or control.n < contract.lift_min_control:
        return LiftResult(LiftOutcome.LIFT_INSUFFICIENT, None, treatment.n, control.n,
                          dropped, zeros)
    drawn = draws(len(sessions), stats) if drawn is None else drawn
    difference = difference_interval(treatment, control, stats, drawn)
    outcome = LiftOutcome.LIFT_PASS if difference.lower > 0 else LiftOutcome.LIFT_FAIL
    return LiftResult(outcome, difference, treatment.n, control.n, dropped, zeros)


def _cohort_series(anchors: Sequence[Anchor], cohort: Cohort,
                   sessions: Sequence[date]) -> SessionSeries:
    by_session: dict[date, list[float]] = {}
    for anchor in anchors:
        if anchor.cohort is cohort and anchor.forward_return_pct is not None:
            by_session.setdefault(anchor.session, []).append(anchor.forward_return_pct)
    return series(by_session, sessions)


def _tally(anchors: Sequence[Anchor], status: AnchorStatus) -> dict[str, int]:
    out: dict[str, int] = {}
    for anchor in anchors:
        if anchor.status is status:
            key = str(anchor.cohort)
            out[key] = out.get(key, 0) + 1
    return out


def collect(finished: Sequence[Candidate], tapes: Callable[[str], SessionTape | None], *,
            contract: Contract, eod_exit_at: datetime) -> list[Anchor]:
    """Every anchor one session contributes, in symbol order for determinism."""
    out: list[Anchor] = []
    for candidate in sorted(finished, key=lambda item: (item.symbol, item.detected_at)):
        cohort = classify(candidate)
        if cohort is None:
            continue
        tape = tapes(candidate.symbol)
        if tape is None:
            continue  # the contract excludes candidates whose session is missing from the data
        out.append(measure(candidate, cohort, tape,
                           horizon_minutes=contract.lift_horizon_minutes,
                           eod_exit_at=eod_exit_at))
    return out
