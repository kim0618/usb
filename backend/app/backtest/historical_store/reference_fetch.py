"""Daily CS reference snapshots (``/v3/reference/tickers?type=CS&market=stocks&active=true&date=``).

Strategy B's scope S(D) reads the reference as of D-1 (STRATEGY_B_V1_SPEC section 3.2). One
snapshot per session date is stored under
``market_data/raw/massive/reference_tickers/daily/<YYYY>/CS_<date>.pNN.json.gz``: the exact
response body of every page (gzip, mtime 0) and a ``CS_<date>.request.json`` ledger written last.
Missing ledger = not fetched. Unlike aggregates, the reference endpoint is not limited to the
rolling 2-year window of Stocks Basic (a 2023 date answered 200 on 2026-09-18), so these files
never become unavailable and are fetched after every windowed kind.

The eight quarterly C-seed snapshots (``reference_tickers/CS_<date>.json.gz``, wrapped, selected
fields) stay as they are and are not fetched again; for those dates the daily layer defers to them.
"""

from collections.abc import Callable
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from app.backtest.historical_store.raw_fetch import (
    RAW_FORMAT_VERSION, _atomic_bytes, _with_cooldown, gzip_bytes, read_ledger,
)
from app.backtest.workspace.guards import assert_no_secret_like
from app.integrations.massive.client import MassiveAggregatesClient, MassiveError

DAILY_REFERENCE_DIR = "market_data/raw/massive/reference_tickers/daily"
QUARTERLY_REFERENCE_DIR = "market_data/raw/massive/reference_tickers"
MAX_PAGES = 12


def ledger_path(root: Path, as_of: date) -> Path:
    return root / DAILY_REFERENCE_DIR / str(as_of.year) / f"CS_{as_of.isoformat()}.request.json"


def existing_dates(root: Path, dates: list[date]) -> set[date]:
    """Dates answered by a COMPLETE daily ledger or held by a quarterly C-seed snapshot."""
    have = set()
    for as_of in dates:
        ledger = read_ledger(ledger_path(root, as_of))
        if (ledger is not None and ledger.get("status") == "COMPLETE") or \
                (root / QUARTERLY_REFERENCE_DIR / f"CS_{as_of.isoformat()}.json.gz").is_file():
            have.add(as_of)
    return have


def fetch_reference_day(client: MassiveAggregatesClient, capture: list[bytes], root: Path, as_of: date, *,
                        secret: str, log: Callable[[str], None], sleeper: Callable[[float], None],
                        now: Callable[[], str]) -> str:
    path = ledger_path(root, as_of)
    if path.is_file():
        return "cached"
    before = client.accounting.http_requests

    def call() -> None:
        capture.clear()
        client.reference_tickers(as_of, security_type="CS", max_pages=MAX_PAGES)

    _with_cooldown(call, log=log, sleeper=sleeper)
    pages, rows, tickers = [], 0, set()
    for number, raw in enumerate(capture, start=1):
        if secret and secret.encode() in raw:
            raise MassiveError("SECRET_IN_BODY", "a response body contained the API key")
        body = json.loads(raw)
        results = body.get("results") or []
        for row in results:
            assert_no_secret_like(row, where=f"reference {as_of}")
        rows += len(results)
        tickers.update(r.get("ticker") for r in results)
        packed = gzip_bytes(raw)
        page = path.with_name(f"CS_{as_of.isoformat()}.p{number:02d}.json.gz")
        _atomic_bytes(page, packed)
        pages.append({"file": page.name, "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": len(raw),
                      "file_sha256": hashlib.sha256(packed).hexdigest(), "file_bytes": len(packed),
                      "results": len(results)})
    if rows != len(tickers):
        raise MassiveError("MALFORMED_PAYLOAD", f"reference {as_of} repeats a ticker across pages")
    ledger: dict[str, Any] = {
        "format": RAW_FORMAT_VERSION, "provider": "massive", "kind": "reference_tickers_daily",
        "as_of": as_of.isoformat(), "params": {"type": "CS", "market": "stocks", "active": "true",
                                               "sort": "ticker", "order": "asc"},
        "status": "COMPLETE", "pages": pages, "rows": rows,
        "http_requests": client.accounting.http_requests - before, "collected_at": now()}
    _atomic_bytes(path, (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode())
    return f"ok pages={len(pages)} rows={rows}"
