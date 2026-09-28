"""H-V2 D1.1 - SEC Fundamental Data Acquisition Queue.

Pure, deterministic queue-building logic only. No network access happens here; the actual SEC
fetch reuses the existing, already-audited rate-limited clients
(`app.backtest.strategy_c_e0.sec_store` for submissions, `app.backtest.strategy_eqm_v0.xbrl_store`
for companyfacts) from `app.dev.acquire_strategy_h_v2_fundamentals`. Both of those modules already
guarantee raw-source immutability (gzip provider bytes plus a ledger, never overwritten once
present) and CACHED-skip behaviour (an existing file plus its ledger short-circuits any request);
this module does not duplicate that behaviour, it decides *what* needs fetching.

Target universe: unique CIKs from the current H-V2 dated reference snapshot only
(`docs/backtest/strategy_h_v2/H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md` §D) - not the full SEC filer
population. A CIK shared by several tickers (multi-class issuers) is queued exactly once.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable

from app.backtest.strategy_h_v2.universe import UniverseRow

SCHEMA_VERSION = "h_v2_acquisition_queue_v1"

SOURCE_SUBMISSIONS = "submissions"
SOURCE_COMPANYFACTS = "companyfacts"


class AcquisitionStatus(StrEnum):
    CACHED = "CACHED"
    """Both sources were already present before this queue was built; nothing to fetch."""
    PENDING = "PENDING"
    """At least one source is missing; the acquisition run should attempt it."""
    FETCHED = "FETCHED"
    """Set by the runner after a successful fetch of every missing source."""
    PARTIAL = "PARTIAL"
    """Set by the runner when some but not all missing sources were fetched."""
    FAILED = "FAILED"
    """Set by the runner when a fetch was attempted and failed (e.g. transport error after
    retries). A failed fetch is a fact about this run, not about the company - the candidate
    remains `DATA_NOT_READY`, never silently promoted or demoted."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("must be timezone-aware")
    return value


@dataclass(frozen=True)
class AcquisitionQueueItem:
    cik: str
    ticker: str
    """Deterministic representative ticker (alphabetically first among `tickers`)."""
    tickers: tuple[str, ...]
    security_id: str | None
    missing_sources: tuple[str, ...]
    required_actions: tuple[str, ...]
    queued_at: str
    data_cutoff: str
    status: AcquisitionStatus
    schema_version: str = SCHEMA_VERSION


def build_acquisition_queue(
    rows: Iterable[UniverseRow],
    *,
    cached_submission_ciks: frozenset[str],
    cached_companyfacts_ciks: frozenset[str],
    queued_at: datetime,
    data_cutoff: datetime,
) -> list[AcquisitionQueueItem]:
    """Deterministic: same rows and cache sets always produce the same queue in the same order
    (sorted by CIK). Rows without a CIK cannot be targeted by a CIK-keyed SEC fetch and are
    excluded - `eligibility.py` already reports `MISSING_CIK` for them independently."""
    _aware(queued_at)
    _aware(data_cutoff)
    by_cik: dict[str, list[UniverseRow]] = defaultdict(list)
    for row in rows:
        if row.cik:
            by_cik[row.cik].append(row)

    items: list[AcquisitionQueueItem] = []
    for cik in sorted(by_cik):
        group = sorted(by_cik[cik], key=lambda r: r.ticker)
        missing = tuple(
            source for source, cached in (
                (SOURCE_SUBMISSIONS, cached_submission_ciks),
                (SOURCE_COMPANYFACTS, cached_companyfacts_ciks),
            )
            if cik not in cached
        )
        items.append(AcquisitionQueueItem(
            cik=cik,
            ticker=group[0].ticker,
            tickers=tuple(r.ticker for r in group),
            security_id=group[0].security_id,
            missing_sources=missing,
            required_actions=tuple(f"fetch {source}" for source in missing),
            queued_at=queued_at.isoformat(),
            data_cutoff=data_cutoff.isoformat(),
            status=AcquisitionStatus.PENDING if missing else AcquisitionStatus.CACHED,
        ))
    return items
