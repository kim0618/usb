"""Execute the Strategy H-V2 D2.1 Official Evidence Materialization against D2's real bundles.

Fetches actual filing/exhibit document content (bounded by the P1/P2 depth policy in
`evidence/materialize.py`) for D2's 2,010 real Evidence Bundles, extracts text, chunks it, and
writes an immutable `AIResearchInputV1` package per candidate. Reuses the existing rate-limited
`SecClient` unmodified - same User-Agent, same 5 req/s ceiling, same retry/backoff as D1.1's
acquisition. See `docs/backtest/strategy_h_v2/H_V2_D2_1_OFFICIAL_EVIDENCE_MATERIALIZATION_V1.md`.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sys

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_h_v2.evidence.bundle import EvidenceBundleV2
from app.backtest.strategy_h_v2.evidence.materialize import materialize_candidate

BUNDLES_DIR = Path("data/runtime/strategy_h_v2/d2/D2-20260928T061101Z/bundles")
CACHE_ROOT = Path("data/runtime/strategy_h_v2/d2_1")
OUTPUT_ROOT = Path("data/runtime/strategy_h_v2/d2_1")
USER_AGENT = "USB Research tjd6189@gmail.com"


def run(bundle_files: list[Path], *, run_id: str) -> dict:
    packages_dir = OUTPUT_ROOT / run_id / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)

    readiness_counts: Counter = Counter()
    doc_role_status: dict[str, Counter] = {}
    chunk_total = 0
    chunk_bytes = 0
    errors: list[dict] = []

    client = sec_store.SecClient(USER_AGENT)
    try:
        for n, path in enumerate(bundle_files, start=1):
            bundle = EvidenceBundleV2.model_validate_json(path.read_text())
            try:
                result = materialize_candidate(client, CACHE_ROOT, bundle, candidate_id=bundle.ticker)
            except Exception as exc:  # noqa: BLE001 - recorded, never silently dropped
                errors.append({"ticker": bundle.ticker, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            (packages_dir / f"{bundle.ticker}.json").write_text(result.model_dump_json(indent=2))
            readiness_counts[result.materialization.content_readiness] += 1
            for doc in result.materialization.documents:
                doc_role_status.setdefault(doc.role, Counter())[doc.status.value] += 1
            chunk_total += len(result.chunks)
            chunk_bytes += sum(len(c.text.encode("utf-8")) for c in result.chunks)

            if n % 100 == 0 or n == len(bundle_files):
                print(f"[{n}/{len(bundle_files)}] readiness={dict(readiness_counts)} "
                      f"http_requests={client.accounting.http_requests} "
                      f"bytes={client.accounting.bytes_downloaded} "
                      f"retries={client.accounting.retries}")
    finally:
        accounting = client.accounting
        client.close()

    manifest = {
        "schema": "H_V2_D2_1_MANIFEST_V1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_bundles": len(bundle_files),
        "packages_written": len(bundle_files) - len(errors),
        "errors": errors,
        "content_readiness_counts": dict(readiness_counts),
        "document_role_status_counts": {role: dict(counter) for role, counter in doc_role_status.items()},
        "chunk_count": chunk_total,
        "chunk_bytes_estimate": chunk_bytes,
        "chunk_token_estimate": chunk_bytes // 4,
        "http_requests": accounting.http_requests,
        "status_codes": dict(accounting.status_codes),
        "retries": accounting.retries,
        "download_bytes": accounting.bytes_downloaded,
        "constraints": {
            "ai_company_judgment_executed": False, "future_business_evaluated": False,
            "catalyst_evaluated": False, "expectation_gap_evaluated": False,
            "decision_generated": False, "valuation_decision_made": False,
            "forward_return_used": False, "paid_data_used": False,
        },
    }
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (OUTPUT_ROOT / run_id / "manifest.json").write_text(manifest_json)
    (OUTPUT_ROOT / run_id / "manifest.sha256").write_text(
        hashlib.sha256(manifest_json.encode()).hexdigest() + "\n"
    )
    return manifest


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    all_files = sorted(BUNDLES_DIR.glob("*.json"))
    if mode == "pilot":
        # Deterministic, diverse-by-construction: first 10 P1 (FULL) + first 10 P2 (CORE) tickers
        # alphabetically, not outcome-selected, per D2.1 brief §18.
        full, core = [], []
        for path in all_files:
            bundle = EvidenceBundleV2.model_validate_json(path.read_text())
            (full if bundle.collection_depth.value == "FULL" else core).append(path)
            if len(full) >= 10 and len(core) >= 10:
                break
        files = full[:10] + core[:10]
        run_id = datetime.now(timezone.utc).strftime("D2_1-PILOT-%Y%m%dT%H%M%SZ")
    else:
        files = all_files
        run_id = datetime.now(timezone.utc).strftime("D2_1-%Y%m%dT%H%M%SZ")
    manifest = run(files, run_id=run_id)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
