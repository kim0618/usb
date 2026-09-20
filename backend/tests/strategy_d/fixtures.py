"""A synthetic frozen dataset in the common-store layout. No network, no Drive, no real prices.

The sessions are real XNYS sessions because the grid contract checks them against the calendar;
everything else (prices, tickers, splits) is made up so a test can plant exactly one defect.
"""

from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.market.calendar import MarketCalendar

FORMAT = "strategy-c-selection-raw-v1"
SEEDED_FROM = "data/runtime/strategy_c/raw (test fixture)"
SNAPSHOT_ID = "TEST-SNAP-V1"
#: Ten quiet names plus one per universe reason, so every exclusion has a witness.
QUIET = tuple(f"Q{i:02d}" for i in range(10))
SPECIAL = ("LOWPRICE", "THIN", "SPLITTER", "CAJUMP", "GAPPY", "LATECO")
TICKERS = QUIET + SPECIAL


def sessions(count: int, start: date = date(2024, 9, 17)) -> tuple[date, ...]:
    calendar = MarketCalendar()
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if calendar.is_trading_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


def _prices(days: int, seed: int = 11) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = {t: 50.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, days))) for t in TICKERS}
    close["LOWPRICE"] = np.full(days, 2.0)
    close["CAJUMP"] = close["CAJUMP"].copy()
    close["CAJUMP"][days // 2:] *= 5.0  # a 5x step: CA suspect while it sits inside a window
    return close


def build(root: Path, *, day_count: int = 290, duplicate_on: date | None = None,
          break_session_label: date | None = None, corrupt_sha_on: date | None = None,
          drop_session: int | None = None, unusable_index: int | None = None) -> dict[str, Any]:
    """Write a complete frozen dataset and return its freeze document."""
    grid = list(sessions(day_count))
    if drop_session is not None:
        del grid[drop_session]
    close = _prices(len(grid))
    volume = {t: np.full(len(grid), 400_000.0) for t in TICKERS}
    volume["THIN"] = np.full(len(grid), 100.0)
    gap_index = len(grid) // 2 + 3
    split_day = grid[len(grid) // 2]

    files: list[dict[str, Any]] = []

    def write(relative: str, common: str, payload: dict[str, Any], *, corrupt: bool = False) -> dict[str, Any]:
        path = root / common
        path.parent.mkdir(parents=True, exist_ok=True)
        body = gzip.compress(json.dumps(payload, sort_keys=True).encode("utf-8"), mtime=0)
        path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        return {"relative_path": relative, "common_path": common, "size": len(body),
                "sha256": "0" * 64 if corrupt else digest, "source": "massive",
                "seeded_from": SEEDED_FROM, "payload_form": "wrapped-json-sort-keys",
                "format": FORMAT}

    for i, session in enumerate(grid):
        rows = []
        for ticker in TICKERS:
            if ticker == "GAPPY" and i == gap_index:
                continue  # one missing bar: NO_HISTORY for every window that spans it
            if ticker == "LATECO" and i < len(grid) // 3:
                continue
            price = float(close[ticker][i])
            rows.append({"T": ticker, "o": price * 0.99, "h": price * 1.02, "l": price * 0.98,
                         "c": price, "v": float(volume[ticker][i])})
        if duplicate_on == session:
            rows.append(dict(rows[0]))
        label = session.isoformat()
        if break_session_label == session:
            label = (session + timedelta(days=1)).isoformat()
        status = "OK"
        payload = {"format": FORMAT, "session": label,
                   "body": {"adjusted": False, "queryCount": len(rows), "resultsCount": len(rows),
                            "results": rows}}
        if unusable_index == i:
            payload = {"format": FORMAT, "session": label, "error": "NOT_AUTHORIZED"}
            status = "NOT_AVAILABLE:NOT_AUTHORIZED"
        row = write(f"grouped/{session.isoformat()}.json.gz",
                    f"market_data/raw/massive/grouped_daily/{session.year}/{session.isoformat()}.json.gz",
                    payload, corrupt=corrupt_sha_on == session)
        row.update(file_type="GROUPED_DAILY", session_date=session.isoformat(),
                   authority="MASSIVE_GROUPED_DAILY", adjusted=None if status != "OK" else False,
                   rows=None if status != "OK" else len(rows), status=status)
        files.append(row)

    snapshot_dates = (grid[0], grid[len(grid) // 3])
    for as_of in snapshot_dates:
        names = [t for t in TICKERS if t != "LATECO" or as_of != grid[0]]
        results = [{"ticker": t, "type": "CS", "market": "stocks", "locale": "us",
                    "primary_exchange": "XNYS", "active": True, "cik": None,
                    "composite_figi": None if t == "THIN" else f"BBG{abs(hash(t)) % 10**9:09d}"}
                   for t in names]
        results.append({"ticker": "DEADCO", "type": "CS", "market": "stocks", "locale": "us",
                        "primary_exchange": "XNYS", "active": False, "cik": None,
                        "composite_figi": "BBG000000001"})
        results.append({"ticker": "SOMEETF", "type": "ETF", "market": "stocks", "locale": "us",
                        "primary_exchange": "XNYS", "active": True, "cik": None,
                        "composite_figi": "BBG000000002"})
        payload = {"format": FORMAT, "as_of": as_of.isoformat(), "security_type": "CS",
                   "pages": 1, "results": results}
        row = write(f"tickers/CS_{as_of.isoformat()}.json.gz",
                    f"market_data/raw/massive/reference_tickers/CS_{as_of.isoformat()}.json.gz", payload)
        row.update(file_type="REFERENCE_TICKERS_CS", as_of=as_of.isoformat(), adjusted=None,
                   rows=len(results), pages=1, status="OK")
        files.append(row)

    split_payload = {"format": FORMAT, "start": grid[0].isoformat(), "end": grid[-1].isoformat(),
                     "pages": 1, "results": [{"ticker": "SPLITTER", "execution_date": split_day.isoformat(),
                                              "split_from": 1, "split_to": 2}]}
    row = write(f"splits/splits_{grid[0].isoformat()}_{grid[-1].isoformat()}.json.gz",
                f"market_data/raw/massive/splits/splits_{grid[0].isoformat()}_{grid[-1].isoformat()}.json.gz",
                split_payload)
    row.update(file_type="SPLITS", as_of=f"{grid[0].isoformat()}..{grid[-1].isoformat()}",
               adjusted=None, rows=1, pages=1, status="OK")
    files.append(row)

    usable = sorted(r["session_date"] for r in files
                    if r["file_type"] == "GROUPED_DAILY" and r["status"] == "OK")
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda r: r["relative_path"]):
        digest.update(f"{entry['relative_path']}\t{entry['sha256']}\t{entry['size']}\n".encode())
    freeze = {"freeze_id": "STRATEGY_C_RAW_FREEZE_V1", "created_at": "2026-09-18T00:00:00+00:00",
              "raw_root": "data/runtime/strategy_c/raw", "provider": "massive",
              "price_adjustment": "adjusted=false", "file_count": len(files),
              "total_bytes": sum(r["size"] for r in files),
              "grouped_files": sum(r["file_type"] == "GROUPED_DAILY" for r in files),
              "grouped_usable_sessions": len(usable), "usable_range": [usable[0], usable[-1]],
              "not_available": [r["relative_path"] for r in files if r["status"] != "OK"],
              "c_raw_digest": "f" * 64, "freeze_digest": digest.hexdigest(), "files": files}
    meta = root / "market_data/metadata/historical_snapshot" / SNAPSHOT_ID
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "c_raw_freeze.json").write_text(json.dumps(freeze, indent=1, sort_keys=True), encoding="utf-8")
    snapshot = {"snapshot_id": SNAPSHOT_ID, "status": "FROZEN", "provider": "massive",
                "start_date": grid[0].isoformat(), "end_date": grid[-1].isoformat(),
                "sessions": len(grid), "grouped_sessions": len(usable),
                "c_raw_freeze": {"freeze_id": freeze["freeze_id"], "freeze_digest": freeze["freeze_digest"],
                                 "c_raw_digest": freeze["c_raw_digest"]}}
    (meta / "snapshot.json").write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
    return freeze
