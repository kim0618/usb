"""Inject live candidates into Paper without touching ``entry_management_runtime``.

``EntryManagementRuntime`` already takes its lifecycle service as a constructor argument, so
the injection seam exists and needs no new one. This module supplies a subclass of
``EntryLifecycleService`` that changes exactly one thing and overrides nothing else:

* ``approved_candidates`` - the run is resolved with the live source filter, so an entry
  session consumes the live run and nothing else. There is no union and no fallback: an entry
  session served by two scanners would attribute one day's trades to both.

What it deliberately does **not** change is the date rule. ``analysis_session_date`` stays the
inherited ``previous_trading_day``, so the chain is

    mover scan of session D (09:15 ET cut, ``trading_date = D``)
        -> GPT research and human APPROVE through the Korean day
            -> entry evaluation on ``next_trading_day(D)``

for the live source exactly as it is for the morning trade-value scanner. The scan's own date
is untouched - a live run still records the premarket session it observed - and only the
session that consumes it is the next one.

``evaluate`` is **not** overridden, which is the point. Every approved candidate still travels
``EntryLifecycleService.evaluate`` -> ``StrategyV0Engine`` -> ``RiskEngine`` ->
``StrategyLifecycleRunner`` -> position and exit, through the same premarket context, the same
gate, the same capacity rules and the same durable records. Nothing calls the engine directly
and nothing bypasses risk.

Only ``APPROVE`` reaches entry: the query filters on ``HumanDecisionRecord.decision ==
"APPROVE"``, so a REJECT and the absence of any decision are both simply not candidates.
Authority stays where it is - GPT analyses and scores, a human approves or rejects.

Legacy rows are untouched. A legacy run keeps ``quant_v0``; a research forward run keeps
``mover_v1.2``; this service only ever reads runs stamped ``a_mover_live_v1``. There is no
fallback onto either: an entry session whose predecessor has no completed live run, no active
analysis or no APPROVE resolves zero candidates and trades nothing.
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


#: What section O's baseline names are when no live run, or no candidate row, carries them.
#: UNKNOWN rather than zeros: an entry session with no run has made no claim about a
#: denominator, and writing 0 Kiwoom and 0 Massive sessions would be one.
NO_BASELINE_STAMP: dict[str, Any] = {"baseline_mode": "UNKNOWN", "baseline_session_count": None,
                                     "kiwoom_session_count": None,
                                     "massive_session_count": None}


def baseline_stamp_of(session: Session, run: ScannerRun) -> dict[str, Any]:
    """The denominator mix the scan recorded, read back from the run's own candidate rows.

    The mix is a property of the run, so every candidate of a run carries the same values and
    the first row answers for the run. It is read, never recomputed: recomputing it here would
    read today's stored rows and could disagree with what the morning's scan actually divided
    by. A run that admitted nobody has no candidate row and so no stamp, which is reported as
    UNKNOWN rather than filled in.
    """
    row = session.scalar(select(ScannerCandidate)
                         .where(ScannerCandidate.scanner_run_id == run.id)
                         .order_by(ScannerCandidate.rank, ScannerCandidate.id).limit(1))
    components = dict(row.score_components_json or {}) if row is not None else {}
    return {name: components.get(name, default)
            for name, default in NO_BASELINE_STAMP.items()}


def live_run_for(session: Session, analysis_session_date: date) -> ScannerRun | None:
    """The newest completed live run stamped with this *analysis* session. Source-filtered.

    ``analysis_session_date`` is the session the scan observed, which is the run's own
    ``trading_date``, and is ``previous_trading_day`` of the session that consumes it. The
    caller does that arithmetic once, in ``EntryLifecycleService.analysis_session_date``, so
    that this query and the legacy ``load_approved_candidates`` are keyed identically and
    cannot drift apart by a session.
    """
    return session.scalar(
        select(ScannerRun).where(
            ScannerRun.trading_date == analysis_session_date,
            ScannerRun.status == "COMPLETED",
            ScannerRun.score_version == LC.RUN_SCORE_VERSION,
            ScannerRun.provider == LC.RUN_PROVIDER,
        ).order_by(ScannerRun.completed_at.desc(), ScannerRun.id.desc()).limit(1))


def load_live_approved_candidates(session: Session, analysis_session_date: date,
                                  ) -> tuple[EMR.ApprovedCandidate, ...]:
    """``load_approved_candidates`` with one difference: which run is resolved.

    The run selection is source-filtered; the date it is keyed by is the analysis session,
    exactly as the deployed loader's is. Everything after the selection - the research
    authority resolution, the APPROVE join and the ordering - is the deployed query, kept
    identical on purpose so the two paths cannot diverge in what "approved" means.
    """
    run = live_run_for(session, analysis_session_date)
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
    """The deployed entry lifecycle, resolving candidates from the live mover run.

    ``analysis_session_date`` is **not** overridden, and that is the restored contract. The
    inherited rule - the analysis an entry session consumes is the one stamped with its
    ``previous_trading_day`` - is the rule for both scanners, because it is a property of the
    workflow and not of the scanner: a ranking is produced, a human reviews it, and the next
    session trades it.

    A-MOVER-LIVE-V1 originally overrode it to the identity, reasoning that a cut taken at
    09:15 ET of session D is "about" session D. The arithmetic was right and the contract was
    wrong: with entry bound to the identity, the candidates of the session being traded do not
    exist until 09:27 ET and the entry deadline is 10:30 ET, so the whole GPT research and
    human APPROVE had to happen inside a 63-minute window at 22:27-23:30 KST. Under the
    predecessor rule the same run is reviewed through the whole Korean day that follows it and
    traded that night. The run keeps its own ``trading_date``; only the consumer moves.
    """

    candidate_source = str(LiveCandidateSource.A_MOVER_LIVE_V1)
    scanner_version = LC.LIVE_VERSION

    def approved_candidates(self, analysis_session_date: date
                            ) -> tuple[EMR.ApprovedCandidate, ...]:
        with self.runtime.session_factory() as session:
            return load_live_approved_candidates(session, analysis_session_date)

    def session_metadata(self, entry_session_date: date) -> dict[str, Any]:
        """What this entry session's candidates came from, for a record or a report."""
        analysis_session = self.analysis_session_date(entry_session_date)
        with self.runtime.session_factory() as session:
            run = live_run_for(session, analysis_session)
            baseline = baseline_stamp_of(session, run) if run is not None else NO_BASELINE_STAMP
        return {"entry_session_date": entry_session_date.isoformat(),
                "candidate_source": self.candidate_source,
                "scanner_version": self.scanner_version,
                "scanner_checksum": LC.current().scanner_checksum,
                "run_score_version": LC.RUN_SCORE_VERSION,
                "run_provider": LC.RUN_PROVIDER,
                "scanner_run_id": run.id if run else None,
                "analysis_session_date": analysis_session.isoformat(),
                "analysis_session_rule": "previous_trading_day",
                "baseline_version": LC.BASELINE_VERSION,
                "baseline_provider_contract": LC.BASELINE_PROVIDER_CONTRACT} | baseline


