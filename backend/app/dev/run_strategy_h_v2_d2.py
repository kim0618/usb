"""Execute the Strategy H-V2 D2 Evidence Collector against D1.1's real P1/P2 candidates.

Converts each candidate's frozen `CandidateEvidenceStub` (D1 output) plus its already-local SEC
submissions metadata into an `EvidenceBundleV2`. No network access happens here - every source is
already local from D1.1's acquisition. No forward return, ranking, GPT judgment, or decision is
read or produced anywhere in this script. See
`docs/backtest/strategy_h_v2/H_V2_D2_EVIDENCE_COLLECTOR_V1.md`.

Reuses `app.dev.run_strategy_h_v2_d1.find_submission_doc`/`SUBMISSION_ROOTS` (already covers H0,
PV2C, H0.5, and D1.1's new store) rather than re-implementing the multi-root lookup.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys

from app.backtest.strategy_c_e0.sec_store import ledger_path, rows_of
from app.backtest.strategy_h_v2.evidence.bundle import CandidateSource, CollectionDepth
from app.backtest.strategy_h_v2.evidence.collector import assemble_evidence_bundle_v2
from app.dev.run_strategy_h_v2_d1 import find_submission_doc

CANDIDATES_DIR = Path("data/runtime/strategy_h_v2/d1/D1-20260928T054937Z/candidates")
OUTPUT_ROOT = Path("data/runtime/strategy_h_v2/d2")

PRIORITY_TO_SOURCE_DEPTH = {
    "P1_HIGH": (CandidateSource.E3_P1_HIGH, CollectionDepth.FULL),
    "P2_MEDIUM": (CandidateSource.E3_P2_MEDIUM, CollectionDepth.CORE),
}


def submissions_ledger_info(cik: str) -> tuple[str | None, datetime]:
    from app.dev.run_strategy_h_v2_d1 import SUBMISSION_ROOTS
    for root in SUBMISSION_ROOTS:
        doc_path = root / f"CIK{cik}" / f"CIK{cik}.json.gz"
        ledger = ledger_path(doc_path)
        if doc_path.exists() and ledger.exists():
            info = json.loads(ledger.read_text())
            fetched_at = info.get("fetched_at")
            checksum = info.get("file_sha256") or info.get("raw_sha256")
            if fetched_at:
                return checksum, datetime.fromisoformat(fetched_at)
    return None, datetime.now(timezone.utc)


def run(candidate_files: list[Path], *, run_id: str) -> dict:
    generated_at = datetime.now(timezone.utc)
    bundles_dir = OUTPUT_ROOT / run_id / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)

    completeness_counts: Counter = Counter()
    depth_counts: Counter = Counter()
    source_type_counts: Counter = Counter()
    errors: list[dict] = []
    written = 0

    for path in candidate_files:
        stub = json.loads(path.read_text())
        cik = stub.get("identity", {}).get("cik")
        priority = stub.get("research_priority", {}).get("state")
        mapping = PRIORITY_TO_SOURCE_DEPTH.get(priority)
        if mapping is None or cik is None:
            errors.append({"ticker": stub.get("ticker"), "reason": "no_priority_or_cik"})
            continue
        candidate_source, depth = mapping
        try:
            sub_doc = find_submission_doc(cik)
            rows = rows_of(sub_doc.get("filings", {}).get("recent") or {}) if sub_doc else []
            checksum, fetched_at = submissions_ledger_info(cik)
            data_cutoff = datetime.fromisoformat(stub["data_cutoff"])
            bundle = assemble_evidence_bundle_v2(
                stub, submissions_rows=rows, cik=cik, run_id=run_id, generated_at=generated_at,
                data_cutoff=data_cutoff, candidate_source=candidate_source, collection_depth=depth,
                submissions_checksum=checksum, submissions_fetched_at=fetched_at,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never silently dropped
            errors.append({"ticker": stub.get("ticker"), "reason": f"{type(exc).__name__}: {exc}"})
            continue

        (bundles_dir / f"{bundle.ticker}.json").write_text(bundle.model_dump_json(indent=2))
        written += 1
        completeness_counts[bundle.evidence_completeness.value] += 1
        depth_counts[bundle.collection_depth.value] += 1
        for source in bundle.source_manifest:
            source_type_counts[source.source_type.value] += 1

    manifest = {
        "schema": "H_V2_D2_MANIFEST_V1",
        "run_id": run_id,
        "generated_at": generated_at.isoformat(),
        "input_candidates": len(candidate_files),
        "bundles_written": written,
        "errors": errors,
        "completeness_counts": dict(completeness_counts),
        "depth_counts": dict(depth_counts),
        "source_type_counts": dict(source_type_counts),
        "constraints": {
            "forward_return_used": False, "alpha_ranking_used": False, "gpt_judgment_executed": False,
            "expectation_gap_evaluated": False, "valuation_decision_made": False,
            "decision_generated": False, "broker": False, "paper_trading": False, "paid_data": False,
        },
    }
    (OUTPUT_ROOT / run_id).mkdir(parents=True, exist_ok=True)
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (OUTPUT_ROOT / run_id / "manifest.json").write_text(manifest_json)
    (OUTPUT_ROOT / run_id / "manifest.sha256").write_text(
        hashlib.sha256(manifest_json.encode()).hexdigest() + "\n"
    )
    return manifest


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    all_files = sorted(CANDIDATES_DIR.glob("*.json"))
    if mode == "pilot":
        files = all_files[:20]  # deterministic (alphabetical by ticker), not outcome-selected
        run_id = datetime.now(timezone.utc).strftime("D2-PILOT-%Y%m%dT%H%M%SZ")
    else:
        files = all_files
        run_id = datetime.now(timezone.utc).strftime("D2-%Y%m%dT%H%M%SZ")
    manifest = run(files, run_id=run_id)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
