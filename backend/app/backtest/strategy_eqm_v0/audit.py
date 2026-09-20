"""EQM-V0 PIT audit: every number used was public at the accession that carried it.

C-E0 audited when a filing became usable. EQM-V0 has to audit one thing more: the *figures*. A
revenue growth rate is only point-in-time if the two revenue facts behind it were tagged by an
accession that was itself inside W_PRIMARY, so the audit re-derives the accession's placement and
re-checks the accession number on every fact that entered the feature. The shift and the synthetic
positive control are the same two devices C-E0 used, because an audit that cannot fail is not an
audit.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.backtest.strategy_c_e0.pit import SessionGrid, place

W_PRIMARY_BACK = 2


@dataclass(frozen=True)
class UsedFact:
    """One row's feature provenance, as the run assembled it."""

    date_idx: int
    ticker: str
    cik: str
    accession: str
    acceptance: datetime          # the filing's acceptance time, in ET
    fact_accessions: tuple[str, ...]  # accession of every fact that entered the feature
    event_sessions: tuple[int, ...]   # sessions of the row's W_PRIMARY events, from the event view


def in_window(grid: SessionGrid, d_index: int, session_index: int) -> bool:
    return max(0, d_index - W_PRIMARY_BACK) <= session_index <= d_index


def audit(records: Sequence[UsedFact], grid: SessionGrid, *, control_sample: int = 25) -> dict:
    violations: list[dict] = []
    shifted_changed = shifted_unexplained = 0
    for record in records:
        placement = place(grid, record.acceptance)
        if placement is None or not in_window(grid, record.date_idx, placement.session_index):
            violations.append({"kind": "ACCESSION_OUTSIDE_WINDOW", "ticker": record.ticker,
                               "date_idx": record.date_idx, "accession": record.accession})
        elif placement.effective_time >= grid.close_of(record.date_idx):
            violations.append({"kind": "EFFECTIVE_TIME_AT_OR_AFTER_CLOSE", "ticker": record.ticker,
                               "date_idx": record.date_idx, "accession": record.accession})
        wrong = [a for a in record.fact_accessions if a != record.accession]
        if wrong:
            violations.append({"kind": "FACT_FROM_ANOTHER_ACCESSION", "ticker": record.ticker,
                               "date_idx": record.date_idx, "accession": record.accession,
                               "foreign": sorted(set(wrong))[:3]})
        if record.date_idx not in record.event_sessions and not any(
                in_window(grid, record.date_idx, s) for s in record.event_sessions):
            violations.append({"kind": "NOT_A_WINDOW_EVENT", "ticker": record.ticker,
                               "date_idx": record.date_idx, "accession": record.accession})

        shifted = _shift_one_session(grid, record.acceptance, record.date_idx)
        if shifted is None:
            continue
        before = placement is not None and in_window(grid, record.date_idx, placement.session_index)
        after = in_window(grid, record.date_idx, shifted)
        if before != after:
            shifted_changed += 1
            # The only legitimate reason to change is leaving the window at its far edge.
            if not (before and not after and shifted > record.date_idx):
                shifted_unexplained += 1

    detected = 0
    for record in records[:control_sample]:
        synthetic = grid.close_of(record.date_idx) + timedelta(seconds=1)
        placement = place(grid, synthetic)
        # A filing accepted one second after the close must not be usable on D.
        if placement is None or placement.session_index > record.date_idx:
            detected += 1

    return {
        "rows_audited": len(records),
        "violations": len(violations),
        "violation_examples": violations[:10],
        "violation_kinds": sorted({v["kind"] for v in violations}),
        "shift_plus_one_changed": shifted_changed,
        "shift_plus_one_unexplained": shifted_unexplained,
        "positive_control_sample": min(control_sample, len(records)),
        "positive_control_detected": detected,
        "positive_control_pass": detected == min(control_sample, len(records)) and bool(records),
    }


def _shift_one_session(grid: SessionGrid, acceptance: datetime, d_index: int) -> int | None:
    """Where the same filing would land if its acceptance moved one session later."""
    placement = place(grid, acceptance)
    if placement is None:
        return None
    nxt = placement.session_index + 1
    return nxt if nxt < len(grid.sessions) else None
