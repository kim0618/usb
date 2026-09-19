"""One status per C-M0 candidate row, first matching rule wins.

Order (`c_e0_rules_v1.json > event_status_and_cohorts.assignment_order`): UNKNOWN_MAPPING,
UNKNOWN_COVERAGE, UNKNOWN_PIT, UNKNOWN_FORM, EXCLUDED_MA_TARGET, EM_NEGATIVE_RISK, EM, M_ONLY.
Only EM and M_ONLY enter the hypotheses; everything else is counted and reported.

One reading had to be fixed here because the declaration names the status but not its bucket: an
8-K whose `items` field is empty is `UNKNOWN_ITEMS` in the taxonomy, i.e. a listed form whose
class cannot be decided. It is bucketed as UNKNOWN_FORM (excluded, counted, inside the P3 share)
and also counted separately as `unknown_items`, so the choice stays visible.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.backtest.strategy_c_e0.cik_map import Mapping as CikMapping
from app.backtest.strategy_c_e0.events import CikEvents
from app.backtest.strategy_c_e0.pit import DURING_SESSION, SessionGrid
from app.backtest.strategy_c_e0.taxonomy import MATERIAL_CLASSES, NEGATIVE_RISK_CLASSES

UNKNOWN_MAPPING = "UNKNOWN_MAPPING"
UNKNOWN_COVERAGE = "UNKNOWN_COVERAGE"
UNKNOWN_PIT = "UNKNOWN_PIT"
UNKNOWN_FORM = "UNKNOWN_FORM"
EXCLUDED_MA_TARGET = "EXCLUDED_MA_TARGET"
EM_NEGATIVE_RISK = "EM_NEGATIVE_RISK"
EM = "EM"
M_ONLY = "M_ONLY"
STATUSES = (UNKNOWN_MAPPING, UNKNOWN_COVERAGE, UNKNOWN_PIT, UNKNOWN_FORM, EXCLUDED_MA_TARGET,
            EM_NEGATIVE_RISK, EM, M_ONLY)
UNKNOWN_STATUSES = (UNKNOWN_MAPPING, UNKNOWN_COVERAGE, UNKNOWN_PIT, UNKNOWN_FORM)
W_PRIMARY_BACK = 2
W_D5_BACK, W_D1_BACK, W_D0_BACK = 5, 1, 0
MA_PIN_BACK = 60
RECENT_DILUTION_BACK = 20
ACTIVE_FILER_DAYS = 400
PENDING_BACK, PENDING_FORWARD = 3, 1


@dataclass(frozen=True)
class Coverage:
    """What the raw store holds for one CIK, decided before any return is read."""

    covered: bool
    status: str
    earliest_filing_date: date | None
    latest_filing_date: date | None


def window(d_index: int, back: int) -> range:
    return range(max(0, d_index - back), d_index + 1)


def assign(grid: SessionGrid, d_index: int, mapping: CikMapping, events: CikEvents | None,
           coverage: Coverage | None) -> dict[str, Any]:
    """The full row: status, event descriptors and the flags the declaration requires."""
    signal_date = grid.sessions[d_index]
    primary = window(d_index, W_PRIMARY_BACK)
    row: dict[str, Any] = {
        "signal_date": signal_date.isoformat(), "cik": mapping.cik, "cik_as_of": None,
        "event_count": 0, "event_types": [], "event_subtypes": [], "event_accessions": [],
        "negative_risk_flag": False, "negative_risk_types": [], "ma_target_pin_flag": False,
        "post_signal_event_flag": False, "recent_dilution_20": False, "routine_count": 0,
        "unknown_items_flag": False, "status_W_D5": None, "status_W_D1": None, "status_W_D0": None,
        "status_reason": None,
    }
    if mapping.as_of is not None:
        row["cik_as_of"] = mapping.as_of.isoformat()
    if mapping.cik is None:
        return {**row, "status": UNKNOWN_MAPPING, "status_reason": mapping.reason}
    if coverage is None or not coverage.covered:
        return {**row, "status": UNKNOWN_COVERAGE,
                "status_reason": coverage.status if coverage else "NO_FETCH"}
    if events is None:
        return {**row, "status": UNKNOWN_COVERAGE, "status_reason": "NO_ROWS"}
    if not _active_filer(events, signal_date):
        return {**row, "status": UNKNOWN_MAPPING, "status_reason": "NO_FILING_IN_400_DAYS"}

    primary_events = events.classes_in(primary)
    material = [e for e in primary_events if e.event_class in MATERIAL_CLASSES]
    negative = [e for e in primary_events if e.event_class in NEGATIVE_RISK_CLASSES]
    row.update({
        "event_count": len(material),
        "event_types": sorted({e.event_class for e in material}),
        "event_subtypes": sorted({s for e in material for s in e.subtypes}),
        "event_accessions": sorted({a for e in material for a in e.accessions}),
        "negative_risk_flag": bool(negative),
        "negative_risk_types": sorted({s for e in negative for s in e.subtypes}),
        "ma_target_pin_flag": bool(set(window(d_index, MA_PIN_BACK)) & events.ma_pin_sessions),
        "post_signal_event_flag": any(e.session_index == d_index + 1 and DURING_SESSION not in e.rules
                                      for e in events.events),
        "recent_dilution_20": any(e.event_class in NEGATIVE_RISK_CLASSES
                                  for e in events.classes_in(window(d_index, RECENT_DILUTION_BACK))),
        "routine_count": sum(events.routine_by_session.get(i, 0) for i in primary),
        "unknown_items_flag": bool(set(primary) & events.unknown_items_sessions),
        "status_W_D5": _window_status(events, d_index, W_D5_BACK),
        "status_W_D1": _window_status(events, d_index, W_D1_BACK),
        "status_W_D0": _window_status(events, d_index, W_D0_BACK),
    })
    pending = _pending_in_window(events, grid, d_index)
    if pending:
        return {**row, "status": UNKNOWN_PIT, "status_reason": pending}
    if set(primary) & events.unclassifiable_sessions:
        return {**row, "status": UNKNOWN_FORM, "status_reason": "UNCLASSIFIABLE_FORM"}
    if row["unknown_items_flag"]:
        return {**row, "status": UNKNOWN_FORM, "status_reason": "UNKNOWN_ITEMS"}
    if row["ma_target_pin_flag"]:
        return {**row, "status": EXCLUDED_MA_TARGET}
    if negative:
        return {**row, "status": EM_NEGATIVE_RISK}
    if material:
        return {**row, "status": EM}
    return {**row, "status": M_ONLY}


def _active_filer(events: CikEvents, signal_date: date) -> bool:
    latest = events.latest_filing_date
    if latest is None or events.earliest_filing_date is None:
        return False
    start = signal_date - timedelta(days=ACTIVE_FILER_DAYS)
    return events.earliest_filing_date <= signal_date and latest >= start


def _pending_in_window(events: CikEvents, grid: SessionGrid, d_index: int) -> str | None:
    """A listed-class filing without acceptance time whose filingDate sits in [D-3, D+1] sessions."""
    low = grid.sessions[max(0, d_index - PENDING_BACK)]
    high = grid.sessions[min(len(grid.sessions) - 1, d_index + PENDING_FORWARD)]
    hits = [p for p in events.missing_acceptance if low <= p.filing_date <= high]
    return f"MISSING_ACCEPTANCE:{hits[0].form}" if hits else None


def _window_status(events: CikEvents, d_index: int, back: int) -> str:
    sessions = window(d_index, back)
    rows = events.classes_in(sessions)
    if any(e.event_class in NEGATIVE_RISK_CLASSES for e in rows):
        return EM_NEGATIVE_RISK
    if any(e.event_class in MATERIAL_CLASSES for e in rows):
        return EM
    return M_ONLY


def status_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def unknown_share(rows: Sequence[Mapping[str, Any]]) -> float:
    if not rows:
        return 0.0
    unknown = sum(1 for row in rows if row["status"] in UNKNOWN_STATUSES)
    return unknown / len(rows)