def lifecycle_for(runtime, *, calendar: MarketCalendar | None = None,
                  environ: dict[str, str] | None = None) -> EMR.EntryLifecycleService:
    """The lifecycle service this process should use. Authority off: the pre-live one.

    The switch is ``A_MOVER_LIVE_ENTRY_AUTHORITY``, which defaults to the scan flag: with the
    live scan on, the live runs are the candidate authority, and the opt-out is explicit.
    ``app.strategy_a_mover_live.config`` states both switches. Either branch resolves one
    source only - there is no fallback between them in either direction.
    """
    if CFG.entry_authority(environ):
        return MoverLiveEntryLifecycleService(runtime, calendar=calendar)
    return EMR.EntryLifecycleService(runtime, calendar=calendar)


def build_runtime(runtime, provider_factory, *, calendar: MarketCalendar | None = None,
                  environ: dict[str, str] | None = None) -> EMR.EntryManagementRuntime:
    """``EntryManagementRuntime`` with the chosen lifecycle, through its own constructor."""
    return EMR.EntryManagementRuntime(runtime, provider_factory, calendar=calendar,
                                      lifecycle=lifecycle_for(runtime, calendar=calendar,
                                                              environ=environ))


def start(runtime, provider_factory, *, environ: dict[str, str] | None = None):
    """Start the entry runtime. Authority off, this *is* the deployed starter, unchanged.

    ``A_MOVER_LIVE_ENTRY_AUTHORITY`` on, the same ownership bookkeeping is performed with the
    live lifecycle injected.
    The two module-level owner attributes are assigned here because the deployed starter takes
    no lifecycle argument and this stage does not modify that file; a two-line optional
    parameter on ``start_entry_management_runtime`` would be the cleaner home for it and is a
    reviewable change rather than one made quietly here.
    """
    import asyncio
    if not CFG.entry_authority(environ):
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
