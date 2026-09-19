"""Fetch the SEC EDGAR event raw store for the frozen C-M0 candidate CIKs (C-E0 step 2).

    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_strategy_c_e0_events --plan-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_strategy_c_e0_events \
        --user-agent "Your Org your.contact@domain"

Reads the baseline run's candidate table and the C raw CS snapshots, never the C-M results. SEC
requires a declared contact in the User-Agent: it is supplied here and never invented or
defaulted. Serial, 5 requests per second, resumable (a cached page costs no request).

The run stops at the store and its manifest. Statistics are a separate CLI, and the timezone
audit sample (--timezone-sample) is fetched here so the audit can run before any join.
"""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import random

import pandas as pd

from app.backtest.strategy_c_e0 import sec_store
from app.backtest.strategy_c_e0.cik_map import load_snapshot_maps, map_candidate
from app.backtest.strategy_c_e0.rules import load_declaration
from app.backtest.strategy_c_e0.taxonomy import Taxonomy

BASELINE = Path("data/runtime/strategy_c/runs/cmsel1-855b6a0ce64e3698fc74")
C_RAW = Path("data/runtime/strategy_c/raw")
ACTIVE_FILER_DAYS = 400
TIMEZONE_SAMPLE = 40
MANIFEST = "store_manifest.json"
TIMEZONE_SAMPLES = "timezone_audit_samples.json"


