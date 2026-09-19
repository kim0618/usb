"""Raw submissions -> PIT event rows per CIK, deduplicated by (CIK, class, event_session).

The dedup rule is the taxonomy's: one accession yields at most one event per class, several
accessions of the same CIK/class/session are one event keeping the earliest effective_time and
all accession numbers, E3a and E3b of the same session merge, and the same class on two sessions
stays two events. Filings the class table ignores are only counted.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_c_e0.pit import DURING_SESSION, Placement, SessionGrid, parse_acceptance, place
from app.backtest.strategy_c_e0.taxonomy import Classification, Taxonomy


@dataclass(frozen=True)
class Event:
    cik: str
    event_class: str
    session_index: int
    effective_time: datetime
    accessions: tuple[str, ...]
    forms: tuple[str, ...]
    subtypes: tuple[str, ...]
    rules: tuple[str, ...]


@dataclass(frozen=True)
class PendingFiling:
    """A listed-class filing with no acceptance time: it cannot be placed, only flagged."""

    cik: str
    accession: str
    form: str
    filing_date: date
    kind: str  # EVENT_CLASS | MA_PIN | UNCLASSIFIABLE | UNKNOWN_ITEMS


@dataclass
class CikEvents:
    cik: str
    events: tuple[Event, ...] = ()
    ma_pin_sessions: frozenset[int] = frozenset()
    unclassifiable_sessions: frozenset[int] = frozenset()
    unknown_items_sessions: frozenset[int] = frozenset()
    missing_acceptance: tuple[PendingFiling, ...] = ()
    routine_by_session: Mapping[int, int] = field(default_factory=dict)
    amendment_count: int = 0
    earliest_filing_date: date | None = None
    latest_filing_date: date | None = None
    form_counts: Mapping[str, int] = field(default_factory=dict)
    rows_read: int = 0
    beyond_grid: int = 0

    def classes_in(self, sessions: Iterable[int]) -> tuple[Event, ...]:
        wanted = set(sessions)
        return tuple(e for e in self.events if e.session_index in wanted)


def _dedup(cik: str, records: Sequence[tuple[str, int, datetime, str, str, str, str]]) -> tuple[Event, ...]:
    """records: (class, session, effective_time, accession, form, subtype, rule)."""
    grouped: dict[tuple[str, int], list[tuple[datetime, str, str, str, str]]] = {}
    for event_class, session, moment, accession, form, subtype, rule in records:
        grouped.setdefault((event_class, session), []).append((moment, accession, form, subtype, rule))
    events = []
    for (event_class, session), rows in sorted(grouped.items()):
        rows.sort()
        events.append(Event(cik=cik, event_class=event_class, session_index=session,
                            effective_time=rows[0][0],
                            accessions=tuple(dict.fromkeys(r[1] for r in rows)),
                            forms=tuple(dict.fromkeys(r[2] for r in rows)),
                            subtypes=tuple(dict.fromkeys(r[3] for r in rows)),
                            rules=tuple(dict.fromkeys(r[4] for r in rows))))
    return tuple(events)


def build_cik_events(cik: str, rows: Iterable[Mapping[str, Any]], taxonomy: Taxonomy, grid: SessionGrid,
                     *, acceptance_zone: str) -> CikEvents:
    records: list[tuple[str, int, datetime, str, str, str, str]] = []
    ma, unclass, unknown_items = set(), set(), set()
    pending: list[PendingFiling] = []
    routine: dict[int, int] = {}
    forms: dict[str, int] = {}
    amendments = beyond = seen = 0
    earliest = latest = None
    for row in rows:
        seen += 1
        form = str(row.get("form") or "")
        forms[form] = forms.get(form, 0) + 1
        filing_date = date.fromisoformat(str(row["filingDate"])) if row.get("filingDate") else None
        if filing_date is not None:
            earliest = filing_date if earliest is None else min(earliest, filing_date)
            latest = filing_date if latest is None else max(latest, filing_date)
        classification = taxonomy.classify(form, row.get("items"))
        amendments += int(classification.amendment)
        accession = str(row.get("accessionNumber") or "")
        acceptance = str(row.get("acceptanceDateTime") or "").strip()
        kind = ("EVENT_CLASS" if classification.classes else
                "MA_PIN" if classification.ma_pin else
                "UNCLASSIFIABLE" if classification.unclassifiable else
                "UNKNOWN_ITEMS" if classification.unknown_items else None)
        if kind is None:
            if filing_date is not None:
                placement = _place_or_none(grid, acceptance, acceptance_zone, filing_date)
                if placement is not None:
                    routine[placement.session_index] = routine.get(placement.session_index, 0) + 1
            continue
        if not acceptance:
            if filing_date is not None:
                pending.append(PendingFiling(cik, accession, form, filing_date, kind))
            continue
        placement = place(grid, parse_acceptance(acceptance, acceptance_zone))
        if placement is None:
            beyond += 1
            continue
        if classification.ma_pin:
            ma.add(placement.session_index)
            continue
        if classification.unclassifiable:
            unclass.add(placement.session_index)
            continue
        if classification.unknown_items:
            unknown_items.add(placement.session_index)
            continue
        for event_class, subtype in zip(classification.classes, classification.subtypes):
            records.append((event_class, placement.session_index, placement.effective_time,
                            accession, form, subtype, placement.rule))
    return CikEvents(cik, _dedup(cik, records), frozenset(ma), frozenset(unclass), frozenset(unknown_items),
                     tuple(pending), routine, amendments, earliest, latest, forms, seen, beyond)


def _place_or_none(grid: SessionGrid, acceptance: str, zone: str, filing_date: date) -> Placement | None:
    if not acceptance:
        return None
    return place(grid, parse_acceptance(acceptance, zone))


def read_cik_rows(root: Path, cik: str) -> list[dict[str, Any]]:
    """Every stored submissions page of one CIK, deduplicated by accession number."""
    directory = root / "submissions" / f"CIK{cik}"
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json.gz")):
        if not sec_store.ledger_path(path).exists():
            continue
        for row in sec_store.rows_of(sec_store.read_gz_json(path)):
            accession = str(row.get("accessionNumber") or "")
            rows.setdefault(accession or f"_{len(rows)}", row)
    return list(rows.values())


def is_during_session(event: Event) -> bool:
    return DURING_SESSION in event.rules


def classification_of(taxonomy: Taxonomy, form: str, items: str | None) -> Classification:
    return taxonomy.classify(form, items)
