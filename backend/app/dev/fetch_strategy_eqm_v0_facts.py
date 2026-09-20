"""Collect the EQM-V0 raw XBRL store for the CIKs that can carry an event quality feature.

    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_strategy_eqm_v0_facts \
        --user-agent 'Organisation or name contact@domain'

Targets are read from the frozen C-E0 status table: every candidate row whose W_PRIMARY events
include a periodic report (E3b), which is the only event class whose economic magnitude SEC serves
as structured data. Strategy C is not re-run, re-decided or reopened by this script; the table is
read for its CIK column only, never for an outcome.
"""

import argparse
import json
from pathlib import Path

import pandas as pd

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_eqm_v0 import xbrl_store

C_E0_RUN = Path("data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13")
SUBTYPE = "E3b"


def target_ciks(status_path: Path, subtype: str = SUBTYPE) -> list[str]:
    frame = pd.read_parquet(status_path, columns=["cik", "event_subtypes"])
    wanted = frame["event_subtypes"].apply(lambda v: subtype in set(v) if v is not None else False)
    return sorted({c for c in frame.loc[wanted, "cik"].dropna().unique()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-agent", required=True,
                        help="SEC requires a real contact: 'Organisation or name contact@domain'")
    parser.add_argument("--status", type=Path, default=C_E0_RUN / "candidate_status.parquet")
    parser.add_argument("--root", type=Path, default=xbrl_store.DEFAULT_ROOT)
    parser.add_argument("--requests-per-second", type=float, default=sec_store.REQUESTS_PER_SECOND)
    args = parser.parse_args()

    ciks = target_ciks(args.status)
    print(f"targets={len(ciks)} root={args.root}", flush=True)
    with sec_store.SecClient(args.user_agent, requests_per_second=args.requests_per_second) as client:
        report = xbrl_store.fetch_all(client, args.root, ciks, log=lambda m: print(m, flush=True))
        accounting = client.accounting
    manifest = {
        "store_id": xbrl_store.STORE_ID,
        "source_status_table": str(args.status),
        "subtype": SUBTYPE,
        "requested_ciks": len(ciks),
        "fetched": report.fetched,
        "cached": report.cached,
        "missing": report.missing,
        "http_requests": accounting.http_requests,
        "status_codes": dict(accounting.status_codes),
        "retries": accounting.retries,
        "bytes_downloaded": accounting.bytes_downloaded,
        "user_agent": client.user_agent if hasattr(client, "user_agent") else args.user_agent,
    }
    manifest["store_digest"] = xbrl_store.store_digest(args.root)
    (args.root / "store_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                                   encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "missing"}, indent=1), flush=True)
    print(f"missing={len(report.missing)}", flush=True)


if __name__ == "__main__":
    main()
