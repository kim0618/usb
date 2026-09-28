"""H-V2 D1.1 - SEC fundamental data acquisition for the current H-V2 target universe.

Fetches, for every unique CIK in the current dated CS reference snapshot that is not already
locally cached, its SEC submissions (for PIT filing-acceptance timestamps) and companyfacts (raw
XBRL facts). Reuses the existing, already-audited rate-limited SEC clients unmodified:

- `app.backtest.strategy_c_e0.sec_store.SecClient` / `.fetch_cik` for submissions
- `app.backtest.strategy_eqm_v0.xbrl_store.fetch_all` for companyfacts

Both already guarantee: an identified User-Agent (refused otherwise), a serial 5 req/s ceiling
(half of SEC's stated 10/s allowance), bounded retry/backoff on 429/5xx, a typed 404-as-MISSING
result (never an error, never a fabricated zero), and raw-bytes-plus-ledger immutability (an
existing file is never re-fetched or overwritten). This script adds no new HTTP logic; it only
decides which CIKs to ask for, reusing `acquisition.build_acquisition_queue` for that decision.

New raw data is written under `data/runtime/strategy_h_v2/d1_1/sec_raw/`, a fresh store - existing
H0 (`data/runtime/strategy_h/h0/raw`) and PV2C (`.../h_pv2c/sec_raw`) caches are read-only inputs to
the "already cached" computation and are never touched.

Target universe: the same 5,192-row dated CS/XNYS/XNAS/XASE snapshot D1 already used - not the full
SEC filer population.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_eqm_v0 import xbrl_store
from app.backtest.strategy_h0.pilot import read_gzip_json
from app.backtest.strategy_h_v2.acquisition import (
    AcquisitionStatus,
    SOURCE_COMPANYFACTS,
    SOURCE_SUBMISSIONS,
    build_acquisition_queue,
)
from app.backtest.strategy_h_v2.universe import build_universe

REFERENCE = Path("data/runtime/research_universe_u1/reference/tickers/CS_2024-10-25.json.gz")
SNAPSHOT_DATE = "2024-10-25"

EXISTING_SUBMISSION_ROOTS = [
    Path("data/runtime/strategy_h/h_pv2c/sec_raw"),
    Path("data/runtime/strategy_c/e0/raw"),
    Path("data/runtime/strategy_h/h0_5/sec_raw"),
]
EXISTING_FACTS_ROOTS = [
    Path("data/runtime/strategy_h/h0/raw"),
    Path("data/runtime/strategy_h/h_pv2c/sec_raw"),
]
NEW_ROOT = Path("data/runtime/strategy_h_v2/d1_1/sec_raw")
USER_AGENT = "USB Research tjd6189@gmail.com"  # same identifier already used by H0/C-E0 in this repo
SUBMISSIONS_REQUIRED_FROM = date(2022, 1, 1)  # ~2 years of comparable quarters is enough for E2
OUTPUT_ROOT = Path("data/runtime/strategy_h_v2/d1_1")


def cached_ciks(roots: list[Path], subdir: str, pattern: str) -> frozenset[str]:
    found: set[str] = set()
    for root in roots:
        base = root / subdir if subdir else root
        if not base.exists():
            continue
        for path in base.glob(pattern):
            name = path.name.removeprefix("CIK")
            found.add(name.split(".")[0])
    return frozenset(found)


def main() -> None:
    run_id = datetime.now(timezone.utc).strftime("D1_1-%Y%m%dT%H%M%SZ")
    started_at = datetime.now(timezone.utc)

    reference_rows = [
        row for page in read_gzip_json(REFERENCE)["pages"] for row in page.get("results", [])
    ]
    universe = build_universe(reference_rows, snapshot_date=SNAPSHOT_DATE, known_at=started_at)

    cached_submissions = cached_ciks(EXISTING_SUBMISSION_ROOTS, "submissions", "CIK*") | cached_ciks(
        [NEW_ROOT], "submissions", "CIK*"
    )
    cached_companyfacts = cached_ciks(EXISTING_FACTS_ROOTS, "companyfacts", "CIK*.json.gz") | cached_ciks(
        [NEW_ROOT], "companyfacts", "CIK*.json.gz"
    )

    queue = build_acquisition_queue(
        universe, cached_submission_ciks=cached_submissions, cached_companyfacts_ciks=cached_companyfacts,
        queued_at=started_at, data_cutoff=started_at,
    )
    pending = [item for item in queue if item.status == AcquisitionStatus.PENDING]
    print(f"target unique CIKs: {len(queue)}; already fully cached: {len(queue) - len(pending)}; "
          f"pending: {len(pending)}")

    client = sec_store.SecClient(USER_AGENT)
    outcomes = {"FETCHED": 0, "PARTIAL": 0, "FAILED": 0, "CACHED": len(queue) - len(pending)}
    failures: list[dict[str, str]] = []

    try:
        for n, item in enumerate(pending, start=1):
            # A `SecFetchError` (network/HTTP failure after retries) is the only thing this script
            # treats as FAILED. A typed NOT_FOUND / NOT_COVERED result is a fact about the filer,
            # not a download failure - it stays cached-as-absent and the candidate remains
            # DATA_NOT_READY through E1, never silently treated as a fetched zero (§10 of the brief).
            errors: list[str] = []
            if SOURCE_SUBMISSIONS in item.missing_sources:
                try:
                    sec_store.fetch_cik(client, NEW_ROOT, item.cik, required_from=SUBMISSIONS_REQUIRED_FROM)
                except sec_store.SecFetchError as exc:
                    errors.append(f"submissions:{exc.code}")
            if SOURCE_COMPANYFACTS in item.missing_sources:
                try:
                    xbrl_store.fetch_cik(client, NEW_ROOT, item.cik)
                except sec_store.SecFetchError as exc:
                    errors.append(f"companyfacts:{exc.code}")

            if errors and len(errors) == len(item.missing_sources):
                outcomes["FAILED"] += 1
                failures.append({"cik": item.cik, "ticker": item.ticker, "error": "; ".join(errors)})
            elif errors:
                outcomes["PARTIAL"] += 1
                failures.append({"cik": item.cik, "ticker": item.ticker, "error": "; ".join(errors)})
            else:
                outcomes["FETCHED"] += 1

            if n % 200 == 0 or n == len(pending):
                print(f"[{n}/{len(pending)}] fetched={outcomes['FETCHED']} failed={outcomes['FAILED']} "
                      f"http_requests={client.accounting.http_requests} "
                      f"bytes={client.accounting.bytes_downloaded} "
                      f"retries={client.accounting.retries}")
    finally:
        accounting = client.accounting
        client.close()

    completed_at = datetime.now(timezone.utc)
    manifest = {
        "schema": "H_V2_D1_1_ACQUISITION_MANIFEST_V1",
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "user_agent": USER_AGENT,
        "requests_per_second": sec_store.REQUESTS_PER_SECOND,
        "submissions_required_from": SUBMISSIONS_REQUIRED_FROM.isoformat(),
        "target_count": len(queue),
        "cached_before_run": len(queue) - len(pending),
        "requested_count": len(pending),
        "outcomes": outcomes,
        "failures": failures,
        "http_requests": accounting.http_requests,
        "status_codes": dict(accounting.status_codes),
        "retries": accounting.retries,
        "download_bytes": accounting.bytes_downloaded,
        "new_root": str(NEW_ROOT),
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (OUTPUT_ROOT / f"{run_id}.manifest.json").write_text(manifest_json)
    (OUTPUT_ROOT / f"{run_id}.manifest.sha256").write_text(
        hashlib.sha256(manifest_json.encode()).hexdigest() + "\n"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