def candidate_ciks(baseline: Path, raw: Path, variant: str) -> tuple[dict[str, date], pd.DataFrame]:
    frame = pd.read_parquet(baseline / "candidates_primary.parquet")
    rows = frame[frame["variant"] == variant][["signal_date", "ticker"]].copy()
    maps = load_snapshot_maps(raw)
    mapped = [map_candidate(maps, t, date.fromisoformat(d)) for d, t in zip(rows.signal_date, rows.ticker)]
    rows["cik"] = [m.cik for m in mapped]
    rows["cik_as_of"] = [m.as_of.isoformat() if m.as_of else None for m in mapped]
    rows["map_reason"] = [m.reason for m in mapped]
    required: dict[str, date] = {}
    for cik, signal_date in zip(rows.cik, rows.signal_date):
        if cik is None:
            continue
        needed = date.fromisoformat(signal_date) - timedelta(days=ACTIVE_FILER_DAYS)
        required[cik] = min(required.get(cik, needed), needed)
    return required, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-agent", default=None,
                        help="SEC contact identifier, e.g. 'Your Org you@domain'. Required to fetch.")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--raw", type=Path, default=C_RAW)
    parser.add_argument("--root", type=Path, default=sec_store.DEFAULT_ROOT)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="fetch at most N CIKs (smoke test)")
    parser.add_argument("--timezone-sample", type=int, default=TIMEZONE_SAMPLE)
    parser.add_argument("--requests-per-second", type=float, default=sec_store.REQUESTS_PER_SECOND)
    args = parser.parse_args()

    declaration = load_declaration()
    required, rows = candidate_ciks(args.baseline, args.raw, declaration.base_variant)
    cached = sum(1 for cik in required
                 if sec_store.ledger_path(sec_store.submissions_path(args.root, cik, f"CIK{cik}.json")).exists())
    print(f"variant={declaration.base_variant} candidate_rows={len(rows)} "
          f"unmapped_rows={int(rows.cik.isna().sum())} unique_ciks={len(required)} cached_primary_pages={cached}")
    print(f"required_from min={min(required.values())} max={max(required.values())}")
    if args.plan_only:
        return

    user_agent = sec_store.check_user_agent(args.user_agent)
    targets = sorted(required)[: args.limit] if args.limit else sorted(required)
    results = []
    with sec_store.SecClient(user_agent, requests_per_second=args.requests_per_second) as client:
        for n, cik in enumerate(targets, start=1):
            result = sec_store.fetch_cik(client, args.root, cik, required_from=required[cik])
            results.append(result)
            if n % 100 == 0 or result.status != "OK":
                print(f"[{n}/{len(targets)}] cik={cik} status={result.status} rows={result.rows} "
                      f"pages={len(result.pages)} http={client.accounting.http_requests}", flush=True)
        samples = _timezone_samples(client, args.root, results, declaration,
                                    count=args.timezone_sample, window_start=min(required.values()))
        accounting = {"http_requests": client.accounting.http_requests,
                      "status_codes": dict(client.accounting.status_codes),
                      "retries": client.accounting.retries,
                      "bytes_downloaded": client.accounting.bytes_downloaded}

    manifest = {
        "store_id": "C_E0_SEC_EVENT_STORE_V1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_checksum": declaration.rules_checksum, "taxonomy_checksum": declaration.taxonomy_checksum,
        "baseline_run_id": declaration.baseline_run_id, "variant": declaration.base_variant,
        "user_agent": user_agent, "requested_ciks": len(targets),
        "statuses": {status: sum(1 for r in results if r.status == status)
                     for status in sorted({r.status for r in results})},
        "not_covered": [r.cik for r in results if not r.covered][:200],
        "pages": sum(len(r.pages) for r in results), "rows": sum(r.rows for r in results),
        "accounting": accounting, "timezone_samples": len(samples),
        "store_digest": sec_store.store_digest(args.root),
        "per_cik": [{"cik": r.cik, "status": r.status, "rows": r.rows, "pages": list(r.pages),
                     "earliest_filing_date": r.earliest_filing_date,
                     "latest_filing_date": r.latest_filing_date, "covered": r.covered,
                     "required_from": required[r.cik].isoformat()} for r in results],
    }
    (args.root / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    (args.root / TIMEZONE_SAMPLES).write_text(json.dumps(samples, sort_keys=True, indent=1) + "\n",
                                              encoding="utf-8")
    print(f"done statuses={manifest['statuses']} pages={manifest['pages']} rows={manifest['rows']} "
          f"http={accounting['http_requests']} store_digest={manifest['store_digest'][:12]}")


def _timezone_samples(client: sec_store.SecClient, root: Path, results, declaration,
                      *, count: int, window_start: date) -> list[dict]:
    """Accessions inside the study window, deliberately covering after-hours and DST boundaries.

    The zone of `acceptanceDateTime` is decided by comparing these against each filing's own
    ACCEPTANCE-DATETIME header (ET), so the sample has to come from the range the study uses.
    """
    taxonomy = Taxonomy(declaration.taxonomy, declaration.addendum)
    pool: list[tuple[str, str, str]] = []  # (cik, accession, acceptance)
    for result in results:
        if result.status != "OK":
            continue
        for row in _rows(root, result.cik):
            acceptance = str(row.get("acceptanceDateTime") or "")
            filing_date = str(row.get("filingDate") or "")
            if not acceptance or filing_date < window_start.isoformat():
                continue
            if not taxonomy.classify(str(row.get("form") or ""), row.get("items")).classes:
                continue
            pool.append((result.cik, str(row["accessionNumber"]), acceptance))
    if not pool:
        return []
    pool.sort(key=lambda item: item[2])
    # As filed, the string carries a `Z`. Whether that is true UTC or a mislabelled ET stamp is
    # exactly what the audit decides, so the groups below select on the raw hour, not on a zone.
    after_hours = [p for p in pool if p[2][11:13] >= "20" or p[2][11:13] <= "13"]
    dst = [p for p in pool if p[2][5:10] in {"03-06", "03-07", "03-08", "03-09", "03-10", "03-11",
                                             "11-01", "11-02", "11-03", "11-04"}]
    spread = [pool[i] for i in range(0, len(pool), max(1, len(pool) // max(1, count // 2)))]
    rng = random.Random(declaration.bootstrap[2])
    chosen: list[tuple[str, str, str]] = []
    for group in (after_hours[:], dst[:], spread, pool[:]):
        rng.shuffle(group)
        for item in group:
            if item not in chosen and len(chosen) < count:
                chosen.append(item)
    samples = []
    for cik, accession, acceptance in chosen:
        header = sec_store.fetch_acceptance_header(client, root, cik, accession)
        if header:
            samples.append({"cik": cik, "accession": accession, "acceptance": acceptance,
                            "header": header})
    return samples


def _rows(root: Path, cik: str) -> list[dict]:
    from app.backtest.strategy_c_e0.events import read_cik_rows

    return read_cik_rows(root, cik)


if __name__ == "__main__":
    main()
