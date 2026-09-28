"""D2 deterministic filing selection policy.

Operates on rows already shaped by `strategy_c_e0.sec_store.rows_of` (accessionNumber, form,
filingDate, acceptanceDateTime, items, primaryDocument) - no new SEC parsing, this module only
decides which of the already-local rows to select and enforces the PIT cutoff. Thresholds are
justified by research usefulness, never chosen or adjusted by looking at any outcome
(D1.1 brief §9's "no forward return" discipline extends to this module too).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

MAX_RECENT_10Q = 4
"""A year's worth of quarterly filings - enough for E2's own 4-period trend window (D1's
`change_detection.py` already looks back at most 4 comparable periods) without pulling in
unbounded history a 1-3 month research thesis horizon would never need."""

MATERIAL_8K_WINDOW_DAYS = 180
"""Twice D0's stated thesis horizon (weeks to 3 months, `H_V2_D0` §R). An 8-K whose effects are
still unfolding at the start of a new thesis is worth surfacing; older ones are stale context, not
a current catalyst - the interpretation of *whether* it still matters is D3's job, this window only
bounds what gets offered."""


def _parse_acceptance(row: dict[str, Any]) -> datetime | None:
    raw = row.get("acceptanceDateTime")
    if not raw:
        return None
    try:
        stamp = str(raw).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_filing_date(row: dict[str, Any]) -> date | None:
    raw = row.get("filingDate")
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


@dataclass(frozen=True)
class FilingSelection:
    latest_10k: dict[str, Any] | None
    recent_10q: tuple[dict[str, Any], ...]
    recent_8k: tuple[dict[str, Any], ...]
    excluded_future: int
    """Count of rows excluded only because their acceptance time is after `data_cutoff` (or is
    unparseable) - a PIT audit figure, not a data-quality complaint about the filer."""


def select_filings(rows: list[dict[str, Any]], *, data_cutoff: datetime) -> FilingSelection:
    if data_cutoff.tzinfo is None:
        raise ValueError("data_cutoff must be timezone-aware")

    pit_eligible: list[tuple[dict[str, Any], datetime]] = []
    excluded_future = 0
    for row in rows:
        accepted = _parse_acceptance(row)
        if accepted is None:
            excluded_future += 1  # unparseable acceptance time is treated as not-yet-knowable
            continue
        if accepted > data_cutoff:
            excluded_future += 1
            continue
        pit_eligible.append((row, accepted))

    pit_eligible.sort(key=lambda pair: pair[1], reverse=True)

    tenk = [row for row, _ in pit_eligible if row.get("form") in ("10-K", "10-K/A")]
    tenq = [row for row, _ in pit_eligible if row.get("form") in ("10-Q", "10-Q/A")]
    window_start = data_cutoff - timedelta(days=MATERIAL_8K_WINDOW_DAYS)
    eightk = [row for row, accepted in pit_eligible
              if row.get("form") in ("8-K", "8-K/A") and accepted >= window_start]

    return FilingSelection(
        latest_10k=tenk[0] if tenk else None,
        recent_10q=tuple(tenq[:MAX_RECENT_10Q]),
        recent_8k=tuple(eightk),
        excluded_future=excluded_future,
    )
