"""Persist a live run and hand it to the existing GPT pipeline. The prompt text does not change.

``ResearchPromptService`` renders from persisted ``ScannerRun`` and ``ScannerCandidate`` rows
and states the candidate count from the number of rows it is given, so a live run that fills
those rows renders the deployed prompt with no change at all: a five-candidate session renders
a five-symbol prompt by itself. Nothing in ``app.research`` is touched by this module.

Three rules are enforced here rather than assumed.

**Zero candidates means zero GPT calls.** The run is still persisted - a session where the scan
ran and admitted nobody is evidence, and losing it would make a quiet morning
indistinguishable from a morning the scanner never ran - but no prompt is rendered.

**Idempotency is on the run, not on the caller.** A completed live run for the session already
exists means this session is done; a restart finds it and reuses it instead of writing a second
run that would give the entry path two chains for one morning.

**The version boundary is written, never inferred later.** The run carries
``score_version = a_mover_live_v1`` and ``provider = KIWOOM_AE_SHARED_PREMARKET``, and every
candidate's components carry the live scanner version and checksum. The legacy trade-value
scanner keeps ``quant_v0`` and the research forward contract keeps ``mover_v1.2``; no stored row
is updated, reclassified or deleted by anything here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.scanner import ScannerRun
from app.repositories.scanner import ScannerSnapshotRepository
from app.research.prompt import ResearchPromptService
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import scanner as SCAN


@dataclass(frozen=True)
class HandoffResult:
    """What one live session's handoff produced, including the reason it produced nothing."""

    run_id: int
    reused: bool
    candidate_count: int
    prompt: str | None
    prompt_chars: int
    gpt_calls_expected: int
    metadata: dict[str, Any]

    @property
    def gpt_called(self) -> bool:
        return self.gpt_calls_expected > 0


def existing_run(session: Session, trading_date) -> ScannerRun | None:
    """The completed live run for this session, if one exists. Source-filtered."""
    return ScannerSnapshotRepository(session).get_latest_completed_run(
        trading_date, score_version=LC.RUN_SCORE_VERSION, provider=LC.RUN_PROVIDER)


def persist(session: Session, live: SCAN.LiveScan, *, now: datetime) -> HandoffResult:
    """Persist the run and its candidates, then render the deployed prompt if there are any."""
    repository = ScannerSnapshotRepository(session)
    found = existing_run(session, live.session)
    if found is not None:
        candidates = repository.get_top8(found.id)
        prompt = (ResearchPromptService(repository).generate_top_for_run(found)
                  if candidates else None)
        return HandoffResult(run_id=found.id, reused=True, candidate_count=len(candidates),
                             prompt=prompt, prompt_chars=len(prompt or ""),
                             gpt_calls_expected=1 if candidates else 0,
                             metadata=live.metadata() | {"reused_run": True})
    rows = SCAN.candidate_rows(live)
    run = repository.create_run(
        trading_date=live.session, started_at=live.observed_at, provider=LC.RUN_PROVIDER,
        score_version=LC.RUN_SCORE_VERSION, status="RUNNING",
        universe_count=live.scan.evaluated, excluded_count=live.scan.evaluated - live.scan.eligible,
        candidate_count=live.selection.discovery_pool_size, top8_count=len(rows))
    if rows:
        repository.add_candidates(run.id, rows)
    repository.complete_run(run.id, completed_at=now, status="COMPLETED")
    session.commit()
    prompt = (ResearchPromptService(repository).generate_top_for_run(run) if rows else None)
    return HandoffResult(run_id=run.id, reused=False, candidate_count=len(rows), prompt=prompt,
                         prompt_chars=len(prompt or ""),
                         gpt_calls_expected=1 if rows else 0,
                         metadata=live.metadata() | {"reused_run": False})


def candidate_metadata(live: SCAN.LiveScan) -> list[dict[str, Any]]:
    """Section N's per-candidate record, for a report or a handoff file."""
    payload = SCAN.candidate_payload(live)
    return [{"symbol": entry["symbol"], "live_scanner_rank": entry["live_scanner_rank"],
             "discovery_output_rank": entry["discovery_output_rank"],
             "discovery_pool_rank": entry["discovery_pool_rank"],
             "score": entry["score"], "score_components": entry["score_components"],
             "scanner_version": entry["scanner_version"],
             "scanner_checksum": entry["scanner_checksum"],
             "provider_contract": entry["provider_contract"]}
            for entry in payload["candidates"]]
