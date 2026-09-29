"""Strategy H-V2 D3.1: Batch-2 generalization / stability validation.

Reuses the D3 engine unchanged (`research_one`, same model, same bounded repair loop) and adds the
three things D3.1 requires that a pilot did not need:

1. A deterministic 24-issuer sample that is CIK-disjoint from the 12 pilot issuers, so "it works"
   cannot be an artifact of the alphabetically-first companies the pilot happened to draw.
2. A hard total spend ceiling that stops the run *before* a call that could breach it.
3. A manifest checksum over the frozen sample, so the sample cannot be quietly re-rolled after
   the results are seen.

Also runs the D3.1 §3 regression (`--regression`) of the two pilot issuers whose repair behaviour
is under investigation. NOT a full 2,010-candidate run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.prompt_builder import PROMPT_VERSION
from app.dev.run_strategy_h_v2_d3 import MODEL, OUTPUT_ROOT, PACKAGES_DIR, research_one

#: D3.1 brief §5: a hard total ceiling on live-model spend for this stage, not a per-call cap.
HARD_BUDGET_USD = 60.00
#: Stop before starting a call that could not complete inside the ceiling (per-call cap is $2.00,
#: and a candidate may need up to 1 + MAX_REPAIR_ATTEMPTS calls).
WORST_CASE_CANDIDATE_USD = 6.00

#: The 12 issuers already researched in the D3 pilot. Batch 2 must not reuse them.
PILOT_TICKERS = ("AAON", "AAPL", "ABCB", "ABL", "ACA", "ACLS",
                 "A", "AA", "AAP", "AAT", "ABCL", "ABEO")

#: Fixed seed for the deterministic sample. Alphabetical order would have drawn Batch 2 from the
#: same "A..." neighbourhood as the pilot, which is exactly the clustering D3.1 is testing against;
#: hashing with a frozen seed is reproducible without being alphabetically biased.
SAMPLE_SEED = "H_V2_D3_1_BATCH2_V1"
N_FULL = 12
N_CORE = 12


def _index(packages_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(packages_dir.glob("*.json")):
        bundle = json.loads(path.read_text())["evidence_bundle"]
        rows.append({"ticker": path.stem, "cik": bundle["identity"]["cik"],
                     "depth": bundle["collection_depth"],
                     "priority": bundle["candidate_source"]})
    return rows


def _sample_key(ticker: str) -> str:
    return hashlib.sha256(f"{SAMPLE_SEED}:{ticker}".encode("utf-8")).hexdigest()


def select_batch2(rows: list[dict], pilot_tickers: tuple[str, ...] = PILOT_TICKERS,
                  n_full: int = N_FULL, n_core: int = N_CORE) -> list[dict]:
    """Deterministic and CIK-disjoint from the pilot.

    Exclusion is by CIK, not ticker: the same issuer can appear under a different ticker, and a
    "disjoint" batch that quietly re-researched a pilot company would invalidate the whole
    generalization claim.
    """
    pilot_ciks = {row["cik"] for row in rows if row["ticker"] in pilot_tickers}
    if len(pilot_ciks) != len(pilot_tickers):
        raise RuntimeError(f"expected {len(pilot_tickers)} pilot CIKs, resolved {len(pilot_ciks)}")
    eligible = [row for row in rows if row["cik"] not in pilot_ciks]
    picked: list[dict] = []
    # Brief §4 asks for 12 P1_HIGH/FULL + 12 P2_MEDIUM/CORE. Those two are 1:1 in the D2.1 run, but
    # the pairing is asserted rather than assumed so a future depth-policy change cannot silently
    # turn this into a depth-only sample.
    for depth, priority, count in (("FULL", "E3_P1_HIGH", n_full), ("CORE", "E3_P2_MEDIUM", n_core)):
        pool = sorted((row for row in eligible
                       if row["depth"] == depth and row["priority"] == priority),
                      key=lambda row: _sample_key(row["ticker"]))
        if len(pool) < count:
            raise RuntimeError(f"only {len(pool)} {depth} packages available, need {count}")
        picked.extend(pool[:count])
    return picked


def sample_checksum(sample: list[dict]) -> str:
    canonical = json.dumps(
        {"seed": SAMPLE_SEED,
         "sample": [[r["ticker"], r["cik"], r["depth"], r["priority"]] for r in sample]},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run(sample: list[dict], *, run_id: str, budget_usd: float, spent_usd: float = 0.0) -> dict:
    results: list[dict] = []
    stopped_early: str | None = None
    for row in sample:
        if spent_usd + WORST_CASE_CANDIDATE_USD > budget_usd:
            stopped_early = (f"budget ceiling: spent ${spent_usd:.2f}, a further candidate could "
                             f"reach ${spent_usd + WORST_CASE_CANDIDATE_USD:.2f} > ${budget_usd:.2f}")
            break
        ticker = row["ticker"]
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        result = research_one(package, ticker)
        result["depth"] = row["depth"]
        result["priority"] = row["priority"]
        result["cik"] = row["cik"]
        spent_usd += result.get("total_cost_usd", 0) or 0
        result["cumulative_cost_usd"] = round(spent_usd, 4)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != "raw_output_preview"},
                         default=str)[:400], flush=True)

    manifest = {
        "schema": "H_V2_D3_1_BATCH2_MANIFEST_V1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "sample_seed": SAMPLE_SEED,
        "sample_checksum": sample_checksum(sample),
        "sample": sample,
        "pilot_tickers_excluded": list(PILOT_TICKERS),
        "hard_budget_usd": budget_usd,
        "results": results,
        "attempted_count": len(results),
        "ok_count": sum(1 for r in results if r["status"] == "OK"),
        "schema_validation_failed_count":
            sum(1 for r in results if r["status"] == "SCHEMA_VALIDATION_FAILED"),
        "model_call_failed_count": sum(1 for r in results if r["status"] == "MODEL_CALL_FAILED"),
        "repair_free_count": sum(1 for r in results if r.get("repair_attempts") == 0),
        "total_cost_usd": round(spent_usd, 4),
        "stopped_early": stopped_early,
        "constraints": {
            "full_2010_run": False, "forward_return_used": False, "alpha_ranking_used": False,
            "expectation_gap_finalized": False, "decision_generated": False,
            "valuation_conclusion_generated": False, "price_target_generated": False,
            "paid_market_data_used": False,
        },
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / f"{run_id}.manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    print(f"\nmanifest: {OUTPUT_ROOT / (run_id + '.manifest.json')}")
    print(f"attempted={manifest['attempted_count']} ok={manifest['ok_count']} "
          f"repair_free={manifest['repair_free_count']} spend=${manifest['total_cost_usd']:.2f} "
          f"stopped_early={stopped_early}")
    return manifest


def main() -> None:
    args = sys.argv[1:]
    rows = _index(PACKAGES_DIR)
    if args and args[0] == "--regression":
        # D3.1 §3: re-run the two pilot issuers under the fixed contract before Batch 2 starts.
        sample = [row for row in rows if row["ticker"] in {"AAON", "A"}]
        run_id = datetime.now(timezone.utc).strftime("D3_1-REGRESSION-%Y%m%dT%H%M%SZ")
        budget = float(args[1]) if len(args) > 1 else 8.0
    else:
        sample = select_batch2(rows)
        run_id = datetime.now(timezone.utc).strftime("D3_1-BATCH2-%Y%m%dT%H%M%SZ")
        budget = float(args[0]) if args else HARD_BUDGET_USD
    print(f"run_id={run_id} n={len(sample)} checksum={sample_checksum(sample)} budget=${budget:.2f}")
    print("sample:", [r["ticker"] for r in sample], flush=True)
    run(sample, run_id=run_id, budget_usd=budget)


if __name__ == "__main__":
    main()
