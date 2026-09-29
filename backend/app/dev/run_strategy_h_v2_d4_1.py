"""H-V2-D4.1 live pilot runner: the Expectation Gap Engine's first real model calls.

Structure mirrors `run_strategy_h_v2_d3_3.py` deliberately - same telemetry discipline, same
before-not-after budget check, same `call_fn` seam so every function except `call_opus` is testable
with zero live calls. What differs is only what D4 differs in: the input is a D3 output plus a
code-owned expectation evidence bundle, and the two tiers of `d4_1_contract.py` are executed
separately because they answer different questions.

TIER A is a mechanical shakedown on three issuers whose D3 output already exists. It may never be
reported as a quality result (`d4_1_contract` states why), and `run_tier_a` therefore refuses to
adjudicate anything but the mechanical gates.

TIER B chains D3-then-D4 on six issuers disjoint from all 48 previously-touched CIKs. Its D3 leg
runs through the SAME validated Prompt V2 / Schema V2 / Validation V2 path D3.3 used, invoked by
importing D3.3's own `research_one_v3` rather than restating it - a D3 leg that drifted from the
contract D3.3 validated would make the D4 result uninterpretable.

Nothing here modifies a D3 artifact, the D4 contract, the gap contract, or a frozen threshold. On a
structural defect the runner records and stops; it never edits code and re-runs to get a result.
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
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    D3_WORST_CASE_CANDIDATE_USD,
    D4_1_CONTRACT_VERSION,
    D4_1_HARD_BUDGET_USD,
    D4_WORST_CASE_CANDIDATE_USD,
    TIER_A_N,
    TIER_A_TICKERS,
    TIER_B_SAMPLE,
    TIER_B_WORST_CASE_CANDIDATE_USD,
)
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
)
from app.backtest.strategy_h_v2.research.prompt_builder_v2 import build_repair_prompt_v2
from app.backtest.strategy_h_v2.research.repair import classify_repair_reason
from app.backtest.strategy_h_v2.research.research_attempt_v2 import (
    compute_model_mismatch,
    extract_usage,
    store_attempt,
)
from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1, checksum
from app.backtest.strategy_h_v2.research.validate import package_checksum
from app.dev.run_strategy_h_v2_d3_3 import (
    ATTEMPTS_ROOT as D3_3_ATTEMPTS_ROOT,
    PACKAGES_DIR,
    research_one_v3,
)

DAILY_PANEL_DIR = Path("data/runtime/strategy_b_e0/mirror/market_data/raw/massive/grouped_daily")
D4_1_ROOT = Path("data/runtime/strategy_h_v2/d4_1")
ANALYSES_ROOT = D4_1_ROOT / "analyses"
D3_LEG_ATTEMPTS_ROOT = D4_1_ROOT / "d3_leg_attempts"

MODEL = "claude-opus-5-5"
BENCHMARK_TICKER = "SPY"
MAX_BUDGET_USD_PER_CALL = 2.00
MAX_REPAIR_ATTEMPTS = 2
DISALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,TodoWrite"

#: Frozen here, before execution, from `d4_1_contract`'s own per-candidate worst cases. Tier A gets
#: exactly what the contract's §V table budgets it and no more, so a Tier A overrun cannot quietly
#: eat the tier that actually adjudicates the gates.
TIER_A_HARD_BUDGET_USD = TIER_A_N * D4_WORST_CASE_CANDIDATE_USD
TIER_B_HARD_BUDGET_USD = len(TIER_B_SAMPLE) * TIER_B_WORST_CASE_CANDIDATE_USD

CallFn = Callable[[str, str], dict]


def call_opus(system_prompt: str, user_prompt: str) -> dict:
    """The one function here that makes a real call. Same CLI invocation D3.3 used; restated rather
    than imported so the D3 runner's own call path can change without silently changing D4's."""
    exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
    if not exec_path:
        raise RuntimeError("CLAUDE_CODE_EXECPATH is not set; live model execution is not available")
    cmd = [
        exec_path, "-p", "--model", MODEL, "--system-prompt", system_prompt,
        "--disallowed-tools", DISALLOWED_TOOLS, "--output-format", "json",
        "--max-budget-usd", str(MAX_BUDGET_USD_PER_CALL),
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
    """Daily closes for `tickers` plus the benchmark, in ONE pass over the grouped-daily store.

    Per-ticker passes would re-read ~500 gzipped session files per candidate; the panel is the same
    for every candidate, so it is read once and sliced. No filtering by date happens here - PIT
    trimming is `pit_trim`'s job and the leak check is `pit_eligible_series`'s, and doing it in
    three places is how one of them ends up doing it differently.
    """
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
    """Drop sessions that could not have closed by `decision_time`, using the SAME later bound
    (21:00Z) `pit_eligible_series` enforces with. This is assembly, not enforcement: the builder
    still runs the leak check on what it is handed, so a bug here raises rather than passes."""
    cutoff = decision_time.astimezone(timezone.utc)
    return {
        session: close for session, close in series.items()
        if datetime.combine(session, SESSION_CLOSE_LATEST_UTC.replace(tzinfo=None),
                            tzinfo=timezone.utc) <= cutoff
    }


# --- one D4 analysis -----------------------------------------------------------------------------

def analyze_one(
    *,
    package: AIResearchInputV1,
    research_record: dict,
    bundle: ExpectationEvidenceBundleV1,
    analysis_run_id: str,
    call_fn: CallFn = call_opus,
    analysis_id: str | None = None,
) -> D4AnalysisRecordV1:
    """One candidate's full D4 lifecycle: one initial call, up to `MAX_REPAIR_ATTEMPTS` bounded
    schema-repair rounds, full untruncated telemetry throughout. Returns the record; storing it is
    the caller's decision, exactly as `research_one_v3` does for D3."""
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
    total_cost = raw.get("total_cost_usd") or 0.0
    input_tokens, output_tokens = extract_usage(raw)

    def _base(**overrides) -> D4AnalysisRecordV1:
        fields = dict(
            schema_version=LEDGER_SCHEMA_VERSION, analysis_id=analysis_id,
            analysis_run_id=analysis_run_id, candidate_id=bundle.company_id, ticker=ticker,
            research_input_id=research_output.get("research_id", "UNKNOWN"),
            research_output_checksum=research_record.get("final_output_checksum") or "UNKNOWN",
            expectation_evidence_id=bundle.bundle_id,
            expectation_evidence_checksum=_bundle_checksum(bundle),
            input_package_id=f"{package.run_id}:{ticker}",
            input_package_checksum=package_checksum(package),
            model_requested=MODEL, canonical_model=canonical_model,
            model_mismatch=compute_model_mismatch(MODEL, canonical_model),
            prompt_version=PROMPT_VERSION, schema_version_out=D4_SCHEMA_VERSION,
            gap_contract_version=GAP_CONTRACT_VERSION,
            validation_contract_version=VALIDATION_CONTRACT_VERSION,
            started_at=started_at, completed_at=None, initial_raw_response=initial_response,
            initial_parse_status="NOT_ATTEMPTED", initial_validation_status="NOT_ATTEMPTED",
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=total_cost,
        )
        fields.update(overrides)
        return D4AnalysisRecordV1(**fields)

    if raw.get("is_error"):
        return _base(completed_at=_now_iso(), final_status="MODEL_CALL_FAILED")

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
    initial_failure_codes = [classify_repair_reason(errors).value] if errors else []
    initial_failure_details = list(errors)

    repair_rounds: list[D4RepairRoundV1] = []
    attempt_n = 0
    while output is None and attempt_n < MAX_REPAIR_ATTEMPTS:
        failure_codes_before = [classify_repair_reason(errors).value]
        failure_details_before = list(errors)
        raw = call_fn(system, build_repair_prompt_v2(raw_text, errors))
        total_cost += raw.get("total_cost_usd") or 0.0
        repair_raw = RawResponseRecordV1.capture(
            candidate_id=ticker, attempt=attempt_n + 1, role="repair",
            raw_text=raw.get("result", ""), prompt_version=PROMPT_VERSION,
        )
        if raw.get("is_error"):
            repair_rounds.append(D4RepairRoundV1(
                attempt_number=attempt_n, failure_codes_before=failure_codes_before,
                failure_details_before=failure_details_before,
                repair_prompt_version=PROMPT_VERSION, raw_repair_response=repair_raw,
                validation_after="MODEL_CALL_FAILED", cost_usd=raw.get("total_cost_usd") or 0.0,
            ))
            return _base(
                completed_at=_now_iso(), final_status="MODEL_CALL_FAILED",
                initial_parse_status=parse_status, initial_validation_status=validation_status,
                initial_failure_codes=initial_failure_codes,
                initial_failure_details=initial_failure_details, repair_rounds=repair_rounds,
                cost_usd=total_cost,
            )
        attempt_n += 1
        raw_text = raw.get("result", "")
        output, errors = _validate(raw_text, _canonical_model(raw) or canonical_model)
        repair_rounds.append(D4RepairRoundV1(
            attempt_number=attempt_n - 1, failure_codes_before=failure_codes_before,
            failure_details_before=failure_details_before, repair_prompt_version=PROMPT_VERSION,
            raw_repair_response=repair_raw,
            validation_after="OK" if output is not None else "FAILED",
            cost_usd=raw.get("total_cost_usd") or 0.0,
        ))

    final_raw = repair_rounds[-1].raw_repair_response if repair_rounds else initial_response
    if output is None:
        return _base(
            completed_at=_now_iso(), final_status="SCHEMA_VALIDATION_FAILED",
            initial_parse_status=parse_status, initial_validation_status=validation_status,
            initial_failure_codes=initial_failure_codes,
            initial_failure_details=initial_failure_details, repair_rounds=repair_rounds,
            final_raw_response=final_raw, cost_usd=total_cost,
            applied_contract_rules=[], final_output=None,
        )

    output_dict = json.loads(output.model_dump_json())
    return _base(
        completed_at=_now_iso(), final_status="OK", initial_parse_status=parse_status,
        initial_validation_status=validation_status, initial_failure_codes=initial_failure_codes,
        initial_failure_details=initial_failure_details, repair_rounds=repair_rounds,
        final_raw_response=final_raw, final_output=output_dict,
        final_output_checksum=checksum(json.dumps(output_dict, sort_keys=True, default=str)),
        applied_contract_rules=list(output_dict.get("applied_contract_rules") or []),
        cost_usd=total_cost,
    )


