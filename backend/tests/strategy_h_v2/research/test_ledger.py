from __future__ import annotations

import pytest
from helpers import utc

from app.backtest.strategy_h_v2.research.ledger import latest_version, next_version, read_research_output, write_research_output
from app.backtest.strategy_h_v2.research.schema import GrowthDurability, GrowthDurabilityEvidence, GrowthDurabilityState, HResearchInterpretationV1, ResearchCompleteness, WhyNowCandidate, BusinessModel

NOW = utc(2026, 9, 28)


def _output(ticker: str = "ACME", version: int = 1) -> HResearchInterpretationV1:
    return HResearchInterpretationV1(
        research_id=f"R-{version}", version=version, company_id="1", ticker=ticker,
        decision_time=NOW, input_package_id="P-1", input_package_checksum="deadbeef",
        model="claude-opus-5-5", model_version="claude-opus-5-5", prompt_version="v1",
        created_at=NOW, business_model=BusinessModel(), fundamental_change=[],
        growth_durability=GrowthDurability(state=GrowthDurabilityState.UNKNOWN,
                                            evidence_checklist=GrowthDurabilityEvidence()),
        future_business=[], competitive_position=[], management_execution=[],
        catalyst_candidates=[], why_now_candidate=WhyNowCandidate(summary="worth a look"),
        risks=[], invalidation_candidates=[], open_questions=[], unknown_fields=[], sources=[],
        research_completeness=ResearchCompleteness.INSUFFICIENT_EVIDENCE,
    )


def test_first_write_is_version_1(tmp_path):
    assert next_version(tmp_path, "ACME") == 1
    write_research_output(tmp_path, _output(version=1))
    assert next_version(tmp_path, "ACME") == 2


def test_rerun_creates_v2_not_overwrite(tmp_path):
    write_research_output(tmp_path, _output(version=1))
    write_research_output(tmp_path, _output(version=2))
    v1 = read_research_output(tmp_path, "ACME", 1)
    v2 = read_research_output(tmp_path, "ACME", 2)
    assert v1.research_id == "R-1"
    assert v2.research_id == "R-2"


def test_writing_the_same_version_twice_is_refused(tmp_path):
    write_research_output(tmp_path, _output(version=1))
    with pytest.raises(FileExistsError):
        write_research_output(tmp_path, _output(version=1))


def test_latest_version_returns_the_most_recent(tmp_path):
    write_research_output(tmp_path, _output(version=1))
    write_research_output(tmp_path, _output(version=2))
    latest = latest_version(tmp_path, "ACME")
    assert latest is not None
    assert latest.version == 2


def test_latest_version_is_none_when_nothing_written(tmp_path):
    assert latest_version(tmp_path, "NEVER_RESEARCHED") is None


def test_input_package_checksum_is_persisted(tmp_path):
    write_research_output(tmp_path, _output(version=1))
    v1 = read_research_output(tmp_path, "ACME", 1)
    assert v1.input_package_checksum == "deadbeef"
