"""Event and event-quality context for every (session, ticker) of the analog library.

C-E0 and EQM-V0 computed these only for C-M0 candidate rows. C-4 needs them for the library too,
so the same readers, the same taxonomy, the same window and the same PIT rule are applied to the
whole base-eligible panel. Nothing here re-defines an event, a window or a quality status: the
definitions are imported, and the only new thing is the shape they are evaluated on.

`UNKNOWN` never becomes `NO_EVENT`. A row whose CIK is unmapped, whose filings are not in the
store, whose acceptance time is missing, whose form is unclassifiable, or which is an M&A target
carries the UNKNOWN category and the neutral value of every other event coordinate.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_c4_analog import stores
from app.backtest.strategy_c_e0 import events as c_events
from app.backtest.strategy_c_e0.cohorts import (ACTIVE_FILER_DAYS, MA_PIN_BACK, PENDING_BACK,
                                                PENDING_FORWARD, RECENT_DILUTION_BACK, W_PRIMARY_BACK)
from app.backtest.strategy_c_e0.pit import SessionGrid
from app.backtest.strategy_c_e0.taxonomy import MATERIAL_CLASSES, NEGATIVE_RISK_CLASSES, Taxonomy
from app.backtest.strategy_eqm_v0 import quality as eqm_quality
from app.backtest.strategy_eqm_v0.rows import FINANCING_LOOKBACK_DAYS, PERIODIC_FORMS

CATEGORIES = ("NO_EVENT", "RESULTS", "AGREEMENT", "ACQUISITION_DISPOSITION", "OTHER_MATERIAL",
              "NEGATIVE_RISK", "UNKNOWN")
CATEGORY_INDEX = {name: i for i, name in enumerate(CATEGORIES)}
CLASS_TO_CATEGORY = {"E3": "RESULTS", "E1": "AGREEMENT", "E2": "ACQUISITION_DISPOSITION",
                     "E5": "OTHER_MATERIAL"}
EVENT_AGE_CAP = 20
QUALITY_CODES = ("NO_PERIODIC_EVENT", "NO_FACTS_DOCUMENT", "OBSERVABLE", "NO_FACTS", "NO_REVENUE",
                 "NO_COMPARATIVE", "ZERO_BASE", "NEGATIVE_BASE")
QUALITY_INDEX = {name: i for i, name in enumerate(QUALITY_CODES)}
BUCKET_EDGES = ((None, 0.0), (0.0, 0.05), (0.05, 0.10), (0.10, 0.25), (0.25, 0.50), (0.50, None))


@dataclass(frozen=True)
class CikTables:
    """Per (CIK, session) event and quality state, before any ticker is attached."""

    names: tuple[str, ...]
    covered: np.ndarray            # (C,) bool
    material_count: np.ndarray     # (C, T) int16, material events accepted at that session
    class_flags: dict[str, np.ndarray]  # E1/E2/E3/E5/E4 -> (C, T) bool
    ma_pin: np.ndarray             # (C, T) bool
    unclassifiable: np.ndarray     # (C, T) bool
    unknown_items: np.ndarray      # (C, T) bool
    pending: np.ndarray            # (C, T) bool
    active: np.ndarray             # (C, T) bool
    periodic: np.ndarray           # (C, T) bool, an E3 event whose accession is a periodic report
    quality_status: np.ndarray     # (C, T) int8, index into QUALITY_CODES, -1 when no window entry
    revenue_yoy: np.ndarray        # (C, T) float32, NaN unless OBSERVABLE


@dataclass(frozen=True)
class EventContext:
    """(T, N) coordinates. NaN marks a value the UNKNOWN indicator is responsible for."""

    unknown: np.ndarray
    event_present: np.ndarray
    category: np.ndarray           # int8 index into CATEGORIES
    event_age: np.ndarray
    event_count: np.ndarray
    negative_risk: np.ndarray
    periodic_flag: np.ndarray
    quality_observable: np.ndarray
    revenue_yoy: np.ndarray
    revenue_bucket: np.ndarray


def _window_any(flags: np.ndarray, back: int) -> np.ndarray:
    """(C, T) bool: the flag is set anywhere in the session window [s-back, s]."""
    return _window_sum(flags.astype(np.int32), back) > 0


def _window_sum(values: np.ndarray, back: int) -> np.ndarray:
    rows, columns = values.shape
    padded = np.concatenate([np.zeros((rows, back), dtype=np.int64),
                             values.astype(np.int64)], axis=1)
    cumulative = np.cumsum(padded, axis=1)
    inclusive = cumulative[:, back:]
    exclusive = np.concatenate([np.zeros((rows, 1), dtype=np.int64),
                                cumulative[:, : columns - 1]], axis=1)
    return inclusive - exclusive


def _calendar_window_any(flags: np.ndarray, sessions: Sequence[date], days: int) -> np.ndarray:
    """(C, T) bool over [session_date - days, session_date], on the session grid."""
    ordinals = np.array([s.toordinal() for s in sessions])
    starts = np.searchsorted(ordinals, ordinals - days, side="left")
    cumulative = np.cumsum(flags.astype(np.int32), axis=1)
    total = cumulative.copy()
    positive = starts > 0
    total[:, positive] = cumulative[:, positive] - cumulative[:, starts[positive] - 1]
    return total > 0


def build_cik_tables(roots: stores.StoreRoots, names: Sequence[str], grid: SessionGrid,
                     taxonomy: Taxonomy, *, acceptance_zone: str, covered_ciks: set[str],
                     log=lambda _m: None) -> tuple[CikTables, dict[str, list[tuple[int, Any, str]]]]:
    """Event state per (CIK, session), plus the periodic accessions each CIK needs facts for."""
    sessions = grid.sessions
    t = len(sessions)
    c = len(names)
    material_count = np.zeros((c, t), dtype=np.int16)
    class_flags = {key: np.zeros((c, t), dtype=bool) for key in ("E1", "E2", "E3", "E5", "E4")}
    ma_pin = np.zeros((c, t), dtype=bool)
    unclassifiable = np.zeros((c, t), dtype=bool)
    unknown_items = np.zeros((c, t), dtype=bool)
    pending = np.zeros((c, t), dtype=bool)
    active = np.zeros((c, t), dtype=bool)
    periodic = np.zeros((c, t), dtype=bool)
    covered = np.zeros(c, dtype=bool)
    ordinals = np.array([s.toordinal() for s in sessions])
    periodic_accessions: dict[str, list[tuple[int, Any, str]]] = {}

    for index, cik in enumerate(names):
        if cik not in covered_ciks:
            continue
        rows = stores.read_rows(roots, cik)
        if not rows:
            continue
        view = c_events.build_cik_events(cik, rows, taxonomy, grid, acceptance_zone=acceptance_zone)
        covered[index] = True
        forms = {str(r.get("accessionNumber")): r for r in rows if r.get("accessionNumber")}
        for event in view.events:
            s = event.session_index
            if event.event_class in MATERIAL_CLASSES:
                material_count[index, s] += 1
                class_flags[event.event_class][index, s] = True
                if event.event_class == "E3":
                    hits = [a for a in event.accessions
                            if str((forms.get(a) or {}).get("form")) in PERIODIC_FORMS]
                    if hits:
                        periodic[index, s] = True
                        periodic_accessions.setdefault(cik, []).extend(
                            (s, event.effective_time, a) for a in hits)
            elif event.event_class in NEGATIVE_RISK_CLASSES:
                class_flags["E4"][index, s] = True
        for s in view.ma_pin_sessions:
            ma_pin[index, s] = True
        for s in view.unclassifiable_sessions:
            unclassifiable[index, s] = True
        for s in view.unknown_items_sessions:
            unknown_items[index, s] = True
        for filing in view.missing_acceptance:
            ordinal = filing.filing_date.toordinal()
            first = int(np.searchsorted(ordinals, ordinal, side="left"))
            last = int(np.searchsorted(ordinals, ordinal, side="right")) - 1
            low = max(0, first - PENDING_FORWARD)
            high = min(t - 1, last + PENDING_BACK)
            if low <= high:
                pending[index, low:high + 1] = True
        if view.earliest_filing_date is not None and view.latest_filing_date is not None:
            earliest = view.earliest_filing_date.toordinal()
            latest = view.latest_filing_date.toordinal()
            active[index] = (earliest <= ordinals) & (latest >= ordinals - ACTIVE_FILER_DAYS)
        if (index + 1) % 500 == 0:
            log(f"cik tables {index + 1}/{c}")
    tables = CikTables(tuple(names), covered, material_count, class_flags, ma_pin, unclassifiable,
                       unknown_items, pending, active, periodic,
                       np.full((c, t), -1, dtype=np.int8), np.full((c, t), np.nan, dtype=np.float32))
    return tables, periodic_accessions


def facts_by_accession(document: Mapping[str, Any], wanted: set[str]) -> dict[str, list]:
    """One pass over a companyfacts document, bucketing facts by the accession that carries them."""
    out: dict[str, list] = {accession: [] for accession in wanted}
    for taxonomy, tags in (document.get("facts") or {}).items():
        for tag, body in (tags or {}).items():
            for unit, rows in ((body or {}).get("units") or {}).items():
                for row in rows or ():
                    accession = str(row.get("accn"))
                    if accession not in out:
                        continue
                    value = row.get("val")
                    if value is None:
                        continue
                    out[accession].append(eqm_quality.Fact(
                        tag=str(tag), taxonomy=str(taxonomy), unit=str(unit),
                        start=_day(row.get("start")), end=_day(row.get("end")), value=float(value)))
    return out


def _day(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def fill_quality(tables: CikTables, roots: stores.StoreRoots,
                 periodic_accessions: Mapping[str, list[tuple[int, Any, str]]],
                 *, session_count: int, log=lambda _m: None) -> None:
    """EQM-V0 quality status and revenue growth for every (CIK, session) with a periodic report."""
    index_of = {name: i for i, name in enumerate(tables.names)}
    done = 0
    for cik, entries in periodic_accessions.items():
        row = index_of[cik]
        document = stores.read_facts(roots, cik)
        wanted = {accession for _s, _t, accession in entries}
        extracted: dict[str, Any] = {}
        if document is not None:
            bucketed = facts_by_accession(document, wanted)
            for accession in wanted:
                extracted[accession] = eqm_quality.extract(bucketed.get(accession, []), accession)
        per_session: dict[int, list[tuple[Any, str]]] = {}
        for session, moment, accession in entries:
            for s in range(session, min(session + W_PRIMARY_BACK, session_count - 1) + 1):
                per_session.setdefault(s, []).append((moment, accession))
        for s, candidates in per_session.items():
            candidates.sort()
            if document is None:
                tables.quality_status[row, s] = QUALITY_INDEX["NO_FACTS_DOCUMENT"]
                continue
            statuses = []
            resolved = False
            for _moment, accession in candidates:
                facts = extracted[accession]
                statuses.append(facts.status)
                if facts.status == eqm_quality.OK:
                    tables.quality_status[row, s] = QUALITY_INDEX["OBSERVABLE"]
                    tables.revenue_yoy[row, s] = np.float32(facts.revenue_growth_yoy)
                    resolved = True
                    break
            if not resolved:
                tables.quality_status[row, s] = QUALITY_INDEX.get(statuses[0], QUALITY_INDEX["NO_FACTS"])
        done += 1
        if done % 500 == 0:
            log(f"quality {done}/{len(periodic_accessions)}")


def project(tables: CikTables, cik_code: np.ndarray, sessions: Sequence[date]) -> EventContext:
    """(CIK, session) state -> (session, ticker) coordinates, under the C-E0 precedence."""
    t, n = cik_code.shape
    rows = np.arange(t)[:, None]
    safe = np.where(cik_code >= 0, cik_code, 0)

    def take(array: np.ndarray) -> np.ndarray:
        return array[safe, rows]

    primary_material = _window_sum(tables.material_count, W_PRIMARY_BACK)
    primary_e4 = _window_any(tables.class_flags["E4"], W_PRIMARY_BACK)
    primary_periodic = _window_any(tables.periodic, W_PRIMARY_BACK)
    ma = _window_any(tables.ma_pin, MA_PIN_BACK)
    unclass = _window_any(tables.unclassifiable, W_PRIMARY_BACK)
    unknown_items = _window_any(tables.unknown_items, W_PRIMARY_BACK)
    dilution = _window_any(tables.class_flags["E4"], RECENT_DILUTION_BACK)
    financing = _calendar_window_any(tables.class_flags["E4"], sessions, FINANCING_LOOKBACK_DAYS)
    class_primary = {key: _window_any(value, W_PRIMARY_BACK)
                     for key, value in tables.class_flags.items() if key != "E4"}

    material_index = np.where(tables.material_count > 0, np.arange(tables.material_count.shape[1]), -1)
    last_material = np.maximum.accumulate(material_index, axis=1)

    mapped = cik_code >= 0
    covered = np.zeros_like(mapped)
    covered[mapped] = tables.covered[cik_code[mapped]]
    unknown = (~mapped | ~covered | take(tables.pending) | unclass[safe, rows]
               | unknown_items[safe, rows] | ma[safe, rows] | ~take(tables.active))

    count = take(primary_material).astype(np.float64)
    e4_primary = primary_e4[safe, rows]
    present = (count > 0).astype(np.float64)
    category = np.full((t, n), CATEGORY_INDEX["NO_EVENT"], dtype=np.int8)
    for key in ("E5", "E2", "E1", "E3"):  # precedence: RESULTS wins, then AGREEMENT, then the rest
        category = np.where(class_primary[key][safe, rows], CATEGORY_INDEX[CLASS_TO_CATEGORY[key]],
                            category).astype(np.int8)
    category = np.where(e4_primary, CATEGORY_INDEX["NEGATIVE_RISK"], category).astype(np.int8)
    category = np.where(unknown, CATEGORY_INDEX["UNKNOWN"], category).astype(np.int8)

    last = take(last_material)
    age = np.where(last >= 0, rows - last, EVENT_AGE_CAP).astype(np.float64)
    age = np.minimum(age, EVENT_AGE_CAP)

    risk = (dilution[safe, rows] | financing[safe, rows]).astype(np.float64)
    periodic_flag = primary_periodic[safe, rows].astype(np.float64)
    status = take(tables.quality_status)
    observable = (status == QUALITY_INDEX["OBSERVABLE"]).astype(np.float64)
    yoy = take(tables.revenue_yoy).astype(np.float64)
    yoy = np.where(observable > 0, yoy, np.nan)
    bucket = _bucket_of(yoy)

    blank = unknown
    return EventContext(
        unknown=blank,
        event_present=np.where(blank, np.nan, present),
        category=category,
        event_age=np.where(blank, np.nan, age),
        event_count=np.where(blank, np.nan, count),
        negative_risk=np.where(blank, np.nan, risk),
        periodic_flag=np.where(blank, np.nan, periodic_flag),
        quality_observable=np.where(blank, np.nan, observable),
        revenue_yoy=np.where(blank, np.nan, yoy),
        revenue_bucket=np.where(blank, np.nan, bucket))


def _bucket_of(yoy: np.ndarray) -> np.ndarray:
    out = np.full(yoy.shape, np.nan)
    for position, (low, high) in enumerate(BUCKET_EDGES):
        mask = np.isfinite(yoy)
        if low is not None:
            mask = mask & (yoy >= low)
        if high is not None:
            mask = mask & (yoy < high)
        out[mask] = position / (len(BUCKET_EDGES) - 1)
    return out


def load_covered_ciks() -> set[str]:
    """CIKs whose submissions coverage the frozen C-E0 or the C-4 incremental fetch confirmed."""
    out: set[str] = set()
    for root in (stores.FROZEN_SEC_ROOT, stores.C4_SEC_ROOT):
        path = root / "store_manifest.json"
        if not path.exists():
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for entry in manifest.get("per_cik") or ():
            if entry.get("status") == "OK":
                out.add(str(entry["cik"]))
    return out