def _bundle_checksum(bundle: ExpectationEvidenceBundleV1) -> str:
    from app.backtest.strategy_h_v2.expectation.validate import bundle_checksum

    return bundle_checksum(bundle)


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


# --- tiers ---------------------------------------------------------------------------------------

def _d3_output_index(attempts_root: Path) -> dict[str, dict]:
    """Ticker -> the stored D3 attempt record whose `final_status` is OK. A ticker with more than
    one OK record keeps the latest by `completed_at`; there is no merging and no averaging."""
    index: dict[str, dict] = {}
    for path in sorted(attempts_root.rglob("*.json")):
        record = json.loads(path.read_text())
        if record.get("final_status") != "OK" or not record.get("final_output"):
            continue
        ticker = record["ticker"]
        current = index.get(ticker)
        if current is None or (record.get("completed_at") or "") > (current.get("completed_at") or ""):
            index[ticker] = record
    return index


def run_tier_a(
    *, tickers: tuple[str, ...] = TIER_A_TICKERS, call_fn: CallFn = call_opus,
    hard_budget_usd: float = TIER_A_HARD_BUDGET_USD,
    d3_attempts_root: Path = D3_3_ATTEMPTS_ROOT, analyses_root: Path = ANALYSES_ROOT,
    manifest_root: Path = D4_1_ROOT, panel: Mapping[str, Mapping[date, float]] | None = None,
) -> dict:
    """Tier A: D4 only, on issuers whose D3.3 output already exists. Stops BEFORE a candidate whose
    worst case would breach the tier budget. Returns a manifest; adjudicates no gate - that is
    `audit_strategy_h_v2_d4_1.py`'s job, on the stored records."""
    run_id = datetime.now(timezone.utc).strftime("D4_1_A-%Y%m%dT%H%M%SZ")
    d3_outputs = _d3_output_index(d3_attempts_root)
    missing = [t for t in tickers if t not in d3_outputs]
    if missing:
        raise RuntimeError(
            f"Tier A needs an existing D3 output for {missing} and none was found under "
            f"{d3_attempts_root}. D4 consumes a D3 output; it cannot manufacture one."
        )
    panel = panel if panel is not None else load_price_panel(tickers)
    generated_at = datetime.now(timezone.utc)
    #: D3.3 already paid for these outputs. Charging their historical `cost_usd` against the D4.1
    #: budget would consume a budget on calls this pilot never makes, so the D3 leg of Tier A costs
    #: zero HERE while keeping its real checksum and id.
    def research_for(ticker: str, _package) -> tuple[dict | None, float]:
        record = d3_outputs[ticker]
        return {"final_output": record["final_output"],
                "final_output_checksum": record["final_output_checksum"]}, 0.0

    return _run_candidates(
        run_id=run_id, tier="A",
        entries=[{"ticker": t, "cik": d3_outputs[t]["cik"],
                  "d3_attempt_id": d3_outputs[t]["attempt_id"],
                  "d3_cost_usd_already_spent_in_d3_3": d3_outputs[t]["cost_usd"]}
                 for t in tickers],
        research_for=research_for,
        panel=panel, generated_at=generated_at, call_fn=call_fn,
        hard_budget_usd=hard_budget_usd, worst_case=D4_WORST_CASE_CANDIDATE_USD,
        analyses_root=analyses_root, manifest_root=manifest_root,
    )


