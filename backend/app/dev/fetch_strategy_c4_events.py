"""C4-P1 incremental SEC submissions fetch: only the CIKs the analog library needs and C-E0 lacks.

Writes under ``data/runtime/strategy_c4/sec``. The frozen C-E0 store is read but never written,
so its digest stays exactly what the closed study recorded.
"""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import time

import numpy as np

from app.backtest.strategy_c4_analog import stores, universe
from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_c_selection.rules import load_rules as load_c_rules

ACTIVE_FILER_DAYS = 400
COVERAGE = Path("data/runtime/strategy_c4/coverage")
CACHE = Path("data/runtime/strategy_c4/cache")
STORE_ID = "C4_SEC_EVENT_STORE_V1"


def required_from_by_cik() -> dict[str, date]:
    masks = np.load(CACHE / "masks.npz")
    library, cik_code = masks["library_mask"], masks["cik_code"]
    root = universe.C_RAW_ROOT
    sessions = universe.usable_sessions(root, universe.all_sessions(root))
    names = json.loads((COVERAGE / "needed_ciks.json").read_text())
    rows_i, rows_j = np.nonzero(library)
    codes = cik_code[rows_i, rows_j]
    keep = codes >= 0
    earliest: dict[int, int] = {}
    for code, session_idx in zip(codes[keep], rows_i[keep]):
        code = int(code)
        if code not in earliest or session_idx < earliest[code]:
            earliest[code] = int(session_idx)
    # `names` is the sorted unique CIK list; cik_code indexes the order universe.cik_codes built.
    order = json.loads((COVERAGE / "cik_order.json").read_text()) if (COVERAGE / "cik_order.json").exists() \
        else None
    table = order or names
    return {table[code]: sessions[idx] - timedelta(days=ACTIVE_FILER_DAYS)
            for code, idx in earliest.items() if code < len(table)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--requests-per-second", type=float, default=sec_store.REQUESTS_PER_SECOND)
    args = parser.parse_args()
    load_c_rules()

    required = required_from_by_cik()
    roots = stores.StoreRoots()
    already = stores.stored_sec_ciks(roots)
    targets = [cik for cik in sorted(required) if cik not in already]
    if args.limit:
        targets = targets[: args.limit]
    floor = min(required.values()) if required else date(2023, 1, 1)
    print(f"needed={len(required)} stored={len(already)} to_fetch={len(targets)} "
          f"required_from min={floor} max={max(required.values())}", flush=True)
    if not targets:
        return

    started = time.perf_counter()
    root = stores.C4_SEC_ROOT
    results = []
    with sec_store.SecClient(sec_store.check_user_agent(args.user_agent),
                             requests_per_second=args.requests_per_second) as client:
        for n, cik in enumerate(targets, start=1):
            outcome = sec_store.fetch_cik(client, root, cik, required_from=required[cik])
            results.append({"cik": cik, "status": outcome.status, "rows": outcome.rows,
                            "pages": len(outcome.pages), "covered": outcome.covered,
                            "earliest_filing_date": outcome.earliest_filing_date,
                            "latest_filing_date": outcome.latest_filing_date,
                            "required_from": required[cik].isoformat()})
            if n % 100 == 0 or n == len(targets):
                acc = client.accounting
                print(f"{n}/{len(targets)} requests={acc.http_requests} "
                      f"bytes={acc.bytes_downloaded / 1e6:.0f}MB "
                      f"elapsed={time.perf_counter() - started:.0f}s", flush=True)
        accounting = client.accounting
    manifest = {
        "store_id": STORE_ID, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "user_agent": args.user_agent, "requested_ciks": len(targets),
        "statuses": {s: sum(1 for r in results if r["status"] == s) for s in {r["status"] for r in results}},
        "not_covered": [r["cik"] for r in results if not r["covered"]],
        "rows": sum(r["rows"] for r in results), "pages": sum(r["pages"] for r in results),
        "accounting": {"http_requests": accounting.http_requests, "retries": accounting.retries,
                       "bytes_downloaded": accounting.bytes_downloaded,
                       "status_codes": {str(k): v for k, v in accounting.status_codes.items()}},
        "per_cik": results,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "store_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    manifest["store_digest"] = sec_store.store_digest(root)
    (root / "store_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "per_cik"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
