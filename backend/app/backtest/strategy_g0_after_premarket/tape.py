"""The G0 minute tape: the common store plus the V2 local staging, merged per session.

Three physical forms carry the same authority (``MASSIVE_TICKER_AGGREGATE`` adjusted=false,
window 04:00-20:00 ET):

* the common raw pages on the Drive workspace (``market_data/raw/massive/minute``);
* the legacy normalized Parquet on the Drive workspace (30 symbols, read through E0's loader,
  which already drops raw rows of a session the legacy form also carries);
* the USB-HIST-V2 local staging (``data/runtime/common_hist/v2_staging``), 174 symbols with a
  two-year window, collected by the same collector before the scope was cut back.

The first two are read with E0's ``load_symbol_tape`` unchanged. Staging is added only for
sessions the Drive tape does not carry, only when every ledger in the symbol's staging folder is
``COMPLETE``, and every session both forms carry is compared and counted rather than merged.

Integrity is measured on the raw pages in file order before anything is sorted, so an
out-of-order or duplicated timestamp is reported where it occurs instead of being hidden by the
merge.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M

STAGING_REL = Path("data/runtime/common_hist/v2_staging")


@dataclass(frozen=True)
class MergedTape:
    tape: M.SymbolTape
    drive_days: frozenset[int]
    staging_days: frozenset[int]
    overlap_days: int
    overlap_disagreements: int


def ledgers_complete(folder: Path) -> bool:
    ledgers = list(folder.glob("*.request.json"))
    if not ledgers:
        return False
    for path in ledgers:
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("status") != "COMPLETE":
                return False
        except (OSError, ValueError):
            return False
    return True


def _day_signature(tape: M.SymbolTape, day: int) -> tuple[int, float]:
    sel = tape.et_day == day
    return int(sel.sum()), float(np.nansum(tape.volume[sel]))


def load(symbol: str, drive_root: Path, staging_root: Path | None, edges: np.ndarray,
         offsets: np.ndarray) -> MergedTape | None:
    drive = M.load_symbol_tape(symbol, drive_root, edges, offsets)
    staging = None
    if staging_root is not None:
        folder = staging_root / M.RAW_DIR / symbol
        if folder.is_dir() and ledgers_complete(folder):
            staging = M.load_symbol_tape(symbol, staging_root, edges, offsets, use_legacy=False)
    if drive is None and staging is None:
        return None
    drive_days = frozenset(np.unique(drive.et_day).tolist()) if drive is not None else frozenset()
    if staging is None:
        return MergedTape(drive, drive_days, frozenset(), 0, 0)
    staging_all = frozenset(np.unique(staging.et_day).tolist())
    overlap = drive_days & staging_all
    disagreements = 0
    for day in overlap:
        if _day_signature(drive, day) != _day_signature(staging, day):
            disagreements += 1
    add = np.isin(staging.et_day, list(staging_all - drive_days))
    if drive is None:
        merged = staging
    else:
        parts = {name: np.concatenate([getattr(drive, name), getattr(staging, name)[add]])
                 for name in ("et_day", "minute", "open", "high", "low", "close", "volume", "vwap")}
        order = np.lexsort((parts["minute"], parts["et_day"]))
        sources = dict(drive.sources)
        sources["staging_bars"] = int(add.sum())
        merged = M.SymbolTape(symbol=symbol, sources=sources,
                              overlap_sessions=drive.overlap_sessions,
                              **{k: v[order] for k, v in parts.items()})
    return MergedTape(merged, drive_days, staging_all - drive_days, len(overlap), disagreements)


def raw_page_integrity(symbol: str, root: Path) -> dict[str, int]:
    """Timestamp and OHLCV checks on the provider pages as stored, in file order."""
    out = {"pages": 0, "bars": 0, "out_of_order": 0, "duplicate_t_within_page": 0,
           "duplicate_t_across_pages": 0, "missing_t": 0}
    folder = root / M.RAW_DIR / symbol
    if not folder.is_dir():
        return out
    seen: set[int] = set()
    for path in sorted(folder.iterdir()):
        if not M.PAGE_RE.match(path.name):
            continue
        payload = json.loads(gzip.decompress(path.read_bytes()))
        stamps = [bar.get("t") for bar in payload.get("results") or ()]
        out["pages"] += 1
        out["bars"] += len(stamps)
        valid = np.array([t for t in stamps if isinstance(t, int)], dtype=np.int64)
        out["missing_t"] += len(stamps) - int(valid.size)
        if valid.size > 1:
            out["out_of_order"] += int((np.diff(valid) < 0).sum())
        unique = np.unique(valid)
        out["duplicate_t_within_page"] += int(valid.size - unique.size)
        out["duplicate_t_across_pages"] += int(sum(1 for t in unique.tolist() if t in seen))
        seen.update(unique.tolist())
    return out


def tape_integrity(tape: M.SymbolTape) -> dict[str, int]:
    """Checks on the merged tape: duplicate minutes, OHLC validity, signs, zero volume."""
    o, h, l, c, v = tape.open, tape.high, tape.low, tape.close, tape.volume
    key = tape.et_day.astype(np.int64) * 2000 + tape.minute.astype(np.int64)
    dup = int(key.size - np.unique(key).size)
    finite = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c)
    with np.errstate(invalid="ignore"):
        bad_ohlc = finite & ((l > np.minimum(o, c) + 1e-9) | (h < np.maximum(o, c) - 1e-9) | (l > h))
        negative_price = finite & ((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0))
    return {
        "bars": int(key.size),
        "duplicate_day_minute": dup,
        "non_finite_ohlc": int((~finite).sum()),
        "invalid_ohlc": int(bad_ohlc.sum()),
        "non_positive_price": int(negative_price.sum()),
        "negative_volume": int((np.isfinite(v) & (v < 0)).sum()),
        "zero_volume": int((np.isfinite(v) & (v == 0)).sum()),
        "missing_volume": int((~np.isfinite(v)).sum()),
        "outside_0400_2000": int(((tape.minute < 240) | (tape.minute >= 1200)).sum()),
    }


def duplicate_days(tape: M.SymbolTape) -> dict[int, bool]:
    """ET days carrying a duplicated minute, mapped to whether every duplicate is identical."""
    key = tape.et_day.astype(np.int64) * 2000 + tape.minute.astype(np.int64)
    order = np.argsort(key, kind="stable")
    k = key[order]
    same_as_prev = np.flatnonzero(k[1:] == k[:-1]) + 1
    out: dict[int, bool] = {}
    fields = (tape.open, tape.high, tape.low, tape.close, tape.volume)
    for position in same_as_prev:
        a, b = order[position - 1], order[position]
        identical = all((f[a] == f[b]) or (not np.isfinite(f[a]) and not np.isfinite(f[b]))
                        for f in fields)
        day = int(tape.et_day[b])
        out[day] = out.get(day, True) and identical
    return out


def drop_identical_duplicates(tape: M.SymbolTape) -> M.SymbolTape:
    """Keep the first bar of each (day, minute). Callers exclude days whose copies differ."""
    key = tape.et_day.astype(np.int64) * 2000 + tape.minute.astype(np.int64)
    _, first = np.unique(key, return_index=True)
    if first.size == key.size:
        return tape
    keep = np.sort(first)
    return M.SymbolTape(symbol=tape.symbol, sources=tape.sources,
                        overlap_sessions=tape.overlap_sessions,
                        **{name: getattr(tape, name)[keep] for name in
                           ("et_day", "minute", "open", "high", "low", "close", "volume", "vwap")})


def summarise_integrity(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for row in rows:
        for key, value in row.items():
            total[key] = total.get(key, 0) + int(value)
    return total
