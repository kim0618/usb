"""H-V2-D3.3 live runner: Research Prompt V2 + Schema V2, with full telemetry (D3.2F).

NOT EXECUTED by D3.2F (brief §0/§18: zero live Opus calls this stage). This file exists so D3.3, if
and when the user authorizes it, runs against a runner that already:

1. Persists the full, untruncated raw response for every attempt and every repair round
   (`research_attempt_v2.RawResponseRecordV1` via `telemetry_contract_v2`) - never a 1,500-character
   preview. MRVI (D3.1's one unrepaired failure, `NOT_AUDITABLE` today per D3.2 §J.7) is the
   specific gap this closes.
2. Never overwrites a stored attempt (`research_attempt_v2.store_attempt` raises on collision) - a
   retry or rerun gets a new `attempt_id`.
3. Checks the response's own reported `canonicalModel` against the model actually requested and
   flags a mismatch as invalid, rather than trusting the request string.
4. Stops before starting a call that could breach the frozen $30 hard budget
   (`d3_3_contract.D3_3_HARD_BUDGET_USD`), the same before-not-after discipline
   `run_strategy_h_v2_d3_1.py` already used for Batch 2's ceiling.

`call_opus` is the only function that actually shells out to the Claude Code CLI; every other
function takes it as a parameter (`call_fn`) so the rest of the pipeline is testable with a stub and
zero live calls - see `test_run_strategy_h_v2_d3_3.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import json
import os
import subprocess
import uuid

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.assemble_v2 import assemble_and_validate_v2
from app.backtest.strategy_h_v2.research.d3_3_contract import (
    D3_3_HARD_BUDGET_USD,
    D3_3_SAMPLE,
    D3_3_WORST_CASE_CANDIDATE_USD,
)
from app.backtest.strategy_h_v2.research.prompt_builder_v2 import (
    PROMPT_VERSION_V2,
    build_repair_prompt_v2,
    build_research_prompt_v2,
)
from app.backtest.strategy_h_v2.research.repair import classify_repair_reason
from app.backtest.strategy_h_v2.research.research_attempt_v2 import (
    RepairAttemptRecordV1,
    ResearchAttemptRecordV1,
    compute_model_mismatch,
    extract_usage,
    store_attempt,
)
from app.backtest.strategy_h_v2.research.schema_v2 import SCHEMA_VERSION as SCHEMA_VERSION_V2
from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1, checksum
from app.backtest.strategy_h_v2.research.validate import package_checksum

PACKAGES_DIR = Path("data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages")
ATTEMPTS_ROOT = Path("data/runtime/strategy_h_v2/d3_3/attempts")
MANIFEST_ROOT = Path("data/runtime/strategy_h_v2/d3_3")
MODEL = "claude-opus-5-5"
VALIDATION_CONTRACT_VERSION = "h_v2_d3_2_validation_contract_v2"
MAX_BUDGET_USD_PER_CALL = 2.00
MAX_REPAIR_ATTEMPTS = 2
DISALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,TodoWrite"

CallFn = Callable[[str, str], dict]


def call_opus(system_prompt: str, user_prompt: str) -> dict:
    """The one function in this module that makes a real call. Structurally identical to
    `run_strategy_h_v2_d3.py::call_opus` (same CLI invocation, same argv/stdin split for the
    evidence-size limit) - restated here, not imported, so this runner does not depend on the V1
    runner file at all."""
    exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
    if not exec_path:
        raise RuntimeError("CLAUDE_CODE_EXECPATH is not set; live model execution is not available")
    cmd = [
        exec_path, "-p", "--model", MODEL, "--system-prompt", system_prompt,
        "--disallowed-tools", DISALLOWED_TOOLS, "--output-format", "json",
        "--max-budget-usd", str(MAX_BUDGET_USD_PER_CALL),
    ]
    proc = subprocess.run(cmd, input=user_prompt, capture_output=True, text=True, timeout=300)
    return json.loads(proc.stdout)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _raw_response(candidate_id: str, attempt: int, role: str, raw: dict) -> RawResponseRecordV1:
    return RawResponseRecordV1.capture(
        candidate_id=candidate_id, attempt=attempt, role=role, raw_text=raw.get("result", ""),
        prompt_version=PROMPT_VERSION_V2,
    )


def _canonical_model(raw: dict) -> str | None:
    return raw.get("modelUsage", {}).get(MODEL, {}).get("canonicalModel")


def research_one_v3(
    package: AIResearchInputV1, ticker: str, cik: str, research_run_id: str, *,
    call_fn: CallFn = call_opus, attempt_id: str | None = None,
) -> ResearchAttemptRecordV1:
    """The full candidate lifecycle: one initial call, up to `MAX_REPAIR_ATTEMPTS` bounded repair
    rounds (schema/contract correction only - `build_repair_prompt_v2` never asks for new evidence
    or an investment judgment, brief §6), full telemetry throughout. Returns the record; does NOT
    store it - call `research_attempt_v2.store_attempt` with the result (kept separate so a test can
    inspect the record without touching the filesystem, and so a caller decides its own attempt
    root)."""
    attempt_id = attempt_id or f"{ticker}-{uuid.uuid4()}"
    started_at = _now_iso()
    bundle = package.evidence_bundle
    input_package_id = f"{package.run_id}:{bundle.ticker}"
    input_package_checksum = package_checksum(package)

    system, user = build_research_prompt_v2(package)
    raw = call_fn(system, user)
    initial_response = _raw_response(ticker, 0, "initial", raw)
    canonical_model = _canonical_model(raw)
    total_cost = raw.get("total_cost_usd") or 0.0
    input_tokens, output_tokens = extract_usage(raw)

    def _base(**overrides) -> ResearchAttemptRecordV1:
        fields = dict(
            attempt_id=attempt_id, research_run_id=research_run_id, candidate_id=ticker,
            ticker=ticker, cik=cik, input_package_id=input_package_id,
            input_package_checksum=input_package_checksum, model_requested=MODEL,
            canonical_model=canonical_model,
            model_mismatch=compute_model_mismatch(MODEL, canonical_model),
            prompt_version=PROMPT_VERSION_V2, schema_version=SCHEMA_VERSION_V2,
            validation_contract_version=VALIDATION_CONTRACT_VERSION, started_at=started_at,
            completed_at=None, initial_raw_response=initial_response,
            initial_parse_status="NOT_ATTEMPTED", initial_validation_status="NOT_ATTEMPTED",
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=total_cost,
        )
        fields.update(overrides)
        return ResearchAttemptRecordV1(**fields)

    if raw.get("is_error"):
        return _base(completed_at=_now_iso(), final_status="MODEL_CALL_FAILED")

    research_id = f"D3.3-{ticker}-{started_at.replace(':', '').replace('-', '')}"
    created_at = datetime.now(timezone.utc)
    raw_text = raw.get("result", "")
    output, errors = assemble_and_validate_v2(
        raw_text, package, research_id=research_id, version=1, model=MODEL,
        model_version=canonical_model, prompt_version=PROMPT_VERSION_V2, created_at=created_at,
    )
    parse_status = "JSON_PARSE_ERROR" if errors and "JSON_PARSE_ERROR" in errors[0] else "PARSED"
    validation_status = "OK" if output is not None else "FAILED"

    repair_records: list[RepairAttemptRecordV1] = []
    attempt_n = 0
    while output is None and attempt_n < MAX_REPAIR_ATTEMPTS:
        failure_codes_before = [classify_repair_reason(errors).value]
        repair_prompt = build_repair_prompt_v2(raw_text, errors)
        raw = call_fn(system, repair_prompt)
        total_cost += raw.get("total_cost_usd") or 0.0
        repair_raw = _raw_response(ticker, attempt_n + 1, "repair", raw)
        if raw.get("is_error"):
            repair_records.append(RepairAttemptRecordV1(
                attempt_number=attempt_n, failure_codes_before=failure_codes_before,
                repair_prompt_version=PROMPT_VERSION_V2, raw_repair_response=repair_raw,
                validation_after="MODEL_CALL_FAILED", cost_usd=raw.get("total_cost_usd") or 0.0,
            ))
            return _base(completed_at=_now_iso(), final_status="MODEL_CALL_FAILED",
                        initial_parse_status=parse_status, initial_validation_status=validation_status,
                        initial_failure_codes=failure_codes_before, initial_failure_details=errors,
                        repair_attempts=repair_records, cost_usd=total_cost)
        attempt_n += 1
        raw_text = raw.get("result", "")
        output, errors = assemble_and_validate_v2(
            raw_text, package, research_id=research_id, version=1, model=MODEL,
            model_version=_canonical_model(raw) or canonical_model, prompt_version=PROMPT_VERSION_V2,
            created_at=created_at,
        )
        repair_records.append(RepairAttemptRecordV1(
            attempt_number=attempt_n - 1, failure_codes_before=failure_codes_before,
            repair_prompt_version=PROMPT_VERSION_V2, raw_repair_response=repair_raw,
            validation_after="OK" if output is not None else "FAILED",
            cost_usd=raw.get("total_cost_usd") or 0.0,
        ))

    final_raw = repair_records[-1].raw_repair_response if repair_records else initial_response
    if output is None:
        return _base(completed_at=_now_iso(), final_status="SCHEMA_VALIDATION_FAILED",
                    initial_parse_status=parse_status, initial_validation_status=validation_status,
                    initial_failure_codes=[classify_repair_reason(errors).value] if errors else [],
                    initial_failure_details=errors, repair_attempts=repair_records,
                    final_raw_response=final_raw, cost_usd=total_cost)

    output_dict = json.loads(output.model_dump_json())
    return _base(
        completed_at=_now_iso(), final_status="OK", initial_parse_status=parse_status,
        initial_validation_status=validation_status, repair_attempts=repair_records,
        final_raw_response=final_raw, final_output=output_dict,
        final_output_checksum=checksum(json.dumps(output_dict, sort_keys=True, default=str)),
        cost_usd=total_cost,
    )


def run_sample(
    *, sample=D3_3_SAMPLE, hard_budget_usd: float = D3_3_HARD_BUDGET_USD,
    call_fn: CallFn = call_opus, attempts_root: Path = ATTEMPTS_ROOT,
    manifest_root: Path = MANIFEST_ROOT,
) -> dict:
    """Runs the frozen 12-candidate sample, stopping BEFORE any candidate whose worst case would
    breach `hard_budget_usd` (brief §10) - not a retroactive check. Stores every attempt immutably
    via `store_attempt`. Returns a manifest dict; does not itself decide L1-L7 (that needs the
    manual audit, §13, which this function cannot perform)."""
    research_run_id = datetime.now(timezone.utc).strftime("D3_3-%Y%m%dT%H%M%SZ")
    results = []
    spent = 0.0
    stopped_early = None
    for entry in sample:
        if spent + D3_3_WORST_CASE_CANDIDATE_USD > hard_budget_usd:
            stopped_early = entry.ticker
            break
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{entry.ticker}.json").read_text())
        record = research_one_v3(package, entry.ticker, entry.cik, research_run_id, call_fn=call_fn)
        store_attempt(attempts_root, record)
        spent += record.cost_usd
        results.append({"ticker": entry.ticker, "cik": entry.cik, "depth": entry.depth,
                        "final_status": record.final_status, "cost_usd": record.cost_usd,
                        "model_mismatch": record.model_mismatch, "attempt_id": record.attempt_id})
    manifest = {
        "schema": "H_V2_D3_3_RUN_MANIFEST_V1", "run_id": research_run_id,
        "sample_size": len(sample), "attempted": len(results), "stopped_early": stopped_early,
        "hard_budget_usd": hard_budget_usd, "total_cost_usd": spent, "results": results,
    }
    manifest_root.mkdir(parents=True, exist_ok=True)
    (manifest_root / f"{research_run_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    return manifest


def main() -> None:
    """NOT invoked by D3.2F. `H_V2_D3_3_LIVE_CONFIRMATION_PREREGISTRATION_V1.md` must be read and
    D3.3 explicitly authorized by the user before this is ever called."""
    raise RuntimeError(
        "run_strategy_h_v2_d3_3.main() is intentionally not wired to a CLI entry point yet - D3.3 "
        "requires explicit user authorization after reading the preregistration document. Call "
        "run_sample() directly, deliberately, once authorized."
    )


if __name__ == "__main__":
    main()
