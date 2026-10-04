"""Inject live candidates into Paper without touching ``entry_management_runtime``.

``EntryManagementRuntime`` already takes its lifecycle service as a constructor argument, so
the injection seam exists and needs no new one. This module supplies a subclass of
``EntryLifecycleService`` that changes exactly two things and overrides nothing else:

* ``analysis_session_date`` - the live scan is taken at 09:15 ET *of the session it will
  trade*, so its ``trading_date`` **is** the entry session. The legacy predecessor rule
  (``previous_trading_day``) applies to the trade-value scanner, which ranks after the close,
  and must not be applied to a run stamped with the morning it was taken;
* ``approved_candidates`` - the run is resolved with the live source filter, so a live entry
  session consumes the live run and nothing else. There is no union and no fallback: an entry
  session served by two scanners would attribute one day's trades to both.

``evaluate`` is **not** overridden, which is the point. Every approved candidate still travels
``EntryLifecycleService.evaluate`` -> ``StrategyV0Engine`` -> ``RiskEngine`` ->
``StrategyLifecycleRunner`` -> position and exit, through the same premarket context, the same
gate, the same capacity rules and the same durable records. Nothing calls the engine directly
and nothing bypasses risk.

Only ``APPROVE`` reaches entry: the query filters on ``HumanDecisionRecord.decision ==
"APPROVE"``, so a REJECT and the absence of any decision are both simply not candidates.
Authority stays where it is - GPT analyses and scores, a human approves or rejects.

Legacy rows are untouched. A legacy run keeps ``quant_v0`` and resolves through the unchanged
predecessor path; a research forward run keeps ``mover_v1.2``; this service only ever reads
runs stamped ``a_mover_live_v1``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market.calendar import MarketCalendar
from app.models.research import GPTCandidateAnalysis, HumanDecisionRecord
from app.models.scanner import ScannerCandidate, ScannerRun
from app.research.authority import ResearchAuthorityService
from app.services import entry_management_runtime as EMR
from app.services.candidate_source import CandidateSource, candidate_source_of
from app.services.exchange_authority import stored_exchange
from app.strategy.lifecycle import OvernightSuitability, TrailingProfile
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC


class LiveCandidateSource(StrEnum):
    """The live source's own name, added without rewriting the existing classifier.

    ``app.services.candidate_source`` classifies the legacy and research sources from stored
    ``score_version`` values. This enum extends that vocabulary for the live contract; the
    existing names keep their exact meaning and no stored row is reclassified.
    """

    A_MOVER_LIVE_V1 = "A_MOVER_LIVE_V1"


def source_of(run: ScannerRun) -> str:
    """Classify any run, live included. Read-only; never updates a row."""
    if run.score_version == LC.RUN_SCORE_VERSION:
        return str(LiveCandidateSource.A_MOVER_LIVE_V1)
    return str(candidate_source_of(run))


def live_run_for(session: Session, entry_session_date: date) -> ScannerRun | None:
    """The newest completed live run stamped with this entry session. Source-filtered."""
    return session.scalar(
        select(ScannerRun).where(
            ScannerRun.trading_date == entry_session_date,
            ScannerRun.status == "COMPLETED",
            ScannerRun.score_version == LC.RUN_SCORE_VERSION,
            ScannerRun.provider == LC.RUN_PROVIDER,
        ).order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1))


def load_live_approved_candidates(session: Session, entry_session_date: date,
                                  ) -> tuple[EMR.ApprovedCandidate, ...]:
    """``load_approved_candidates`` with one difference: which run is resolved.

    The run selection is source-filtered and dated with the entry session; everything after it
    - the research authority resolution, the APPROVE join and the ordering - is the deployed
    query, kept identical on purpose so the two paths cannot diverge in what "approved" means.
    """
    run = live_run_for(session, entry_session_date)
    if run is None:
        return ()
    analysis = ResearchAuthorityService(session).resolve(run.id)
    if analysis is None:
        return ()
    rows = session.execute(
        select(HumanDecisionRecord, GPTCandidateAnalysis, ScannerCandidate)
        .join(GPTCandidateAnalysis, (
            GPTCandidateAnalysis.gpt_analysis_id == HumanDecisionRecord.gpt_analysis_id
        ) & (GPTCandidateAnalysis.scanner_candidate_id == HumanDecisionRecord.scanner_candidate_id)
          & (GPTCandidateAnalysis.symbol == HumanDecisionRecord.symbol))
        .join(ScannerCandidate, ScannerCandidate.id == HumanDecisionRecord.scanner_candidate_id)
        .where(
            HumanDecisionRecord.gpt_analysis_id == analysis.id,
            HumanDecisionRecord.decision == "APPROVE",
            ScannerCandidate.scanner_run_id == run.id,
            ScannerCandidate.symbol == HumanDecisionRecord.symbol,
        ).order_by(GPTCandidateAnalysis.gpt_rank, HumanDecisionRecord.symbol)
    ).all()
    return tuple(EMR.ApprovedCandidate(
        analysis.id, run.id, scanner.id, decision.symbol, stored_exchange(scanner),
        TrailingProfile(candidate.trailing_profile),
        OvernightSuitability(candidate.overnight_suitability), candidate.gpt_rank,
    ) for decision, candidate, scanner in rows)


class MoverLiveEntryLifecycleService(EMR.EntryLifecycleService):
    """The deployed entry lifecycle, resolving candidates from the live mover run."""

    candidate_source = str(LiveCandidateSource.A_MOVER_LIVE_V1)
    scanner_version = LC.LIVE_VERSION

    def analysis_session_date(self, entry_session_date: date) -> date:
        """The live scan's ``trading_date`` is the entry session itself; no predecessor shift."""
        return entry_session_date

    def approved_candidates(self, analysis_session_date: date
                            ) -> tuple[EMR.ApprovedCandidate, ...]:
        with self.runtime.session_factory() as session:
            return load_live_approved_candidates(session, analysis_session_date)

    def session_metadata(self, entry_session_date: date) -> dict[str, Any]:
        """What this entry session's candidates came from, for a record or a report."""
        with self.runtime.session_factory() as session:
            run = live_run_for(session, entry_session_date)
        return {"entry_session_date": entry_session_date.isoformat(),
                "candidate_source": self.candidate_source,
                "scanner_version": self.scanner_version,
                "scanner_checksum": LC.current().scanner_checksum,
                "run_score_version": LC.RUN_SCORE_VERSION,
                "run_provider": LC.RUN_PROVIDER,
                "scanner_run_id": run.id if run else None,
                "analysis_session_date": self.analysis_session_date(
                    entry_session_date).isoformat(),
                "legacy_predecessor_rule_applied": False}


