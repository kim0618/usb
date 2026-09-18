"""What already exists, per symbol and session, before any request is planned.

Existing coverage comes from two read-only sources: COMPLETE entries of the legacy collector
manifest (every session of a COMPLETE entry is present, by the collector's own contract) and
COMPLETE Common Raw ledgers. The manifest is opened ``mode=ro``; nothing here writes.
"""

from collections.abc import Sequence
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from app.backtest.historical_store.raw_fetch import DAILY_DIR, MINUTE_DIR, ledger_sessions, read_ledger

REPO_ROOT = Path(__file__).resolve().parents[4]
RESEARCH_UNIVERSE_V2 = REPO_ROOT / "docs/backtest/research_universe_v2.json"
MINUTE_UNIVERSE_ID = "MINUTE_UNIVERSE_V1"
LEGACY_KIND = {"minute": "minute_bars", "per_symbol_daily": "daily_bars"}
RAW_DIR = {"minute": MINUTE_DIR, "per_symbol_daily": DAILY_DIR}


def minute_universe_v1() -> dict:
    """A's research universe V2 (frozen, 29) plus its benchmark. B scope is estimated, not stored."""
    raw = RESEARCH_UNIVERSE_V2.read_bytes()
    spec = json.loads(raw)
    symbols = sorted(set(spec["symbols"]) | {spec["benchmark_symbol"]})
    return {"universe_id": MINUTE_UNIVERSE_ID, "symbols": symbols,
            "sources": {"research_universe_v2": {"path": "docs/backtest/research_universe_v2.json",
                                                 "sha256": hashlib.sha256(raw).hexdigest(),
                                                 "symbols": len(spec["symbols"])},
                        "benchmark": spec["benchmark_symbol"]}}


def legacy_entries(workspace_root: Path, kind: str) -> list[tuple[str, date, date, str, int]]:
    uri = f"file:{workspace_root / 'state/collector_manifest.sqlite3'}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "SELECT symbol, start_date, end_date, relative_path, row_count FROM collector_entries "
            "WHERE status = 'COMPLETE' AND provider = 'massive' AND data_kind = ?",
            (LEGACY_KIND[kind],)).fetchall()
    return [(s, date.fromisoformat(a), date.fromisoformat(b), p, n) for s, a, b, p, n in rows]


def existing_sessions(workspace_root: Path, kind: str, sessions: Sequence[date],
                      symbols: Sequence[str], *, raw_root: Path | None = None,
                      ) -> tuple[dict[str, set[date]], dict[str, set[date]]]:
    """(legacy, common) session sets per symbol, both restricted to ``sessions``. ``raw_root``
    reads the Common Raw ledgers from another root with the same layout (the local staging)."""
    legacy: dict[str, set[date]] = {s: set() for s in symbols}
    for symbol, start, end, _, _ in legacy_entries(workspace_root, kind):
        if symbol in legacy:
            legacy[symbol] |= {s for s in sessions if start <= s <= end}
    common: dict[str, set[date]] = {s: set() for s in symbols}
    for symbol in symbols:
        folder = (raw_root or workspace_root) / RAW_DIR[kind] / symbol
        for path in sorted(folder.glob("*.request.json")) if folder.is_dir() else ():
            ledger = read_ledger(path)
            if ledger is not None:
                common[symbol] |= ledger_sessions(ledger, sessions)
    return legacy, common
