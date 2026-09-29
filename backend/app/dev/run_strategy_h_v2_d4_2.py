"""H-V2-D4.2 Tier A re-run runner: the same shakedown, under the repaired execution contract.

Structurally this is `run_strategy_h_v2_d4_1.py` with four things changed and nothing else:

  - the budget preflight reserves the candidate's whole CALL TOPOLOGY (initial + up to two
    repairs) instead of one call's cap, which is the arithmetic D4.1 got wrong;
  - every candidate's spend is recorded split into initial, per-repair and total, so a repair
    round cannot be paid for out of an unnamed second budget;
  - repair-reason classification comes from `expectation/d4_inputs.py` rather than from an
    untracked module, so this file does not need a working-tree-only import to run;
  - the contract versions it stamps are V2's.

It makes zero calls on import and has no CLI entry point. D4.2's contract-repair phase does not
execute anything live (brief §30): the re-run is a separate user authorization, taken after the
repair is reviewed.

Tier B is deliberately absent from this module. Tier A must return MECHANICAL READY first
(brief §28), and under the repaired worst-case accounting Tier B no longer fits the $30.00 cap at
all - `d4_2_contract.TIER_B_FITS_REMAINING_CAP` records that as a checked fact rather than a note.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping
import gzip
import json
import os
import subprocess
import uuid

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.analysis_schema import (
    SCHEMA_VERSION as D4_SCHEMA_VERSION,
)
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    CandidateSpend,
    D4_CONTRACT_VERSION,
    MAX_REPAIR_ATTEMPTS,
    run_total_cost_usd,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    D3_3_ATTEMPTS_ROOT,
    D4_2_CONTRACT_VERSION,
    D4_2_REMAINING_CAP_USD,
    D4_2_ROOT,
    OVERALL_HARD_CAP_USD,
    PER_CALL_MAX_BUDGET_USD,
    TIER_A_BUDGET,
    TIER_A_HARD_BUDGET_USD,
    TIER_A_TICKERS_UNCHANGED,
)
from app.backtest.strategy_h_v2.expectation.d4_inputs import classify_d4_repair_reason
from app.backtest.strategy_h_v2.expectation.evidence_builder import (
    build_expectation_evidence_bundle,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.gap_contract import GAP_CONTRACT_VERSION
from app.backtest.strategy_h_v2.expectation.ledger import (
    D4AnalysisRecordV1,
    D4RepairRoundV1,
    LEDGER_SCHEMA_VERSION,
    store_analysis,
    store_evidence_bundle,
)
from app.backtest.strategy_h_v2.expectation.price_engine import SESSION_CLOSE_LATEST_UTC
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION, build_d4_prompt
from app.backtest.strategy_h_v2.expectation.validate import (
    VALIDATION_CONTRACT_VERSION,
    assemble_and_validate_d4,
    bundle_checksum,
)
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.backtest.strategy_h_v2.research.prompt_builder_v2 import build_repair_prompt_v2
from app.backtest.strategy_h_v2.research.research_attempt_v2 import (
    compute_model_mismatch,
    extract_usage,
)
from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1, checksum
from app.backtest.strategy_h_v2.research.validate import package_checksum

DAILY_PANEL_DIR = Path("data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily")
ANALYSES_ROOT = D4_2_ROOT / "analyses"

MODEL = "claude-opus-5-5"
BENCHMARK_TICKER = "SPY"
DISALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,TodoWrite"

CallFn = Callable[[str, str], dict]


def call_opus(system_prompt: str, user_prompt: str) -> dict:
    """The one function here that makes a real call.

    `--max-budget-usd` is a per-CALL cap. That is not a new reading of the flag; it is what it
    always did, and D4.1's budget contract treating the same number as a per-CANDIDATE reserve is
    the defect `TIER_A_BUDGET` exists to fix. The flag is unchanged; the reserve is not.
    """
    exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
    if not exec_path:
        raise RuntimeError("CLAUDE_CODE_EXECPATH is not set; live model execution is not available")
    cmd = [
        exec_path, "-p", "--model", MODEL, "--system-prompt", system_prompt,
        "--disallowed-tools", DISALLOWED_TOOLS, "--output-format", "json",
        "--max-budget-usd", str(PER_CALL_MAX_BUDGET_USD),
    ]
    proc = subprocess.run(cmd, input=user_prompt, capture_output=True, text=True, timeout=600)
    return json.loads(proc.stdout)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_model(raw: dict) -> str | None:
    return raw.get("modelUsage", {}).get(MODEL, {}).get("canonicalModel")


# --- price panel ---------------------------------------------------------------------------------

def load_price_panel(
    tickers: Iterable[str], *, panel_dir: Path = DAILY_PANEL_DIR,
) -> dict[str, dict[date, float]]:
    """Daily closes for `tickers` plus the benchmark, in ONE pass over the grouped-daily store."""
    wanted = set(tickers) | {BENCHMARK_TICKER}
    panel: dict[str, dict[date, float]] = {ticker: {} for ticker in wanted}
    for path in sorted(panel_dir.rglob("*.json.gz")):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if "body" not in doc or "session" not in doc:
            continue
        session = date.fromisoformat(doc["session"])
        for row in doc["body"].get("results", []):
            ticker = row.get("T")
            if ticker in wanted and isinstance(row.get("c"), (int, float)):
                panel[ticker][session] = float(row["c"])
    return panel


def pit_trim(series: Mapping[date, float], decision_time: datetime) -> dict[date, float]:
    """Drop sessions that could not have closed by `decision_time`, using the same later bound
    (21:00Z) `pit_eligible_series` enforces with."""
    cutoff = decision_time.astimezone(timezone.utc)
    return {
        session: close for session, close in series.items()
        if datetime.combine(session, SESSION_CLOSE_LATEST_UTC.replace(tzinfo=None),
                            tzinfo=timezone.utc) <= cutoff
    }


def build_bundle_for(
    package: AIResearchInputV1, panel: Mapping[str, Mapping[date, float]], *,
    bundle_id: str, generated_at: datetime,
) -> ExpectationEvidenceBundleV1:
    decision_time = package.evidence_bundle.data_cutoff
    return build_expectation_evidence_bundle(
        package,
        bundle_id=bundle_id,
        series=pit_trim(panel.get(package.evidence_bundle.ticker, {}), decision_time),
        benchmark=pit_trim(panel.get(BENCHMARK_TICKER, {}), decision_time),
        generated_at=generated_at,
    )


# --- one D4 analysis -----------------------------------------------------------------------------

def analyze_one_v2(
    *,
    package: AIResearchInputV1,
    research_record: dict,
    bundle: ExpectationEvidenceBundleV1,
    analysis_run_id: str,
    call_fn: CallFn = call_opus,
    analysis_id: str | None = None,
) -> tuple[D4AnalysisRecordV1, CandidateSpend]:
    """One candidate's full D4 lifecycle, plus the spend broken out per call.

    The second return value is the brief §11 record: what the initial call cost, what each repair
    cost, and the candidate total. D4.1 stored only the total, so the one question its budget
    defect actually raised - how much of this was repair? - could not be answered from the record.
    """
    ticker = bundle.ticker
    analysis_id = analysis_id or f"{ticker}-{uuid.uuid4()}"
    started_at = _now_iso()
    research_output = research_record["final_output"]
    code_facts = build_code_fact_index(
        bundle, research_facts=package.evidence_bundle.fundamental_changes,
    )

    system, user = build_d4_prompt(
        package=package, bundle=bundle, research_output=research_output, code_facts=code_facts,
    )
    raw = call_fn(system, user)
    initial_response = RawResponseRecordV1.capture(
        candidate_id=ticker, attempt=0, role="initial", raw_text=raw.get("result", ""),
        prompt_version=PROMPT_VERSION,
    )
    canonical_model = _canonical_model(raw)
    initial_cost = raw.get("total_cost_usd") or 0.0
    repair_costs: list[float] = []
    input_tokens, output_tokens = extract_usage(raw)

    def _spend() -> CandidateSpend:
        return CandidateSpend(ticker=ticker, initial_cost_usd=initial_cost,
                              repair_costs_usd=tuple(repair_costs))

    def _base(**overrides) -> D4AnalysisRecordV1:
        fields = dict(
            schema_version=LEDGER_SCHEMA_VERSION, analysis_id=analysis_id,
            analysis_run_id=analysis_run_id, candidate_id=bundle.company_id, ticker=ticker,
            research_input_id=research_output.get("research_id", "UNKNOWN"),
            research_output_checksum=research_record.get("final_output_checksum") or "UNKNOWN",
            expectation_evidence_id=bundle.bundle_id,
            expectation_evidence_checksum=bundle_checksum(bundle),
            input_package_id=f"{package.run_id}:{ticker}",
            input_package_checksum=package_checksum(package),
            model_requested=MODEL, canonical_model=canonical_model,
            model_mismatch=compute_model_mismatch(MODEL, canonical_model),
            prompt_version=PROMPT_VERSION, schema_version_out=D4_SCHEMA_VERSION,
            gap_contract_version=GAP_CONTRACT_VERSION,
            validation_contract_version=VALIDATION_CONTRACT_VERSION,
            started_at=started_at, completed_at=None, initial_raw_response=initial_response,
            initial_parse_status="NOT_ATTEMPTED", initial_validation_status="NOT_ATTEMPTED",
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=_spend().candidate_total_cost_usd,
        )
        fields.update(overrides)
        return D4AnalysisRecordV1(**fields)

    if raw.get("is_error"):
        return _base(completed_at=_now_iso(), final_status="MODEL_CALL_FAILED"), _spend()

    created_at = datetime.now(timezone.utc)
    raw_text = raw.get("result", "")

    def _validate(text: str, model_version: str | None):
        return assemble_and_validate_d4(
            text, package=package, bundle=bundle, research_output=research_output,
            code_facts=code_facts, analysis_id=analysis_id, version=1, model_name=MODEL,
            model_version=model_version, prompt_version=PROMPT_VERSION, created_at=created_at,
        )

    output, errors = _validate(raw_text, canonical_model)
    parse_status = "JSON_PARSE_ERROR" if errors and "JSON_PARSE_ERROR" in errors[0] else "PARSED"
    validation_status = "OK" if output is not None else "FAILED"
    initial_failure_codes = [classify_d4_repair_reason(errors).value] if errors else []
    initial_failure_details = list(errors)

    repair_rounds: list[D4RepairRoundV1] = []
    attempt_n = 0
    while output is None and attempt_n < MAX_REPAIR_ATTEMPTS:
        failure_codes_before = [classify_d4_repair_reason(errors).value]
        failure_details_before = list(errors)
        raw = call_fn(system, build_repair_prompt_v2(raw_text, errors))
        round_cost = raw.get("total_cost_usd") or 0.0
        repair_costs.append(round_cost)
        repair_raw = RawResponseRecordV1.capture(
            candidate_id=ticker, attempt=attempt_n + 1, role="repair",
            raw_text=raw.get("result", ""), prompt_version=PROMPT_VERSION,
        )
        if raw.get("is_error"):
            repair_rounds.append(D4RepairRoundV1(
                attempt_number=attempt_n, failure_codes_before=failure_codes_before,
                failure_details_before=failure_details_before,
                repair_prompt_version=PROMPT_VERSION, raw_repair_response=repair_raw,
                validation_after="MODEL_CALL_FAILED", cost_usd=round_cost,
            ))
            return _base(
                completed_at=_now_iso(), final_status="MODEL_CALL_FAILED",
                initial_parse_status=parse_status, initial_validation_status=validation_status,
                initial_failure_codes=initial_failure_codes,
                initial_failure_details=initial_failure_details, repair_rounds=repair_rounds,
                cost_usd=_spend().candidate_total_cost_usd,
            ), _spend()
        attempt_n += 1
        raw_text = raw.get("result", "")
        output, errors = _validate(raw_text, _canonical_model(raw) or canonical_model)
        repair_rounds.append(D4RepairRoundV1(
            attempt_number=attempt_n - 1, failure_codes_before=failure_codes_before,
            failure_details_before=failure_details_before, repair_prompt_version=PROMPT_VERSION,
            raw_repair_response=repair_raw,
            validation_after="OK" if output is not None else "FAILED", cost_usd=round_cost,
        ))

    final_raw = repair_rounds[-1].raw_repair_response if repair_rounds else initial_response
    if output is None:
        return _base(
            completed_at=_now_iso(), final_status="SCHEMA_VALIDATION_FAILED",
            initial_parse_status=parse_status, initial_validation_status=validation_status,
            initial_failure_codes=initial_failure_codes,
            initial_failure_details=initial_failure_details, repair_rounds=repair_rounds,
            final_raw_response=final_raw, cost_usd=_spend().candidate_total_cost_usd,
            applied_contract_rules=[], final_output=None,
        ), _spend()

    output_dict = json.loads(output.model_dump_json())
    return _base(
        completed_at=_now_iso(), final_status="OK", initial_parse_status=parse_status,
        initial_validation_status=validation_status, initial_failure_codes=initial_failure_codes,
        initial_failure_details=initial_failure_details, repair_rounds=repair_rounds,
        final_raw_response=final_raw, final_output=output_dict,
        final_output_checksum=checksum(json.dumps(output_dict, sort_keys=True, default=str)),
        applied_contract_rules=list(output_dict.get("applied_contract_rules") or []),
        cost_usd=_spend().candidate_total_cost_usd,
    ), _spend()


# --- Tier A --------------------------------------------------------------------------------------

def d3_output_index(attempts_root: Path) -> dict[str, dict]:
    """Ticker -> the stored D3 attempt record whose `final_status` is OK. A ticker with more than
    one OK record keeps the latest by `completed_at`; there is no merging and no averaging."""
    index: dict[str, dict] = {}
    for path in sorted(attempts_root.rglob("*.json")):
        record = json.loads(path.read_text())
        if record.get("final_status") != "OK" or not record.get("final_output"):
            continue
        ticker = record["ticker"]
        current = index.get(ticker)
        if current is None or (record.get("completed_at") or "") > (
                current.get("completed_at") or ""):
            index[ticker] = record
    return index


def run_tier_a_v2(
    *, tickers: tuple[str, ...] = TIER_A_TICKERS_UNCHANGED, call_fn: CallFn = call_opus,
    hard_budget_usd: float = TIER_A_HARD_BUDGET_USD,
    d3_attempts_root: Path = D3_3_ATTEMPTS_ROOT, analyses_root: Path = ANALYSES_ROOT,
    manifest_root: Path = D4_2_ROOT, panel: Mapping[str, Mapping[date, float]] | None = None,
) -> dict:
    """Tier A on the frozen three issuers, under the V2 execution contract.

    The preflight reserves `TIER_A_BUDGET.candidate_worst_case_budget_usd` - the per-call cap times
    the call count - before each candidate starts, and stops rather than starting a candidate that
    could not finish inside the ceiling. D4.1's preflight reserved one third of that and so could
    not have stopped the overrun it produced.

    Adjudicates no gate. M1-M12 are computed by `audit_strategy_h_v2_d4_2.py` over the stored
    records, for the reason D4.1's runner gave: a runner that graded itself would be grading the
    only thing it could see.
    """
    run_id = datetime.now(timezone.utc).strftime("D4_2_A-%Y%m%dT%H%M%SZ")
    d3_outputs = d3_output_index(d3_attempts_root)
    missing = [t for t in tickers if t not in d3_outputs]
    if missing:
        raise RuntimeError(
            f"Tier A needs an existing D3 output for {missing} and none was found under "
            f"{d3_attempts_root}. D4 consumes a D3 output; it cannot manufacture one."
        )
    panel = panel if panel is not None else load_price_panel(tickers)
    generated_at = datetime.now(timezone.utc)

    results: list[dict] = []
    spends: list[CandidateSpend] = []
    spent = 0.0
    stopped_early: str | None = None
    budget_exhausted = False

    for ticker in tickers:
        if not TIER_A_BUDGET.admits_next_candidate(
            spent_so_far_usd=spent, overall_hard_cap_usd=hard_budget_usd
        ):
            stopped_early = ticker
            budget_exhausted = True
            break
        d3_record = d3_outputs[ticker]
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        bundle = build_bundle_for(
            package, panel, bundle_id=f"EB-{run_id}-{ticker}", generated_at=generated_at)
        store_evidence_bundle(analyses_root, run_id, ticker, bundle.model_dump_json(indent=2))
        record, spend = analyze_one_v2(
            package=package,
            research_record={"final_output": d3_record["final_output"],
                             "final_output_checksum": d3_record["final_output_checksum"]},
            bundle=bundle, analysis_run_id=run_id, call_fn=call_fn,
        )
        store_analysis(analyses_root, record)
        spends.append(spend)
        spent += spend.candidate_total_cost_usd
        results.append({
            "ticker": ticker, "cik": d3_record["cik"],
            "d3_attempt_id": d3_record["attempt_id"],
            "d3_cost_usd_already_spent_in_d3_3": d3_record["cost_usd"],
            "final_status": record.final_status,
            "model_mismatch": record.model_mismatch, "canonical_model": record.canonical_model,
            "analysis_id": record.analysis_id, "repair_rounds": len(record.repair_rounds),
            "expectation_gap": (record.final_output or {}).get("expectation_gap"),
            "expectation_gap_confidence": (record.final_output or {}).get(
                "expectation_gap_confidence"),
            "priced_in": ((record.final_output or {}).get("priced_in_assessment") or {}).get(
                "state"),
            "applied_contract_rules": record.applied_contract_rules,
            "spend": spend.to_dict(),
            "within_candidate_worst_case": (
                spend.candidate_total_cost_usd
                <= TIER_A_BUDGET.candidate_worst_case_budget_usd),
        })

    manifest = {
        "schema": "H_V2_D4_2_RUN_MANIFEST_V1", "run_id": run_id, "tier": "A",
        "contract_version": D4_2_CONTRACT_VERSION,
        "d4_contract_version": D4_CONTRACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
        "gap_contract_version": GAP_CONTRACT_VERSION,
        "model_requested": MODEL,
        "sample_size": len(tickers), "attempted": len(results),
        "stopped_early": stopped_early, "budget_exhausted": budget_exhausted,
        "budget_contract": TIER_A_BUDGET.to_dict(),
        "tier_hard_budget_usd": hard_budget_usd,
        "overall_hard_cap_usd": OVERALL_HARD_CAP_USD,
        "remaining_cap_before_run_usd": D4_2_REMAINING_CAP_USD,
        "run_total_cost_usd": run_total_cost_usd(spends),
        "candidate_spends": [s.to_dict() for s in spends],
        "results": results,
    }
    manifest_root.mkdir(parents=True, exist_ok=True)
    (manifest_root / f"{run_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    return manifest


def main() -> None:
    raise RuntimeError(
        "run_strategy_h_v2_d4_2.main() is intentionally not wired to a CLI entry point - the D4.2 "
        "Tier A re-run is a separate user authorization taken after the contract repair is "
        "reviewed (brief §30). Call run_tier_a_v2() deliberately."
    )


if __name__ == "__main__":
    main()
