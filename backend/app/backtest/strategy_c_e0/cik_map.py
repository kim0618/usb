"""Candidate ticker-date -> CIK, from the same PIT reference snapshots V1 used for eligibility.

`c_e0_rules_v1.json > cik_mapping`: the CIK of a candidate at signal date D is the `cik` field of
the CS snapshot with the latest `as_of <= D`. Ticker text is never the join key, a missing CIK is
`UNKNOWN_MAPPING` (never NO_EVENT), and if the snapshot used and the next one disagree on the
ticker's CIK, every date between the two `as_of` values is `UNKNOWN_MAPPING` too.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import gzip
import json
from pathlib import Path

UNKNOWN_MAPPING = "UNKNOWN_MAPPING"


@dataclass(frozen=True)
class SnapshotMap:
    as_of: date
    cik_by_ticker: dict[str, str]


def load_snapshot_maps(raw_root: Path) -> tuple[SnapshotMap, ...]:
    """Every CS reference snapshot in the C raw cache, ascending by `as_of`."""
    maps = []
    for path in sorted((raw_root / "tickers").glob("CS_*.json.gz")):
        payload = json.loads(gzip.decompress(path.read_bytes()))
        by_ticker: dict[str, str] = {}
        for row in payload["results"]:
            ticker, cik = row.get("ticker"), (row.get("cik") or "").strip()
            if ticker and cik:
                by_ticker[str(ticker)] = f"{int(cik):010d}"
        maps.append(SnapshotMap(date.fromisoformat(payload["as_of"]), by_ticker))
    return tuple(sorted(maps, key=lambda m: m.as_of))


@dataclass(frozen=True)
class Mapping:
    cik: str | None
    as_of: date | None
    reason: str | None = None


def map_candidate(maps: Sequence[SnapshotMap], ticker: str, signal_date: date) -> Mapping:
    used = [m for m in maps if m.as_of <= signal_date]
    if not used:
        return Mapping(None, None, "NO_SNAPSHOT_AT_OR_BEFORE_D")
    current = used[-1]
    cik = current.cik_by_ticker.get(ticker)
    if cik is None:
        return Mapping(None, current.as_of, "NO_CIK_IN_SNAPSHOT")
    following = [m for m in maps if m.as_of > signal_date]
    if following:
        nxt = following[0].cik_by_ticker.get(ticker)
        if nxt is not None and nxt != cik:
            return Mapping(None, current.as_of, "CIK_CHANGED_ACROSS_D")
    return Mapping(cik, current.as_of)
