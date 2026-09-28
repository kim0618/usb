"""Execute the Strategy H-V2 D3 AI Research Engine pilot against real D2.1 packages.

Invokes real Claude Opus 5.5 through the Claude Code CLI's own headless `print` mode
(`$CLAUDE_CODE_EXECPATH -p --model claude-opus-5-5 ...`, all repo/edit/bash tools disabled - a pure
text-in/text-out call, no side effects on the filesystem beyond this script's own writes). This is
NOT a fresh API integration: it reuses the same first-party Claude Code binary already running this
session, in its documented non-interactive scripting mode.

Deterministic, bounded pilot only - this script does not touch the full 2,010-candidate universe.
See `docs/backtest/strategy_h_v2/H_V2_D3_AI_RESEARCH_ENGINE_V1.md`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import subprocess
import sys

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.ledger import next_version, write_research_output
from app.backtest.strategy_h_v2.research.prompt_builder import PROMPT_VERSION, build_repair_prompt, build_research_prompt
from app.backtest.strategy_h_v2.research.validate import assemble_and_validate

PACKAGES_DIR = Path("data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages")
OUTPUT_ROOT = Path("data/runtime/strategy_h_v2/d3")
MODEL = "claude-opus-5-5"
MAX_BUDGET_USD_PER_CALL = 2.00
MAX_REPAIR_ATTEMPTS = 2
DISALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Task,TodoWrite"


def pick_pilot_tickers(n_full: int, n_core: int) -> list[str]:
    """Deterministic: alphabetically first N of each depth - never a hand-picked list of familiar
    names (D3 brief §30: "유명기업 수동선택 금지")."""
    full: list[str] = []
    core: list[str] = []
    for path in sorted(PACKAGES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        depth = data["evidence_bundle"]["collection_depth"]
        if depth == "FULL" and len(full) < n_full:
            full.append(path.stem)
        elif depth == "CORE" and len(core) < n_core:
            core.append(path.stem)
        if len(full) >= n_full and len(core) >= n_core:
            break
    return full + core


def call_opus(system_prompt: str, user_prompt: str) -> dict:
    exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
    if not exec_path:
        raise RuntimeError("CLAUDE_CODE_EXECPATH is not set; live model execution is not available")
    # The user prompt (evidence) can run past the OS argv size limit ("Argument list too long");
    # -p supports piping the prompt on stdin instead ("useful for pipes" per --help), so only the
    # much smaller system prompt goes on argv.
    cmd = [
        exec_path, "-p", "--model", MODEL, "--system-prompt", system_prompt,
        "--disallowed-tools", DISALLOWED_TOOLS, "--output-format", "json",
        "--max-budget-usd", str(MAX_BUDGET_USD_PER_CALL),
    ]
    proc = subprocess.run(cmd, input=user_prompt, capture_output=True, text=True, timeout=300)
    return json.loads(proc.stdout)


def research_one(package: AIResearchInputV1, ticker: str) -> dict:
    system, user = build_research_prompt(package)
    call_log: list[dict] = []
    raw = call_opus(system, user)
    call_log.append({"attempt": 0, "cost_usd": raw.get("total_cost_usd"), "is_error": raw.get("is_error")})
    if raw.get("is_error"):
        return {"ticker": ticker, "status": "MODEL_CALL_FAILED", "detail": raw.get("result"), "calls": call_log}

    model_version = raw.get("modelUsage", {}).get(MODEL, {}).get("canonicalModel", MODEL)
    created_at = datetime.now(timezone.utc)
    research_id = f"D3-{ticker}-{created_at.strftime('%Y%m%dT%H%M%SZ')}"
    version = next_version(OUTPUT_ROOT, ticker)

    raw_text = raw.get("result", "")
    output, errors = assemble_and_validate(
        raw_text, package, research_id=research_id, version=version, model=MODEL,
        model_version=model_version, prompt_version=PROMPT_VERSION, created_at=created_at,
    )
    attempt = 0
    while output is None and attempt < MAX_REPAIR_ATTEMPTS:
        attempt += 1
        repair_prompt = build_repair_prompt(raw_text, errors)
        raw = call_opus(system, repair_prompt)
        call_log.append({"attempt": attempt, "cost_usd": raw.get("total_cost_usd"), "is_error": raw.get("is_error")})
        if raw.get("is_error"):
            break
        raw_text = raw.get("result", "")
        output, errors = assemble_and_validate(
            raw_text, package, research_id=research_id, version=version, model=MODEL,
            model_version=model_version, prompt_version=PROMPT_VERSION, created_at=created_at,
        )

    total_cost = sum(c["cost_usd"] or 0 for c in call_log)
    if output is None:
        return {
            "ticker": ticker, "status": "SCHEMA_VALIDATION_FAILED", "errors": errors,
            "repair_attempts": attempt, "calls": call_log, "total_cost_usd": total_cost,
            "raw_output_preview": raw_text[:2000],
        }
    path = write_research_output(OUTPUT_ROOT, output)
    return {
        "ticker": ticker, "status": "OK", "repair_attempts": attempt, "calls": call_log,
        "total_cost_usd": total_cost, "ledger_path": str(path),
        "research_completeness": output.research_completeness.value,
    }


def main() -> None:
    n_full = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    n_core = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    tickers = pick_pilot_tickers(n_full, n_core)
    print(f"pilot tickers ({len(tickers)}): {tickers}")

    results = []
    for ticker in tickers:
        package = AIResearchInputV1.model_validate_json((PACKAGES_DIR / f"{ticker}.json").read_text())
        result = research_one(package, ticker)
        results.append(result)
        print(json.dumps(result, default=str)[:500])

    manifest = {
        "schema": "H_V2_D3_PILOT_MANIFEST_V1",
        "run_id": datetime.now(timezone.utc).strftime("D3-PILOT-%Y%m%dT%H%M%SZ"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "pilot_tickers": tickers,
        "results": results,
        "ok_count": sum(1 for r in results if r["status"] == "OK"),
        "schema_validation_failed_count": sum(1 for r in results if r["status"] == "SCHEMA_VALIDATION_FAILED"),
        "model_call_failed_count": sum(1 for r in results if r["status"] == "MODEL_CALL_FAILED"),
        "total_cost_usd": sum(r.get("total_cost_usd", 0) or 0 for r in results),
        "constraints": {
            "full_2010_run": False, "forward_return_used": False, "alpha_ranking_used": False,
            "expectation_gap_finalized": False, "decision_generated": False,
            "valuation_conclusion_generated": False, "price_target_generated": False,
            "paid_market_data_used": False,
        },
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / f"{manifest['run_id']}.manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
