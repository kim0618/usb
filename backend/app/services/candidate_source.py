"""Which scanner a Paper entry session takes its candidates from, and from which session.

Two scanners can fill ``scanner_runs``, and a row says which one did without being rewritten:

``LEGACY_TRANSACTION_AMOUNT``
    the deployed morning Scanner. It ranks Kiwoom's trade-value board after the close, so its
    ``trading_date`` is the session that has just finished and the entry session that consumes
    it is the next one - the predecessor rule ``analysis_session_date`` enforces. Its
    ``score_version`` is ``quant_v0``.

``A_MOVER_V1_2``
    the forward mover scanner. It reads the premarket at 09:15 ET of the session it will trade,
    so its ``trading_date`` *is* the entry session and the predecessor rule does not apply to
    it. Its ``score_version`` is ``mover_v1.2``.

:func:`candidate_source_of` classifies a run, including every run already stored, from the
``score_version`` it was written with. Nothing here updates a row, and a run whose
``score_version`` is neither is reported as such rather than guessed at.

The switch is one setting and it is off by default. Off, the entry runtime resolves candidates
exactly as it does today: the predecessor session, with no source filter on the query at all.
On, the entry session consumes the mover run of its own date and nothing else - there is no
union and no fallback, because an entry session served by two scanners would be attributing
one day's trades to both.
"""

from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from app.backtest.mover_scanner_v1 import contract as K
from app.core.config import Settings, get_settings
from app.market.calendar import MarketCalendar
from app.models.scanner import ScannerRun

LEGACY_SCORE_VERSION = "quant_v0"


class CandidateSource(StrEnum):
    """The scanner a run or an entry session's candidates came from."""

    LEGACY_TRANSACTION_AMOUNT = "LEGACY_TRANSACTION_AMOUNT"
    A_MOVER_V1_2 = "A_MOVER_V1_2"
    UNKNOWN = "UNKNOWN"


#: ``ScannerRun.score_version`` values each source writes. Read-only classification.
SCORE_VERSIONS: dict[CandidateSource, tuple[str, ...]] = {
    CandidateSource.LEGACY_TRANSACTION_AMOUNT: (LEGACY_SCORE_VERSION,),
    CandidateSource.A_MOVER_V1_2: (K.RUN_SCORE_VERSION,),
}


def score_versions(source: CandidateSource) -> tuple[str, ...]:
    return SCORE_VERSIONS.get(source, ())


def candidate_source_of(run: ScannerRun) -> CandidateSource:
    """Classify a stored run without touching it."""
    for source, versions in SCORE_VERSIONS.items():
        if run.score_version in versions:
            return source
    return CandidateSource.UNKNOWN


def active_candidate_source(settings: Settings | None = None) -> CandidateSource | None:
    """The source an entry session resolves through, or None for today's resolution.

    None is not a default-by-omission: it is the unchanged legacy path, with no source filter
    on the run query, so enabling nothing changes nothing.
    """
    settings = settings or get_settings()
    if getattr(settings, "a_mover_candidate_source_enabled", False):
        return CandidateSource.A_MOVER_V1_2
    return None


def analysis_session_date_for(source: CandidateSource | None, calendar: MarketCalendar,
                              entry_session_date: date) -> date:
    """The scanner ``trading_date`` an entry session consumes under ``source``."""
    if source is CandidateSource.A_MOVER_V1_2:
        return entry_session_date
    return calendar.previous_trading_day(entry_session_date)


def describe(source: CandidateSource | None) -> str:
    return str(source) if source is not None else "LEGACY_TRANSACTION_AMOUNT(unfiltered)"


def partition_runs(runs: Sequence[ScannerRun]) -> dict[str, list[int]]:
    """Run ids by source, for a report that has to show the version boundary."""
    out: dict[str, list[int]] = {}
    for run in runs:
        out.setdefault(str(candidate_source_of(run)), []).append(run.id)
    return out
