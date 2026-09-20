"""Building the E1 premarket row table from the minute tape, in parallel.

The B-minute collector is running while E1 reads, so the tape is a moving target. E1 takes no
lock and writes nothing into the minute directories. Instead it binds itself to the symbol and
file list observed when the run starts (``tape_digest``) and recomputes that digest when the run
ends: a tape that grew under the study is reported, never silently averaged in.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e1_premarket import premarket as P

VALUE_FIELDS = (list(P.PREMARKET_FIELDS)
                + ["pm_rvol", "distance_to_premarket_high", "position_in_premarket_range",
                   "premarket_volume_last30m_ratio", f"open_{P.OPEN_MIN}", "open_bars_15m"]
                + [name for h in P.HORIZONS
                   for name in (f"R_{h}", f"R_{h}_strict", f"MFE_{h}", f"MAE_{h}")])

_CONTEXT: dict[str, Any] = {}


def tape_digest(workspace_root: Path, symbols: Sequence[str]) -> tuple[str, int]:
    """sha256 over every minute file's name and size, for the named symbols."""
    digest = hashlib.sha256()
    files = 0
    for symbol in sorted(symbols):
        folder = workspace_root / M.RAW_DIR / symbol
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.name.endswith(".json.gz"):
                continue
            digest.update(f"{symbol}/{path.name}\t{path.stat().st_size}\n".encode())
            files += 1
    return digest.hexdigest(), files


def available_symbols(workspace_root: Path) -> tuple[str, ...]:
    return tuple(sorted(d.name for d in (workspace_root / M.RAW_DIR).iterdir() if d.is_dir()))


def _init(workspace_root: str, decision_last: int) -> None:
    _CONTEXT["root"] = Path(workspace_root)
    _CONTEXT["decision_last"] = decision_last
    _CONTEXT["edges"], _CONTEXT["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def _one(symbol: str) -> dict[str, Any] | None:
    tape = M.load_symbol_tape(symbol, _CONTEXT["root"], _CONTEXT["edges"], _CONTEXT["offsets"])
    if tape is None:
        return None
    rows = P.session_rows(tape, decision_last=_CONTEXT["decision_last"])
    if not rows:
        return None
    sessions = sorted(rows)
    values: dict[str, list[float]] = {name: [] for name in VALUE_FIELDS}
    for key in sessions:
        row = rows[key]
        merged = dict(row.premarket)
        merged["pm_rvol"] = row.pm_rvol
        merged.update(P.derived_features(row.premarket))
        merged[f"open_{P.OPEN_MIN}"] = row.opening[f"open_{P.OPEN_MIN}"]
        merged["open_bars_15m"] = row.opening["open_bars_15m"]
        merged.update(P.labels(row.opening))
        missing = [name for name in VALUE_FIELDS if name not in merged]
        if missing:
            raise KeyError(f"{symbol}: declared columns are not produced: {missing}")
        for name in VALUE_FIELDS:
            values[name].append(float(merged[name]))
    return {"symbol": symbol,
            "sessions": [rows[k].session.isoformat() for k in sessions],
            "values": values}


@dataclass
class Cohort:
    symbols: np.ndarray
    sessions: np.ndarray
    values: dict[str, np.ndarray]
    report: dict[str, Any]


def build(symbols: Sequence[str], workspace_root: Path, *, decision_last: int,
          workers: int = 10, progress: Any = None) -> Cohort:
    out_symbols: list[str] = []
    out_sessions: list[str] = []
    values: dict[str, list[float]] = {name: [] for name in VALUE_FIELDS}
    report = {"requested": len(symbols), "with_rows": 0, "empty": 0, "rows": 0,
              "decision_last_bar_et_minute": decision_last}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(str(workspace_root), decision_last)) as pool:
        for i, result in enumerate(pool.map(_one, symbols, chunksize=4)):
            if progress is not None and i % 300 == 0:
                progress(f"premarket {i}/{len(symbols)} rows={report['rows']}")
            if result is None:
                report["empty"] += 1
                continue
            report["with_rows"] += 1
            report["rows"] += len(result["sessions"])
            out_symbols.extend([result["symbol"]] * len(result["sessions"]))
            out_sessions.extend(result["sessions"])
            for name, series in result["values"].items():
                values[name].extend(series)
    return Cohort(np.array(out_symbols, dtype=object), np.array(out_sessions, dtype=object),
                  {k: np.array(v, dtype=np.float64) for k, v in values.items()}, report)


def save(cohort: Cohort, path: Path) -> None:
    np.savez_compressed(path, symbols=cohort.symbols.astype("U12"),
                        sessions=cohort.sessions.astype("U10"), **cohort.values)


def load(path: Path) -> dict[str, np.ndarray]:
    payload = np.load(path, allow_pickle=False)
    return {k: payload[k] for k in payload.files}