def run_tier_b(
    *, sample=TIER_B_SAMPLE, call_fn: CallFn = call_opus,
    hard_budget_usd: float = TIER_B_HARD_BUDGET_USD,
    d3_leg_root: Path = D3_LEG_ATTEMPTS_ROOT, analyses_root: Path = ANALYSES_ROOT,
    manifest_root: Path = D4_1_ROOT, panel: Mapping[str, Mapping[date, float]] | None = None,
) -> dict:
    """Tier B: the chained D3 -> D4 run on six unseen issuers. The D3 leg is D3.3's own
    `research_one_v3` under D3.3's contract, unchanged - if it fails for a candidate, that candidate
    has no D4 leg and is recorded as `D3_LEG_FAILED` rather than given a substitute input."""
    run_id = datetime.now(timezone.utc).strftime("D4_1_B-%Y%m%dT%H%M%SZ")
    panel = panel if panel is not None else load_price_panel(e.ticker for e in sample)
    generated_at = datetime.now(timezone.utc)
    d3_records: dict[str, dict] = {}

    def research_for(ticker: str, package: AIResearchInputV1) -> tuple[dict | None, float]:
        record = research_one_v3(package, ticker, _cik_of(sample, ticker), run_id, call_fn=call_fn)
        store_attempt(d3_leg_root, record)
        d3_records[ticker] = {
            "attempt_id": record.attempt_id, "final_status": record.final_status,
            "cost_usd": record.cost_usd, "model_mismatch": record.model_mismatch,
            "canonical_model": record.canonical_model,
        }
        #: The cost is returned even when the leg FAILED: a failed D3 call was still paid for, and a
        #: budget that only counts successes is not a budget.
        if record.final_status != "OK":
            return None, record.cost_usd
        return ({"final_output": record.final_output,
                 "final_output_checksum": record.final_output_checksum}, record.cost_usd)

    manifest = _run_candidates(
        run_id=run_id, tier="B",
        entries=[{"ticker": e.ticker, "cik": e.cik, "depth": e.depth, "priority": e.priority}
                 for e in sample],
        research_for=research_for, panel=panel, generated_at=generated_at, call_fn=call_fn,
        hard_budget_usd=hard_budget_usd, worst_case=TIER_B_WORST_CASE_CANDIDATE_USD,
        analyses_root=analyses_root, manifest_root=manifest_root,
    )
    manifest["d3_leg"] = d3_records
    (manifest_root / f"{run_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    return manifest


def _cik_of(sample, ticker: str) -> str:
    for entry in sample:
        if entry.ticker == ticker:
            return entry.cik
    raise KeyError(ticker)


def _run_candidates(
    *, run_id: str, tier: str, entries: list[dict], research_for, panel, generated_at,
    call_fn: CallFn, hard_budget_usd: float, worst_case: float, analyses_root: Path,
    manifest_root: Path,
) -> dict:
    results = []
    spent = 0.0
    stopped_early = None
    for entry in entries:
        ticker = entry["ticker"]
        if spent + worst_case > hard_budget_usd:
            stopped_early = ticker
            break
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        research_record, research_cost = research_for(ticker, package)
        spent += research_cost
        if research_record is None:
            results.append({**entry, "final_status": "D3_LEG_FAILED",
                            "cost_usd": research_cost})
            continue
        bundle = build_bundle_for(
            package, panel, bundle_id=f"EB-{run_id}-{ticker}", generated_at=generated_at)
        store_evidence_bundle(analyses_root, run_id, ticker,
                              bundle.model_dump_json(indent=2))
        record = analyze_one(
            package=package, research_record=research_record, bundle=bundle,
            analysis_run_id=run_id, call_fn=call_fn,
        )
        store_analysis(analyses_root, record)
        spent += record.cost_usd
        results.append({
            **entry, "final_status": record.final_status,
            "cost_usd": research_cost + record.cost_usd, "d4_cost_usd": record.cost_usd,
            "model_mismatch": record.model_mismatch, "canonical_model": record.canonical_model,
            "analysis_id": record.analysis_id, "repair_rounds": len(record.repair_rounds),
            "expectation_gap": (record.final_output or {}).get("expectation_gap"),
            "expectation_gap_confidence": (record.final_output or {}).get(
                "expectation_gap_confidence"),
            "priced_in": ((record.final_output or {}).get("priced_in_assessment") or {}).get(
                "assessment"),
            "applied_contract_rules": record.applied_contract_rules,
        })
    manifest = {
        "schema": "H_V2_D4_1_RUN_MANIFEST_V1", "run_id": run_id, "tier": tier,
        "contract_version": D4_1_CONTRACT_VERSION, "model_requested": MODEL,
        "sample_size": len(entries), "attempted": len(results), "stopped_early": stopped_early,
        "tier_hard_budget_usd": hard_budget_usd, "d4_1_hard_budget_usd": D4_1_HARD_BUDGET_USD,
        "worst_case_per_candidate_usd": worst_case, "total_cost_usd": spent, "results": results,
    }
    manifest_root.mkdir(parents=True, exist_ok=True)
    (manifest_root / f"{run_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    return manifest


def main() -> None:
    raise RuntimeError(
        "run_strategy_h_v2_d4_1.main() is intentionally not wired to a CLI entry point - D4.1 is a "
        "separate user authorization. Call run_tier_a() first, adjudicate it, and only then "
        "run_tier_b(), deliberately."
    )


if __name__ == "__main__":
    main()
