"""C4-P1 incremental XBRL companyfacts fetch for the analog library's periodic reports.

Writes under ``data/runtime/strategy_c4/xbrl``; the frozen EQM-V0 store is read, never written.
"""

import argparse
import json
from pathlib import Path
import time

from app.backtest.strategy_c4_analog import stores
from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_eqm_v0 import xbrl_store

TARGETS = Path("data/runtime/strategy_c4/coverage/xbrl_targets.json")
STORE_ID = "C4_XBRL_FACTS_STORE_V1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-agent", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--requests-per-second", type=float, default=sec_store.REQUESTS_PER_SECOND)
    args = parser.parse_args()

    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    stored = stores.stored_xbrl_ciks(stores.StoreRoots())
    targets = [cik for cik in targets if cik not in stored]
    if args.limit:
        targets = targets[: args.limit]
    print(f"to_fetch={len(targets)} already_stored={len(stored)}", flush=True)
    if not targets:
        return
    started = time.perf_counter()
    root = stores.C4_XBRL_ROOT
    with sec_store.SecClient(sec_store.check_user_agent(args.user_agent),
                             requests_per_second=args.requests_per_second) as client:
        report = xbrl_store.fetch_all(
            client, root, targets,
            log=lambda message: print(f"{message} elapsed={time.perf_counter() - started:.0f}s",
                                      flush=True))
        accounting = client.accounting
    manifest = {
        "store_id": STORE_ID, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "user_agent": args.user_agent, "requested_ciks": len(targets),
        "fetched": report.fetched, "cached": report.cached, "missing": report.missing,
        "accounting": {"http_requests": accounting.http_requests, "retries": accounting.retries,
                       "bytes_downloaded": accounting.bytes_downloaded,
                       "status_codes": {str(k): v for k, v in accounting.status_codes.items()}},
    }
    root.mkdir(parents=True, exist_ok=True)
    manifest["store_digest"] = xbrl_store.store_digest(root)
    (root / "store_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "missing"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
