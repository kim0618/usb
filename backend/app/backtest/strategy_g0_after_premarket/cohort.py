"""Per symbol-day source and target blocks, built in parallel from the merged tape.

One row per (symbol, ET day on the tape). The source block of a day is what DAY T contributes
when that day is T; the target blocks are what it contributes when that day is T+1. Pairing is
done afterwards on the calendar pairs (``dataset``), never here, so this module has no notion of
"next day" and cannot join the wrong one.

Days carrying conflicting duplicate minutes are marked and later excluded; identical duplicates
are dropped to one copy (both counted in the coverage audit).
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_g0_after_premarket import features as F
from app.backtest.strategy_g0_after_premarket import tape as T

_CTX: dict[str, Any] = {}


def target_specs(rules_raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Entry name -> {entry_minute, entry_window, exits{tag: last bar minute}} from the rules."""
    out = {}
    for name, spec in rules_raw["label_sets"].items():
        out[name] = {"entry_minute": int(spec["entry_minute_et"]),
                     "entry_window": int(spec["entry_window_minutes"]),
                     "exits": {tag: int(m) for tag, m in spec["exit_last_bar_minute_et"].items()}}
    return out


def _init(drive_root: str, staging_root: str | None, specs: Mapping[str, Any],
          grid_days: Sequence[int], rvol_window: int, rvol_minimum: int) -> None:
    _CTX.update(drive=Path(drive_root), staging=Path(staging_root) if staging_root else None,
                specs=specs, grid=np.array(grid_days, dtype=np.int64),
                rvol_window=rvol_window, rvol_minimum=rvol_minimum)
    _CTX["edges"], _CTX["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def symbol_rows(tape: M.SymbolTape, specs: Mapping[str, Any], grid: np.ndarray,
                rvol_window: int, rvol_minimum: int,
                conflicting_days: frozenset[int] = frozenset()) -> dict[str, Any]:
    """Every block for one symbol; the after-hours RVOL denominator walks the grid, not the tape."""
    days = np.unique(tape.et_day)
    source: dict[str, list[float]] = {name: [] for name in F.SOURCE_FIELDS}
    target: dict[str, list[float]] = {}
    lows = np.searchsorted(tape.et_day, days, side="left")
    highs = np.searchsorted(tape.et_day, days, side="right")
    for lo, hi in zip(lows, highs):
        sel = slice(int(lo), int(hi))
        m = tape.minute[sel]
        block = F.source_block(m, tape.high[sel], tape.low[sel], tape.close[sel],
                               tape.volume[sel], tape.vwap[sel])
        for name in F.SOURCE_FIELDS:
            source[name].append(block[name])
        for set_name, spec in specs.items():
            tb = F.target_block(m, tape.open[sel], tape.high[sel], tape.low[sel], tape.close[sel],
                                tape.volume[sel], tape.vwap[sel], entry_minute=spec["entry_minute"],
                                entry_window=spec["entry_window"], exits=spec["exits"])
            for key, value in tb.items():
                target.setdefault(f"{set_name}:{key}", []).append(value)

    # after-hours dollar volume on the grid; grid sessions without a tape stay missing
    on_grid = np.isin(grid, days)
    grid_values = np.zeros(grid.size)
    position = np.searchsorted(days, grid[on_grid])
    grid_values[on_grid] = np.array(source["after_dollar_volume"])[position]
    denominators = F.prior_mean(grid_values, on_grid, rvol_window, rvol_minimum)
    by_day = dict(zip(grid.tolist(), denominators.tolist()))
    rvol_den = [by_day.get(int(d), float("nan")) for d in days]
    return {"days": days.tolist(), "source": source, "target": target,
            "after_rvol_denominator": rvol_den,
            "conflicting": [int(d) in conflicting_days for d in days]}


def _one(symbol: str) -> dict[str, Any] | None:
    merged = T.load(symbol, _CTX["drive"], _CTX["staging"], _CTX["edges"], _CTX["offsets"])
    if merged is None:
        return None
    dup = T.duplicate_days(merged.tape)
    conflicting = frozenset(d for d, identical in dup.items() if not identical)
    tape = T.drop_identical_duplicates(merged.tape)
    rows = symbol_rows(tape, _CTX["specs"], _CTX["grid"], _CTX["rvol_window"],
                       _CTX["rvol_minimum"], conflicting)
    rows["symbol"] = symbol
    rows["staging_days"] = sorted(int(d) for d in merged.staging_days)
    return rows


def pack(results: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    """Flatten per-symbol results (``symbol_rows`` plus ``symbol``/``staging_days``) to arrays."""
    out: dict[str, list] = {"symbols": [], "days": [], "conflicting": [], "staging": [],
                            "after_rvol_denominator": []}
    for result in results:
        n = len(result["days"])
        out["symbols"].extend([result["symbol"]] * n)
        out["days"].extend(result["days"])
        out["conflicting"].extend(result["conflicting"])
        staged = set(result.get("staging_days", ()))
        out["staging"].extend([d in staged for d in result["days"]])
        out["after_rvol_denominator"].extend(result["after_rvol_denominator"])
        for group in ("source", "target"):
            for key, values in result[group].items():
                out.setdefault(f"{group}|{key}", []).extend(values)
    arrays: dict[str, np.ndarray] = {
        "symbols": np.array(out.pop("symbols"), dtype="U12"),
        "days": np.array(out.pop("days"), dtype=np.int64),
        "conflicting": np.array(out.pop("conflicting"), dtype=bool),
        "staging": np.array(out.pop("staging"), dtype=bool),
    }
    arrays.update({k: np.array(v, dtype=np.float64) for k, v in out.items()})
    return arrays


def build(symbols: Sequence[str], drive_root: Path, staging_root: Path | None,
          specs: Mapping[str, Any], grid_days: Sequence[int], *, rvol_window: int,
          rvol_minimum: int, workers: int = 5, progress: Any = None) -> dict[str, np.ndarray]:
    results = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(str(drive_root), str(staging_root) if staging_root else None,
                                       dict(specs), list(grid_days), rvol_window, rvol_minimum)) as pool:
        for i, result in enumerate(pool.map(_one, symbols, chunksize=8)):
            if progress is not None and i % 400 == 0:
                progress(f"cohort {i}/{len(symbols)}")
            if result is not None:
                results.append(result)
    return pack(results)


def save(arrays: Mapping[str, np.ndarray], path: Path) -> None:
    np.savez_compressed(path, **{k.replace("|", "__").replace(":", "--"): v for k, v in arrays.items()})


def load(path: Path) -> dict[str, np.ndarray]:
    payload = np.load(path, allow_pickle=False)
    return {k.replace("__", "|").replace("--", ":"): payload[k] for k in payload.files}
