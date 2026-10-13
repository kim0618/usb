"""Backfill the exchange a mover run's candidates were persisted without.

A-MOVER-LIVE-V1 runs written before ``strategy_a_mover_live.scanner.candidate_rows`` carried the
collector's listing have no ``exchange`` in ``score_components_json``, and the entry runtime
refuses such a candidate by name ("Exchange MISSING"). The 2026-10-12 session lost its six
approved candidates that way.

This tool fills ONLY that one key, ONLY on rows of ONE named run that lack it, and ONLY from the
durable listing the baseline cache already stores for the symbol
(``premarket_volume_sessions.exchange``, written from the same Kiwoom stkinfo listing the live
collector uses). A symbol with no stored listing, or with more than one distinct listing, is left
untouched and reported: nothing is guessed and nothing is defaulted.

Dry-run is the default and writes nothing. ``--apply`` updates the named run's rows and commits.
The run must be an ``a_mover_live_v1`` run. No order, strategy state or ledger row is touched.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.market  # noqa: F401  (package init order: kiwoom.mapping imports back into app.market)
from app.integrations.kiwoom.mapping import EXCHANGES
from app.models.analytics import PremarketVolumeSession
from app.models.scanner import ScannerCandidate, ScannerRun

MOVER_SCORE_VERSION = "a_mover_live_v1"


def plan(session: Session, run_id: int) -> list[dict[str, Any]]:
    """One record per candidate of the run: what would be written, or why nothing would be."""
    run = session.get(ScannerRun, run_id)
    if run is None:
        raise SystemExit(f"run {run_id} does not exist")
    if run.score_version != MOVER_SCORE_VERSION:
        raise SystemExit(f"run {run_id} is {run.score_version!r}, not {MOVER_SCORE_VERSION!r}")
    out: list[dict[str, Any]] = []
    candidates = session.scalars(select(ScannerCandidate).where(
        ScannerCandidate.scanner_run_id == run_id).order_by(ScannerCandidate.rank)).all()
    for candidate in candidates:
        components = candidate.score_components_json
        components = components if isinstance(components, dict) else {}
        record: dict[str, Any] = {"candidate_id": candidate.id, "symbol": candidate.symbol,
                                  "rank": candidate.rank, "action": "SKIP"}
        if str(components.get("exchange") or "").strip():
            record |= {"reason": "ALREADY_HAS_EXCHANGE", "exchange": components["exchange"]}
        else:
            listed = sorted({str(value) for value in session.scalars(
                select(PremarketVolumeSession.exchange).where(
                    PremarketVolumeSession.symbol == candidate.symbol).distinct())})
            known = [value for value in listed if value in EXCHANGES]
            if len(known) == 1 and len(listed) == 1:
                record |= {"action": "SET", "exchange": EXCHANGES[known[0]], "listing_code": known[0]}
            elif not listed:
                record |= {"reason": "NO_STORED_LISTING"}
            else:
                record |= {"reason": "AMBIGUOUS_OR_UNSUPPORTED_LISTING", "listed": listed}
        out.append(record)
    return out


def apply(session: Session, records: Sequence[dict[str, Any]]) -> int:
    """Write the SET records. Returns how many rows changed."""
    changed = 0
    for record in records:
        if record["action"] != "SET":
            continue
        candidate = session.get(ScannerCandidate, record["candidate_id"])
        components = dict(candidate.score_components_json or {})
        if str(components.get("exchange") or "").strip():
            continue                                    # never overwrite a stored venue
        components["exchange"] = record["exchange"]
        candidate.score_components_json = components   # reassigned, so the JSON column is flagged dirty
        changed += 1
    session.commit()
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    args = parser.parse_args(argv)
    from app.core.database import SessionLocal
    with SessionLocal() as session:
        records = plan(session, args.run_id)
        print(json.dumps({"run_id": args.run_id, "mode": "APPLY" if args.apply else "DRY_RUN",
                          "records": records}, ensure_ascii=False, indent=2))
        if args.apply:
            print(json.dumps({"updated_rows": apply(session, records)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
