"""H-V2-D3.2F: the full research-attempt record a D3.3 live runner must persist per candidate
(brief §3), built on top of `telemetry_contract_v2.py`'s `RawResponseRecordV1` (full-fidelity, no
truncation - brief §4) rather than replacing it.

This module defines the record shape and an immutable storage contract; it does not itself call a
model (brief §0/§18: zero live calls this stage). `run_strategy_h_v2_d3_3.py` is where this gets
wired to an actual call - see that file's own module docstring for what "wired" means here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1, checksum


class AttemptAlreadyExistsError(RuntimeError):
    """Raised by `store_attempt` when a file for this `attempt_id` already exists. A retry or a
    research rerun must use a NEW `attempt_id` (brief §5) - this is what enforces that, rather than
    leaving it as an unenforced convention a future caller could accidentally violate."""


@dataclass(frozen=True)
class RepairAttemptRecordV1:
    """One repair round inside a `ResearchAttemptRecordV1` (brief §6)."""

    attempt_number: int
    failure_codes_before: list[str]
    repair_prompt_version: str
    raw_repair_response: RawResponseRecordV1
    """Full raw text, never a preview - see that class's own docstring for why this matters."""
    validation_after: str
    """"OK" or the failure status this round's validation produced - not a list of error strings
    (those live in the NEXT round's `failure_codes_before`, or in `final_status`/the failed
    candidate's own record if this was the last round)."""
    cost_usd: float

    def to_dict(self) -> dict:
        return {
            "attempt_number": self.attempt_number, "failure_codes_before": self.failure_codes_before,
            "repair_prompt_version": self.repair_prompt_version,
            "raw_repair_response": self.raw_repair_response.to_dict(),
            "validation_after": self.validation_after, "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class ResearchAttemptRecordV1:
    """One complete, immutable candidate research attempt: every field D3.2F brief §3 lists.

    A field this stage has no real runtime source for yet (`input_tokens`/`output_tokens` - see
    `extract_usage`'s own docstring) is typed `| None` and left `None` rather than invented (brief
    §3: "없는 필드는 임의 생성하지 않는다").
    """

    attempt_id: str
    """Unique per attempt - a retry or rerun gets a NEW one, never reuses an existing candidate's
    id (brief §5)."""
    research_run_id: str
    candidate_id: str
    ticker: str
    cik: str

    input_package_id: str
    input_package_checksum: str

    model_requested: str
    canonical_model: str | None
    """From the CLI response's own `modelUsage.<model>.canonicalModel` - `None` only if the call
    errored before any model usage was reported at all."""
    model_mismatch: bool
    """True iff `canonical_model` is known and differs from `model_requested` - brief §7: such an
    output is INVALID, and this flag is how a caller checks that mechanically rather than by
    string-comparing the two fields itself every time."""

    prompt_version: str
    schema_version: str
    validation_contract_version: str

    started_at: str
    completed_at: str | None

    initial_raw_response: RawResponseRecordV1
    initial_parse_status: str
    initial_validation_status: str
    initial_failure_codes: list[str] = field(default_factory=list)
    initial_failure_details: list[str] = field(default_factory=list)

    repair_attempts: list[RepairAttemptRecordV1] = field(default_factory=list)

    final_raw_response: RawResponseRecordV1 | None = None
    final_output: dict | None = None
    final_output_checksum: str | None = None
    final_status: str = "PENDING"

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id, "research_run_id": self.research_run_id,
            "candidate_id": self.candidate_id, "ticker": self.ticker, "cik": self.cik,
            "input_package_id": self.input_package_id,
            "input_package_checksum": self.input_package_checksum,
            "model_requested": self.model_requested, "canonical_model": self.canonical_model,
            "model_mismatch": self.model_mismatch,
            "prompt_version": self.prompt_version, "schema_version": self.schema_version,
            "validation_contract_version": self.validation_contract_version,
            "started_at": self.started_at, "completed_at": self.completed_at,
            "initial_raw_response": self.initial_raw_response.to_dict(),
            "initial_parse_status": self.initial_parse_status,
            "initial_validation_status": self.initial_validation_status,
            "initial_failure_codes": self.initial_failure_codes,
            "initial_failure_details": self.initial_failure_details,
            "repair_attempts": [r.to_dict() for r in self.repair_attempts],
            "final_raw_response": self.final_raw_response.to_dict() if self.final_raw_response else None,
            "final_output": self.final_output, "final_output_checksum": self.final_output_checksum,
            "final_status": self.final_status,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
        }


def compute_model_mismatch(model_requested: str, canonical_model: str | None) -> bool:
    return canonical_model is not None and canonical_model != model_requested


def extract_usage(raw: dict) -> tuple[int | None, int | None]:
    """Best-effort token-count extraction from the Claude Code CLI's `--output-format json`
    response. `run_strategy_h_v2_d3.py` (D3's own live runner) has only ever read `total_cost_usd`,
    `is_error`, `result`, and `modelUsage.<model>.canonicalModel` from this JSON - no D3/D3.1/D3.2
    code has ever parsed a token count from it, and this stage makes zero live calls (brief §0), so
    there is no real response on hand to confirm the exact key path against. Rather than fabricate
    one, this checks the plausible locations a Claude Code CLI response could carry usage under and
    returns `(None, None)` if none are present - `test_extract_usage_returns_none_when_absent`
    locks this in as the honest default, not a guess dressed up as a result. Whichever key path is
    actually present in the FIRST real D3.3 response should be confirmed and, if this guess was
    wrong, corrected then - not asserted as verified now.
    """
    usage = raw.get("usage") or raw.get("modelUsage", {}).get(raw.get("model", ""), {}).get("usage")
    if isinstance(usage, dict):
        return usage.get("input_tokens"), usage.get("output_tokens")
    return None, None


def store_attempt(root: Path, record: ResearchAttemptRecordV1) -> Path:
    """Writes `record` to `root/<research_run_id>/<ticker>/<attempt_id>.json`. Refuses to overwrite
    an existing file for the same `attempt_id` (brief §5) - a retry or rerun must construct the
    record with a new `attempt_id` before calling this again."""
    directory = root / record.research_run_id / record.ticker
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.attempt_id}.json"
    if path.exists():
        raise AttemptAlreadyExistsError(
            f"{path} already exists - a retry or rerun must use a new attempt_id, not overwrite "
            "an existing immutable attempt"
        )
    path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False, default=str))
    return path


def load_attempt(path: Path) -> dict:
    return json.loads(path.read_text())
