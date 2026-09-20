"""Reading a forward session's minute bars into the shape E1's feature code already speaks.

The point of this module is that it is thin. Forward pages are the same provider bytes in the
same format as the historical ones, so they are turned into the same ``SymbolTape`` E1 built its
features from, and every premarket feature is then produced by importing E1 unchanged. No feature
is re-implemented for the forward path, which is what keeps the forward test a test of H5 rather
than of a second codebase that resembles it.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_e0_overnight.minute import SymbolTape, et_offsets, to_et_fields
from app.backtest.strategy_e1_forward.layout import ForwardViolation, minute_path

#: The window a forward fetch must cover for H5: the premarket the features read, plus the
#: opening minutes the primary label reads.
REQUIRED_ET_WINDOW = ("04:00", "09:35")


def offsets_for(session: date):
    start = datetime(session.year, 1, 1, tzinfo=timezone.utc)
    end = datetime(session.year + 1, 1, 1, tzinfo=timezone.utc)
    return et_offsets(start, end)


def load(workspace_root: Path, symbol: str, session: date, edges, offs) -> SymbolTape | None:
    """One symbol, one forward session. Returns ``None`` when the page is absent."""
    path = minute_path(workspace_root, symbol, session)
    if not path.exists():
        return None
    payload = json.loads(gzip.decompress(path.read_bytes()))
    results = payload.get("results") or ()
    if not results:
        return None
    count = len(results)
    stamps = np.zeros(count, dtype=np.int64)
    columns = {k: np.full(count, np.nan) for k in ("o", "h", "l", "c", "v", "vw")}
    keep = np.zeros(count, dtype=bool)
    for i, bar in enumerate(results):
        moment = bar.get("t")
        if not isinstance(moment, int):
            continue
        stamps[i] = moment
        keep[i] = True
        for key in columns:
            value = bar.get(key)
            if value is not None:
                columns[key][i] = float(value)
    day, minute = to_et_fields(stamps[keep], edges, offs)
    order = np.lexsort((minute, day))
    return SymbolTape(symbol=symbol, et_day=day[order], minute=minute[order],
                      open=columns["o"][keep][order], high=columns["h"][keep][order],
                      low=columns["l"][keep][order], close=columns["c"][keep][order],
                      volume=columns["v"][keep][order], vwap=columns["vw"][keep][order],
                      sources={"forward_pages": 1, "forward_bars": int(keep.sum())},
                      overlap_sessions=0)


def tape_digest(workspace_root: Path, symbols: Sequence[str], session: date) -> tuple[str, int]:
    """sha256 over the forward minute files of one session, so a seal can name its bytes."""
    digest = hashlib.sha256()
    files = 0
    for symbol in sorted(symbols):
        path = minute_path(workspace_root, symbol, session)
        if not path.exists():
            continue
        digest.update(f"{symbol}\t{path.name}\t{path.stat().st_size}\n".encode())
        files += 1
    return digest.hexdigest(), files


def available_symbols(workspace_root: Path, session: date) -> tuple[str, ...]:
    folder = workspace_root / "market_data/forward/massive/minute"
    if not folder.is_dir():
        return ()
    out = []
    for symbol_dir in folder.iterdir():
        if symbol_dir.is_dir() and minute_path(workspace_root, symbol_dir.name, session).exists():
            out.append(symbol_dir.name)
    return tuple(sorted(out))


def require_session_data(workspace_root: Path, session: date) -> tuple[str, ...]:
    symbols = available_symbols(workspace_root, session)
    if not symbols:
        raise ForwardViolation(
            f"no forward minute data for {session.isoformat()} under "
            f"{workspace_root / 'market_data/forward/massive/minute'}. The forward tape for this "
            "session has not been collected; see the protocol document for the fetch plan.")
    return symbols
