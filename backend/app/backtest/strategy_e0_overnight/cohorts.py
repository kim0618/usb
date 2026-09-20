"""Building the two declared minute cohorts.

``M1_DEEP`` is the 30-symbol ``MINUTE_UNIVERSE_V1`` over the whole grid: long history, but a
mega-cap universe that answers a narrower question than the daily study. ``M2_BROAD`` is every
other symbol the V2 collector reached, over the recent scope window only.

The V2 broad collection was stopped part way through an alphabetically ordered queue, so M2
covers roughly A..NEWT and almost nothing after it. Alphabetical position is unrelated to
return, which makes M2 a truncated sample rather than a performance-selected one - but the
truncation is real, it is measured in ``cohort_report`` and it is stated in the report.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M

MINUTE_UNIVERSE_FILE = "market_data/metadata/historical_snapshot/USB-HIST-V1/minute_universe.json"
ROW_FIELDS = (list(M.CLOSING_FIELDS) + ["minute_overnight"]
              + [f"close_to_next_{k}" for k in ("1m", "5m", "15m")]
              + [f"next_{k}_mfe" for k in ("5m", "15m")]
              + [f"next_{k}_mae" for k in ("5m", "15m")] + ["next_bars_15m"])


def deep_symbols(workspace_root: Path) -> tuple[str, ...]:
    import json
    path = workspace_root / MINUTE_UNIVERSE_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("symbols", "tickers", "universe"):
        if key in payload:
            values = payload[key]
            if isinstance(values, dict):
                values = sorted(values)
            return tuple(sorted(str(v) for v in values))
    raise KeyError(f"{path} carries no symbol list")


def available_symbols(workspace_root: Path) -> tuple[str, ...]:
    root = workspace_root / M.RAW_DIR
    return tuple(sorted(d.name for d in root.iterdir() if d.is_dir()))


_CONTEXT: dict[str, Any] = {}


def _init(workspace_root: str, sessions: list[str]) -> None:
    _CONTEXT["root"] = Path(workspace_root)
    _CONTEXT["sessions"] = [date.fromisoformat(s) for s in sessions]
    _CONTEXT["edges"], _CONTEXT["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def _one(symbol: str) -> dict[str, Any] | None:
    tape = M.load_symbol_tape(symbol, _CONTEXT["root"], _CONTEXT["edges"], _CONTEXT["offsets"])
    if tape is None:
        return None
    features = M.session_features(tape)
    rows = M.pair_sessions(features, _CONTEXT["sessions"])
    if not rows:
        return None
    return {
        "symbol": symbol,
        "sessions": [r["session"].isoformat() for r in rows],
        "values": {f: [float(r.get(f, np.nan)) for r in rows] for f in ROW_FIELDS},
        "sources": dict(tape.sources),
        "overlap_sessions": tape.overlap_sessions,
        "tape_sessions": len(features),
    }


@dataclass
class Cohort:
    name: str
    symbols: np.ndarray
    sessions: np.ndarray
    values: dict[str, np.ndarray]
    report: dict[str, Any]

    def __len__(self) -> int:
        return int(self.symbols.size)


def build_cohort(name: str, symbols: Sequence[str], workspace_root: Path,
                 sessions: Sequence[date], *, workers: int = 8,
                 progress: Any = None) -> Cohort:
    out_symbols: list[str] = []
    out_sessions: list[str] = []
    values: dict[str, list[float]] = {f: [] for f in ROW_FIELDS}
    report: dict[str, Any] = {"requested": len(symbols), "with_rows": 0, "empty": 0,
                              "rows": 0, "overlap_sessions": 0,
                              "sources": {"raw_pages": 0, "raw_bars": 0,
                                          "legacy_files": 0, "legacy_bars": 0}}
    session_texts = [s.isoformat() for s in sessions]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(str(workspace_root), session_texts)) as pool:
        for i, result in enumerate(pool.map(_one, symbols, chunksize=4)):
            if progress is not None and i % 100 == 0:
                progress(f"{name} {i}/{len(symbols)} rows={report['rows']}")
            if result is None:
                report["empty"] += 1
                continue
            report["with_rows"] += 1
            report["rows"] += len(result["sessions"])
            report["overlap_sessions"] += result["overlap_sessions"]
            for key, count in result["sources"].items():
                report["sources"][key] += count
            out_symbols.extend([result["symbol"]] * len(result["sessions"]))
            out_sessions.extend(result["sessions"])
            for field, series in result["values"].items():
                values[field].extend(series)
    return Cohort(name=name, symbols=np.array(out_symbols, dtype=object),
                  sessions=np.array(out_sessions, dtype=object),
                  values={k: np.array(v, dtype=np.float64) for k, v in values.items()},
                  report=report)


def save_cohort(cohort: Cohort, path: Path) -> None:
    np.savez_compressed(path, symbols=cohort.symbols.astype("U12"),
                        sessions=cohort.sessions.astype("U10"), **cohort.values)


def load_cohort(path: Path) -> dict[str, np.ndarray]:
    payload = np.load(path, allow_pickle=False)
    return {k: payload[k] for k in payload.files}


def join_to_daily(cohort: Mapping[str, np.ndarray], sessions: Sequence[date],
                  tickers: Sequence[str], session_idx: np.ndarray, ticker_idx: np.ndarray,
                  ) -> dict[str, Any]:
    """Match each minute row to its row in the daily table, dropping what the universe rejected.

    A minute row with no daily row is not an error: the minute store holds symbols and sessions
    the declared PIT universe filters out (price, liquidity, membership, corporate action). The
    share that survives is reported so the cohort's coverage is visible.
    """
    session_text = np.array([s.isoformat() for s in sessions])
    ticker_text = np.array(list(tickers))
    daily_key = np.char.add(np.char.add(session_text[session_idx], "|"), ticker_text[ticker_idx])
    order = np.argsort(daily_key)
    sorted_key = daily_key[order]
    minute_key = np.char.add(np.char.add(cohort["sessions"].astype(str), "|"),
                             cohort["symbols"].astype(str))
    position = np.clip(np.searchsorted(sorted_key, minute_key), 0, sorted_key.size - 1)
    hit = sorted_key[position] == minute_key
    columns = {k: v[hit] for k, v in cohort.items() if k not in ("symbols", "sessions")}
    columns["daily_index"] = order[position][hit]
    return {"columns": columns, "minute_rows": int(minute_key.size),
            "joined_rows": int(hit.sum()),
            "joined_share": float(hit.mean()) if hit.size else 0.0,
            "symbols_joined": int(np.unique(cohort["symbols"][hit]).size)}


def cohort_digest(cohorts: Mapping[str, Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for name in sorted(cohorts):
        block = cohorts[name]
        digest.update(f"{name}\t{block.get('minute_rows')}\t{block.get('joined_rows')}\t"
                      f"{block.get('symbols_joined')}\n".encode())
    return digest.hexdigest()
