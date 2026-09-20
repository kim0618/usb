"""Candidate row -> event quality raw features, on the frozen C-E0 event view.

One candidate row is one C-M0 candidate (`date_idx`, `ticker`). Its quality features come from the
periodic report that already made it an EM row in C-E0: no new event definition, no new window, no
new candidate. The accession used is chosen by a rule fixed before any outcome was read - earliest
effective time first, and within a tie by accession number - so the choice can never drift with a
result. Every status a row can end in is recorded, because the coverage gate is decided on those
statuses alone.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from app.backtest.strategy_c_e0.events import CikEvents, Event
from app.backtest.strategy_c_e0.pit import SessionGrid
from app.backtest.strategy_c_e0.taxonomy import NEGATIVE_RISK_CLASSES
from app.backtest.strategy_eqm_v0 import quality

PERIODIC_FORMS = ("10-K", "10-KT", "10-Q")
W_PRIMARY_BACK = 2          # C-E0's window, unchanged
NOVELTY_WINDOWS = (90, 180)  # calendar days before the window, raw feature only
FINANCING_LOOKBACK_DAYS = 60
E3 = "E3"  # C-E0 dedup class of a results event; E4a..E4d are the financing/distress classes

NO_EVENT = "NO_PERIODIC_EVENT"      # EM row whose W_PRIMARY holds no 10-Q/10-K/10-KT
NO_FACTS_DOC = "NO_FACTS_DOCUMENT"  # the filer has no companyfacts document at all
OBSERVABLE = "OBSERVABLE"           # a growth rate was computed


@dataclass
class QualityRow:
    date_idx: int
    ticker: str
    cik: str | None
    signal_date: str
    status: str                       # the C-E0 cohort status, carried through unchanged
    quality_status: str               # OBSERVABLE, or why not
    accession: str | None = None
    accession_form: str | None = None
    accession_accepted: str | None = None
    accessions_considered: int = 0
    accession_statuses: tuple[str, ...] = field(default_factory=tuple)
    revenue_tag: str | None = None
    period_label: str | None = None
    period_days: int | None = None
    period_end: str | None = None
    revenue: float | None = None
    revenue_prior_year: float | None = None
    revenue_growth_yoy: float | None = None
    net_income: float | None = None
    net_income_prior_year: float | None = None
    gross_profit: float | None = None
    eps: float | None = None
    shares_outstanding: float | None = None
    same_class_count_90d: int = 0
    same_class_count_180d: int = 0
    recent_dilution_20: bool = False
    financing_event_60d: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def periodic_accessions(view: CikEvents, grid: SessionGrid, d_index: int,
                        forms_by_accession: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, Event]]:
    """W_PRIMARY periodic-report accessions, earliest effective time first, ties by accession."""
    window = range(max(0, d_index - W_PRIMARY_BACK), d_index + 1)
    found: list[tuple[Any, str, Event]] = []
    for event in view.classes_in(window):
        if event.event_class != E3:
            continue
        for accession in event.accessions:
            row = forms_by_accession.get(accession)
            if row is not None and str(row.get("form")) in PERIODIC_FORMS:
                found.append((event.effective_time, accession, event))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(accession, event) for _moment, accession, event in found]


def novelty_counts(view: CikEvents, grid: SessionGrid, d_index: int, event_class: str = E3) -> dict[int, int]:
    """Same-class events strictly before W_PRIMARY, within N calendar days of the signal date.

    A low count is not read as `new` or `good`: it is stored raw, exactly as the declaration says.
    """
    signal_day = grid.sessions[d_index]
    window_start = grid.sessions[max(0, d_index - W_PRIMARY_BACK)]
    out: dict[int, int] = {}
    for days in NOVELTY_WINDOWS:
        floor = signal_day - timedelta(days=days)
        out[days] = sum(1 for e in view.events
                        if e.event_class == event_class
                        and floor <= grid.sessions[e.session_index] < window_start)
    return out


def financing_within(view: CikEvents, grid: SessionGrid, d_index: int,
                     days: int = FINANCING_LOOKBACK_DAYS) -> bool:
    """Any financing, dilution or distress event (C-E0 classes E4a..E4d) in [D-days, D]."""
    signal_day = grid.sessions[d_index]
    floor = signal_day - timedelta(days=days)
    return any(e.event_class in NEGATIVE_RISK_CLASSES
               and floor <= grid.sessions[e.session_index] <= signal_day
               for e in view.events)


def build_row(*, date_idx: int, ticker: str, cik: str | None, status: str, grid: SessionGrid,
              view: CikEvents | None, forms_by_accession: Mapping[str, Mapping[str, Any]],
              facts_document: Mapping[str, Any] | None, recent_dilution_20: bool) -> QualityRow:
    signal_date = grid.sessions[date_idx].isoformat()
    row = QualityRow(date_idx=date_idx, ticker=ticker, cik=cik, signal_date=signal_date,
                     status=status, quality_status=NO_EVENT,
                     recent_dilution_20=bool(recent_dilution_20))
    if view is None or cik is None:
        return row
    counts = novelty_counts(view, grid, date_idx)
    row.same_class_count_90d = counts[90]
    row.same_class_count_180d = counts[180]
    row.financing_event_60d = financing_within(view, grid, date_idx)
    ordered = periodic_accessions(view, grid, date_idx, forms_by_accession)
    if not ordered:
        return row
    row.accessions_considered = len(ordered)
    if facts_document is None:
        row.quality_status = NO_FACTS_DOC
        return row
    statuses: list[str] = []
    for accession, _event in ordered:
        extracted = quality.extract(quality.facts_of_accession(facts_document, accession), accession)
        statuses.append(extracted.status)
        if extracted.status == quality.OK:
            _fill(row, extracted, forms_by_accession.get(accession))
            row.quality_status = OBSERVABLE
            row.accession_statuses = tuple(statuses)
            return row
    # No accession produced a comparable pair: the first one's reason is the row's reason.
    row.quality_status = statuses[0]
    row.accession_statuses = tuple(statuses)
    row.accession = ordered[0][0]
    filing = forms_by_accession.get(ordered[0][0]) or {}
    row.accession_form = str(filing.get("form")) if filing else None
    row.accession_accepted = str(filing.get("acceptanceDateTime") or "") or None
    return row


def _fill(row: QualityRow, extracted: quality.QualityFacts,
          filing: Mapping[str, Any] | None) -> None:
    row.accession = extracted.accession
    row.accession_form = str((filing or {}).get("form")) if filing else None
    row.accession_accepted = str((filing or {}).get("acceptanceDateTime") or "") or None
    row.revenue_tag = extracted.revenue_tag
    row.period_label = extracted.period_label
    row.period_days = extracted.period_days
    row.period_end = extracted.period_end.isoformat() if extracted.period_end else None
    row.revenue = extracted.revenue
    row.revenue_prior_year = extracted.revenue_prior_year
    row.revenue_growth_yoy = extracted.revenue_growth_yoy
    row.net_income = extracted.net_income
    row.net_income_prior_year = extracted.net_income_prior_year
    row.gross_profit = extracted.gross_profit
    row.eps = extracted.eps
    row.shares_outstanding = extracted.shares_outstanding


def accession_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """accession number -> its submissions row (form, items, acceptanceDateTime)."""
    return {str(r.get("accessionNumber")): r for r in rows if r.get("accessionNumber")}
