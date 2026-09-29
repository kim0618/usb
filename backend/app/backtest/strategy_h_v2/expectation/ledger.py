"""D4 immutable ledger (brief §31), reusing D3.2F's telemetry contracts rather than restating them.

`RawResponseRecordV1` and `checksum` are imported from `research/telemetry_contract_v2.py`
unchanged - the reason that module exists (D3.1's MRVI failure became unauditable because only a
1,500-character preview survived) applies identically to D4, and a second, parallel raw-response
contract would let the two drift until one of them truncated something again.

What D4 adds is linkage. A D4 analysis is only meaningful relative to three upstream artifacts, so
every record carries their checksums: the D3 research output it interpreted, the expectation
evidence bundle it read, and the D2.1 input package both of those descend from. An analysis whose
D3 checksum no longer matches the D3 output on disk is not a stale record to refresh - it is a
record of an analysis of something else, which is why `verify_linkage` reports rather than repairs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1, checksum

LEDGER_SCHEMA_VERSION = "h_v2_d4_ledger_v1"


class AnalysisAlreadyExistsError(RuntimeError):
    """A file for this `analysis_id` already exists. A rerun uses a NEW id; nothing in this module
    can overwrite a stored analysis, which is what makes "immutable" a property rather than a
    promise."""


@dataclass(frozen=True)
class D4RepairRoundV1:
    attempt_number: int
    failure_codes_before: list[str]
    failure_details_before: list[str]
    repair_prompt_version: str
    raw_repair_response: RawResponseRecordV1
    validation_after: str
    cost_usd: float

    def to_dict(self) -> dict:
        return {
            "attempt_number": self.attempt_number,
            "failure_codes_before": self.failure_codes_before,
            "failure_details_before": self.failure_details_before,
            "repair_prompt_version": self.repair_prompt_version,
            "raw_repair_response": self.raw_repair_response.to_dict(),
            "validation_after": self.validation_after, "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class D4AnalysisRecordV1:
    """One complete, immutable D4 attempt. Every field brief §31 lists, plus the upstream linkage.

    A field with no real runtime source yet (`input_tokens`/`output_tokens`) is `None` rather than
    invented - the same decision `research_attempt_v2.ResearchAttemptRecordV1` documents, and for
    the same reason: D4 makes zero live calls in this stage, so there is no real response to
    confirm a key path against.
    """

    schema_version: str
    analysis_id: str
    analysis_run_id: str
    candidate_id: str
    ticker: str

    research_input_id: str
    research_output_checksum: str
    expectation_evidence_id: str
    expectation_evidence_checksum: str
    input_package_id: str
    input_package_checksum: str

    model_requested: str
    canonical_model: str | None
    model_mismatch: bool

    prompt_version: str
    schema_version_out: str
    gap_contract_version: str
    validation_contract_version: str

    started_at: str
    completed_at: str | None

    initial_raw_response: RawResponseRecordV1
    initial_parse_status: str
    initial_validation_status: str
    initial_failure_codes: list[str] = field(default_factory=list)
    initial_failure_details: list[str] = field(default_factory=list)

    repair_rounds: list[D4RepairRoundV1] = field(default_factory=list)

    final_raw_response: RawResponseRecordV1 | None = None
    final_output: dict | None = None
    final_output_checksum: str | None = None
    final_status: str = "PENDING"

    applied_contract_rules: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "analysis_id": self.analysis_id,
            "analysis_run_id": self.analysis_run_id, "candidate_id": self.candidate_id,
            "ticker": self.ticker,
            "research_input_id": self.research_input_id,
            "research_output_checksum": self.research_output_checksum,
            "expectation_evidence_id": self.expectation_evidence_id,
            "expectation_evidence_checksum": self.expectation_evidence_checksum,
            "input_package_id": self.input_package_id,
            "input_package_checksum": self.input_package_checksum,
            "model_requested": self.model_requested, "canonical_model": self.canonical_model,
            "model_mismatch": self.model_mismatch,
            "prompt_version": self.prompt_version, "schema_version_out": self.schema_version_out,
            "gap_contract_version": self.gap_contract_version,
            "validation_contract_version": self.validation_contract_version,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "initial_raw_response": self.initial_raw_response.to_dict(),
            "initial_parse_status": self.initial_parse_status,
            "initial_validation_status": self.initial_validation_status,
            "initial_failure_codes": self.initial_failure_codes,
            "initial_failure_details": self.initial_failure_details,
            "repair_rounds": [r.to_dict() for r in self.repair_rounds],
            "final_raw_response": (
                self.final_raw_response.to_dict() if self.final_raw_response else None
            ),
            "final_output": self.final_output,
            "final_output_checksum": self.final_output_checksum,
            "final_status": self.final_status,
            "applied_contract_rules": self.applied_contract_rules,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
        }


def store_analysis(root: Path, record: D4AnalysisRecordV1) -> Path:
    """Writes to `root/<analysis_run_id>/<ticker>/<analysis_id>.json` and refuses to overwrite."""
    directory = root / record.analysis_run_id / record.ticker
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.analysis_id}.json"
    if path.exists():
        raise AnalysisAlreadyExistsError(
            f"{path} already exists - a D4 rerun must use a new analysis_id, never overwrite an "
            "immutable analysis"
        )
    path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False, default=str))
    return path


def load_analysis(path: Path) -> dict:
    return json.loads(path.read_text())


def store_evidence_bundle(root: Path, run_id: str, ticker: str, payload: str) -> Path:
    """The expectation evidence bundle is stored beside the analysis and is equally immutable: the
    analysis's `expectation_evidence_checksum` is meaningless if the bundle it points at can be
    rewritten afterwards."""
    directory = root / run_id / ticker
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "expectation_evidence.json"
    if path.exists():
        raise AnalysisAlreadyExistsError(
            f"{path} already exists - build the bundle under a new run_id rather than replacing a "
            "bundle an analysis already checksummed"
        )
    path.write_text(payload)
    return path


def verify_linkage(
    record: dict, *, research_output_json: str, evidence_bundle_json: str,
) -> list[str]:
    """Every mismatch between a stored analysis and the artifacts it claims to descend from.

    Reports, never repairs. A checksum mismatch means the analysis interpreted a different D3
    output or a different evidence bundle than the ones now on disk - recomputing the checksum to
    make it agree would destroy the only evidence that they diverged.
    """
    problems: list[str] = []
    expected_research = checksum(research_output_json)
    if record.get("research_output_checksum") != expected_research:
        problems.append(
            f"research_output_checksum {record.get('research_output_checksum')!r} != "
            f"{expected_research!r} - this analysis did not read the D3 output it points at"
        )
    expected_bundle = checksum(evidence_bundle_json)
    if record.get("expectation_evidence_checksum") != expected_bundle:
        problems.append(
            f"expectation_evidence_checksum {record.get('expectation_evidence_checksum')!r} != "
            f"{expected_bundle!r} - the evidence bundle on disk is not the one analyzed"
        )
    final_output = record.get("final_output")
    if final_output is not None:
        expected_final = checksum(json.dumps(final_output, sort_keys=True, default=str))
        if record.get("final_output_checksum") != expected_final:
            problems.append("final_output_checksum does not match the stored final_output")
    return problems
