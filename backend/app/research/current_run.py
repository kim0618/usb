"""Which ScannerRun the review chain is pointed at. One definition, the runtime's own.

The approval chain has two ends. At one end the UI renders a GPT prompt, imports the returned
analysis and records APPROVE/REJECT; at the other the entry runtime loads the approved
candidates of the run it is about to trade. Both ends name "the current run", and they named
it differently:

* the UI took the newest COMPLETED run by ``completed_at``, with no source filter
  (``latest_completed_run`` here, ``APIQueryService.latest_run`` there);
* the entry runtime takes the run its lifecycle resolves - the run stamped with
  ``previous_trading_day`` of the session it is about to trade.

With two scanners filling ``scanner_runs``, those two are almost never the same row. A's live
cut completes near 09:27 ET and the morning trade-value scanner completes near 18:01 ET of the
same session, so each is "the newest run overall" for part of the day and the unfiltered query
answers differently depending on the hour the operator happens to review in. Between
2026-10-05 and 10-09 the entry runtime was bound to the live source while every analysis was
imported in the Korean day, when the newest run overall is the legacy one: analyses 21 and 22
activated on legacy runs 24 and 26, live runs 21, 23, 25, 27 and 29 kept
``active_gpt_analysis_id = None``, and five entry sessions resolved no candidates at all.

So the resolution is one function here, switched by the one flag the entry lifecycle is
switched by (``A_MOVER_LIVE_ENTRY_AUTHORITY``, read through
``strategy_a_mover_live.config.entry_authority``), and it is **symmetric**. Whichever source
entry is bound to, the UI resolves a run of that source and of no other, with no cross-source
fallback in either direction:

* entry authority live (the default once the live scan is on) - the current run is the newest
  live-source run and nothing else;
* entry authority explicitly off - the current run is the newest run that is *not* a
  live-source run, which is the pre-live deployed contract.

A fallback onto the other source is exactly the silent mismatch above: it renders a prompt for
a run whose approvals the runtime will never read. No run of the authoritative source means no
current run, which the API reports as "no completed scanner run" - the same answer it gives on
an empty database.

Which *session* consumes a run is a separate question from which run is current, and it has
one answer for both sources: :func:`entry_session_for`. A run observes session D and is
consumed on ``next_trading_day(D)``, which is what leaves the Korean day between the two for
the GPT research and the human APPROVE.

Nothing here writes, and no stored row is reclassified. Every run stays reachable through an
explicit ``score_version`` or ``scanner_run_id`` query, which is how the live rows, the
historical rows and the pre-live sessions all remain inspectable.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.market.calendar import MarketCalendar
from app.models.scanner import ScannerRun
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC

#: The live source filter, stated once. Identical to ``paper_adapter.live_run_for``'s own two
#: predicates; the date predicate is the caller's, because the UI resolves the newest live run
#: while the runtime resolves the live run of the analysis session it is consuming.
LIVE_RUN_CRITERIA = (ScannerRun.score_version == LC.RUN_SCORE_VERSION,
                     ScannerRun.provider == LC.RUN_PROVIDER)

#: The complement, for the pre-live deployed source, and the exact SQL negation of
#: :func:`is_live_run` so the row classifier and the query cannot disagree about one row.
#: Stated as "not the live source" rather than "equals ``quant_v0``" on purpose: both columns
#: are NOT NULL, so this is the unfiltered query minus exactly the rows the deployed entry
#: lifecycle cannot consume, and it reclassifies nothing. A research row keeps whatever name
#: it was written with and stays resolvable.
LEGACY_RUN_CRITERIA = (or_(ScannerRun.score_version != LC.RUN_SCORE_VERSION,
                           ScannerRun.provider != LC.RUN_PROVIDER),)


def live_source_active(environ: dict[str, str] | None = None) -> bool:
    """Whether the entry runtime will resolve candidates from the live source."""
    return CFG.entry_authority(environ)


def source_criteria(environ: dict[str, str] | None = None) -> tuple:  # type: ignore[type-arg]
    """The source predicates of whichever scanner the entry runtime is bound to."""
    return LIVE_RUN_CRITERIA if live_source_active(environ) else LEGACY_RUN_CRITERIA


def is_live_run(run: ScannerRun) -> bool:
    """Whether this stored row is a live-source run. Read-only; never updates a row."""
    return run.score_version == LC.RUN_SCORE_VERSION and run.provider == LC.RUN_PROVIDER


def run_is_authoritative(run: ScannerRun, environ: dict[str, str] | None = None) -> bool:
    """Whether an analysis imported against this run would be read by the entry runtime."""
    return is_live_run(run) == live_source_active(environ)


def completed_runs() -> Select[tuple[ScannerRun]]:
    """The COMPLETED-run base query both branches order and limit identically."""
    return select(ScannerRun).where(ScannerRun.status == "COMPLETED",
                                    ScannerRun.completed_at.is_not(None))


def newest(statement: Select[tuple[ScannerRun]], session: Session) -> ScannerRun | None:
    return session.scalar(statement.order_by(ScannerRun.completed_at.desc(),
                                             ScannerRun.id.desc()).limit(1))


def newest_completed_run(session: Session) -> ScannerRun | None:
    """The newest COMPLETED run of any source, unfiltered. Kept for callers that mean that."""
    return newest(completed_runs(), session)


def current_run(session: Session, *, environ: dict[str, str] | None = None) -> ScannerRun | None:
    """The run the review chain is pointed at, resolved by the entry runtime's own switch."""
    return newest(completed_runs().where(*source_criteria(environ)), session)


def entry_session_for(run: ScannerRun, calendar: MarketCalendar) -> date:
    """The session this run's approvals are for: the XNYS session after the run's own.

    One rule, both sources, because the review between them is one workflow. A run's
    ``trading_date`` is the session it *observed* - the morning trade-value scanner ranks
    session D after D's close, the live mover scan ranks D's premarket at 09:15 ET of D - and
    in both cases the ranking is handed to GPT, approved by a human and consumed on
    ``next_trading_day(D)``. It is the exact inverse of the entry runtime's
    ``analysis_session_date`` (``previous_trading_day`` of the entry session), and
    ``previous_trading_day(next_trading_day(D)) == D`` holds for every XNYS session, so a
    board built on this lists the session the runtime will actually evaluate, and the run the
    runtime will actually read.

    This is the correction of A-MOVER-LIVE-V1's original same-session reading. Returning
    ``run.trading_date`` for a live run was arithmetically true of the 09:15 ET cut and
    operationally false of the contract: it put the whole GPT-and-human review inside the
    09:27-10:30 ET entry window (22:27-23:30 KST), which is not when the operator reviews. The
    run's own date is not touched here - it still records the premarket it observed. Only the
    session that *consumes* it moves.
    """
    return calendar.next_trading_day(run.trading_date)