def lifecycle_for(runtime, *, calendar: MarketCalendar | None = None,
                  environ: dict[str, str] | None = None) -> EMR.EntryLifecycleService:
    """The lifecycle service this process should use. Off: the deployed one, unchanged."""
    if CFG.enabled(environ):
        return MoverLiveEntryLifecycleService(runtime, calendar=calendar)
    return EMR.EntryLifecycleService(runtime, calendar=calendar)


def build_runtime(runtime, provider_factory, *, calendar: MarketCalendar | None = None,
                  environ: dict[str, str] | None = None) -> EMR.EntryManagementRuntime:
    """``EntryManagementRuntime`` with the chosen lifecycle, through its own constructor."""
    return EMR.EntryManagementRuntime(runtime, provider_factory, calendar=calendar,
                                      lifecycle=lifecycle_for(runtime, calendar=calendar,
                                                              environ=environ))


def start(runtime, provider_factory, *, environ: dict[str, str] | None = None):
    """Start the entry runtime. Flag off, this *is* the deployed starter, called unchanged.

    Flag on, the same ownership bookkeeping is performed with the live lifecycle injected.
    The two module-level owner attributes are assigned here because the deployed starter takes
    no lifecycle argument and this stage does not modify that file; a two-line optional
    parameter on ``start_entry_management_runtime`` would be the cleaner home for it and is a
    reviewable change rather than one made quietly here.
    """
    import asyncio
    if not CFG.enabled(environ):
        return EMR.start_entry_management_runtime(runtime, provider_factory)
    task = getattr(EMR, "_task", None)
    if task is not None and not task.done():
        return task
    owner = build_runtime(runtime, provider_factory, environ=environ)
    task = asyncio.create_task(owner.run(), name="entry-management")
    EMR._owner, EMR._task = owner, task     # noqa: SLF001 - documented above
    return task


def partition_runs(runs: Sequence[ScannerRun]) -> dict[str, list[int]]:
    """Run ids by source, live included, so a report can show the version boundary."""
    out: dict[str, list[int]] = {}
    for run in runs:
        out.setdefault(source_of(run), []).append(run.id)
    return out


#: Restated so a reader does not have to infer it: the legacy source keeps its own name.
LEGACY_SOURCE = str(CandidateSource.LEGACY_TRANSACTION_AMOUNT)
