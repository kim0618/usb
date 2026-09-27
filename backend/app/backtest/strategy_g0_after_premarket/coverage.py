"""Returns-free coverage of DAY T after-hours and DAY T+1 premarket.

Everything here is a count, a bar-presence flag or a traded-value level. No price is divided by
another price, so nothing in this module can show an outcome; it exists so that the entry time,
the exit time and the activity thresholds can be frozen before any return is computed.

Minute-of-day windows (ET, bar start, a bar labelled t covers [t, t+1)):

* after-hours of T: [16:00, 19:59] as stored; the 16:00 bar carries the closing-cross prints
  (AAPL median 16:00 volume 672k against 5k at 16:01), so the ``_ex1600`` counts start at 16:01
* premarket of T+1: [04:00, 09:29]; the regular session starts at 09:30
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_g0_after_premarket import tape as T

AFTER_START, AFTER_END = 16 * 60, 19 * 60 + 59
PRE_START, PRE_END = 4 * 60, 9 * 60 + 29
REGULAR_START, REGULAR_END = 9 * 60 + 30, 15 * 60 + 59
ANCHORS = {"0400": 4 * 60, "0700": 7 * 60, "0800": 8 * 60}
EXIT_LAST_BAR = {"0900": 8 * 60 + 59, "0915": 9 * 60 + 14, "0925": 9 * 60 + 24}

DAY_FIELDS = (
    ["after_bars", "after_dollar", "after_bars_ex1600", "after_dollar_ex1600", "bar_1600_dollar",
     "after_bars_last30", "regular_bars", "pre_bars",
     "pre_dollar", "duplicate_state"]
    + [f"{kind}_{tag}" for tag in ANCHORS for kind in
       ("exact", "within5", "within15", "prior", "first_bar_dollar", "first_bar_volume",
        "prior30_bars")]
    + [f"exit_{tag}_after_0800" for tag in EXIT_LAST_BAR]
    + ["exit_0924_exact"]
)

_CTX: dict[str, Any] = {}


def _init(drive_root: str, staging_root: str | None) -> None:
    _CTX["drive"] = Path(drive_root)
    _CTX["staging"] = Path(staging_root) if staging_root else None
    _CTX["edges"], _CTX["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def day_block(minute: np.ndarray, volume: np.ndarray, price: np.ndarray) -> dict[str, float]:
    """Counts and traded value for one symbol-day; ``price`` is only used as vwap for value."""
    dollar = np.where(np.isfinite(volume), volume, 0.0) * np.where(np.isfinite(price), price, 0.0)
    after = (minute >= AFTER_START) & (minute <= AFTER_END)
    pre = (minute >= PRE_START) & (minute <= PRE_END)
    out: dict[str, float] = {
        "after_bars": float(after.sum()),
        "after_dollar": float(dollar[after].sum()),
        "after_bars_ex1600": float((after & (minute >= AFTER_START + 1)).sum()),
        "after_dollar_ex1600": float(dollar[after & (minute >= AFTER_START + 1)].sum()),
        "bar_1600_dollar": float(dollar[minute == AFTER_START].sum()),
        "after_bars_last30": float(((minute >= 19 * 60 + 30) & (minute <= AFTER_END)).sum()),
        "regular_bars": float(((minute >= REGULAR_START) & (minute <= REGULAR_END)).sum()),
        "pre_bars": float(pre.sum()),
        "pre_dollar": float(dollar[pre].sum()),
    }
    for tag, anchor in ANCHORS.items():
        out[f"exact_{tag}"] = float((minute == anchor).any())
        window5 = np.flatnonzero((minute >= anchor) & (minute <= anchor + 4))
        window15 = np.flatnonzero((minute >= anchor) & (minute <= anchor + 14))
        out[f"within5_{tag}"] = float(window5.size > 0)
        out[f"within15_{tag}"] = float(window15.size > 0)
        out[f"prior_{tag}"] = float(((minute >= PRE_START) & (minute < anchor)).any())
        if window15.size:
            out[f"first_bar_dollar_{tag}"] = float(dollar[window15[0]])
            out[f"first_bar_volume_{tag}"] = float(volume[window15[0]]) if np.isfinite(volume[window15[0]]) else 0.0
        else:
            out[f"first_bar_dollar_{tag}"] = float("nan")
            out[f"first_bar_volume_{tag}"] = float("nan")
        out[f"prior30_bars_{tag}"] = float(((minute >= anchor - 30) & (minute < anchor)
                                            & (minute >= PRE_START)).sum())
    for tag, last in EXIT_LAST_BAR.items():
        out[f"exit_{tag}_after_0800"] = float(((minute >= ANCHORS["0800"]) & (minute <= last)).any())
    out["exit_0924_exact"] = float((minute == EXIT_LAST_BAR["0925"]).any())
    return out


def _one(symbol: str) -> dict[str, Any] | None:
    merged = T.load(symbol, _CTX["drive"], _CTX["staging"], _CTX["edges"], _CTX["offsets"])
    raw_integrity = T.raw_page_integrity(symbol, _CTX["drive"])
    if _CTX["staging"] is not None:
        staged = T.raw_page_integrity(symbol, _CTX["staging"])
        raw_integrity = {k: raw_integrity[k] + staged[k] for k in raw_integrity}
    if merged is None:
        return {"symbol": symbol, "days": [], "values": {}, "integrity": {},
                "raw_integrity": raw_integrity, "sources": {}}
    tape = merged.tape
    integrity = T.tape_integrity(tape)
    dup_days = T.duplicate_days(tape)
    clean = T.drop_identical_duplicates(tape)
    price = np.where(np.isfinite(clean.vwap), clean.vwap, clean.close)
    days = np.unique(clean.et_day)
    values: dict[str, list[float]] = {name: [] for name in DAY_FIELDS}
    for day in days:
        sel = clean.et_day == day
        block = day_block(clean.minute[sel], clean.volume[sel], price[sel])
        # 0 = clean, 1 = identical duplicates dropped, 2 = conflicting duplicates (excluded later)
        state = dup_days.get(int(day))
        block["duplicate_state"] = 0.0 if state is None else (1.0 if state else 2.0)
        for name in DAY_FIELDS:
            values[name].append(block[name])
    source_of = ["staging" if int(d) in merged.staging_days else "drive" for d in days]
    return {"symbol": symbol, "days": days.tolist(), "source": source_of, "values": values,
            "integrity": integrity, "raw_integrity": raw_integrity,
            "sources": {**{k: int(v) for k, v in tape.sources.items()},
                        "legacy_overlap_sessions": int(tape.overlap_sessions),
                        "staging_days_added": len(merged.staging_days),
                        "drive_staging_overlap_days": merged.overlap_days,
                        "drive_staging_disagreements": merged.overlap_disagreements}}


def symbols_in(drive_root: Path, staging_root: Path | None) -> tuple[str, ...]:
    names: set[str] = set()
    for root, dirs in ((drive_root, (M.RAW_DIR, M.LEGACY_DIR)), (staging_root, (M.RAW_DIR,))):
        if root is None:
            continue
        for rel in dirs:
            folder = root / rel
            if folder.is_dir():
                names.update(d.name for d in folder.iterdir() if d.is_dir())
    return tuple(sorted(names))


def build_day_table(symbols: Sequence[str], drive_root: Path, staging_root: Path | None, *,
                    workers: int = 5, progress: Any = None) -> dict[str, Any]:
    out_symbol: list[str] = []
    out_day: list[int] = []
    out_source: list[str] = []
    values: dict[str, list[float]] = {name: [] for name in DAY_FIELDS}
    integrity: list[Mapping[str, int]] = []
    raw_integrity: list[Mapping[str, int]] = []
    per_symbol_issues: dict[str, dict[str, int]] = {}
    sources: list[Mapping[str, int]] = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(str(drive_root), str(staging_root) if staging_root else None)) as pool:
        for i, result in enumerate(pool.map(_one, symbols, chunksize=8)):
            if progress is not None and i % 400 == 0:
                progress(f"coverage {i}/{len(symbols)} symbol-days={len(out_day)}")
            if result is None:
                continue
            integrity.append(result["integrity"])
            raw_integrity.append(result["raw_integrity"])
            sources.append(result["sources"])
            issues = {k: v for k, v in {**result["integrity"], **result["raw_integrity"]}.items()
                      if k not in ("bars", "pages", "zero_volume") and v}
            if issues:
                per_symbol_issues[result["symbol"]] = issues
            n = len(result["days"])
            out_symbol.extend([result["symbol"]] * n)
            out_day.extend(result["days"])
            out_source.extend(result.get("source", []))
            for name in DAY_FIELDS:
                values[name].extend(result["values"].get(name, []))
    return {"symbols": np.array(out_symbol, dtype=object), "days": np.array(out_day, dtype=np.int64),
            "source": np.array(out_source, dtype=object),
            "values": {k: np.array(v, dtype=np.float64) for k, v in values.items()},
            "integrity": T.summarise_integrity(integrity),
            "raw_integrity": T.summarise_integrity(raw_integrity),
            "sources": T.summarise_integrity(sources),
            "per_symbol_issues": per_symbol_issues}


def save(table: Mapping[str, Any], path: Path) -> None:
    np.savez_compressed(path, symbols=table["symbols"].astype("U12"), days=table["days"],
                        source=table["source"].astype("U8"), **table["values"])


def load(path: Path) -> dict[str, np.ndarray]:
    payload = np.load(path, allow_pickle=False)
    return {k: payload[k] for k in payload.files}


def _pct(values: np.ndarray, qs: Sequence[float] = (5, 25, 50, 75, 95)) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {}
    return {f"p{int(q)}": float(np.percentile(values, q)) for q in qs}


def pair_report(table: Mapping[str, np.ndarray], pairs: Sequence[Any],
                eligible_keys: set[str] | None = None) -> dict[str, Any]:
    """Join T's after-hours with T+1's premarket on the calendar pairs and count."""
    from datetime import date
    epoch = date(1970, 1, 1)
    ordinal_of = lambda d: (d - epoch).days  # noqa: E731
    next_of = {ordinal_of(p.source_session): ordinal_of(p.next_trading_session) for p in pairs}
    kind_of = {ordinal_of(p.source_session): p.kind for p in pairs}
    early = {ordinal_of(p.source_session) for p in pairs if p.source_early_close}
    symbols, days = table["symbols"].astype(str), table["days"]
    v = {k: table[k] for k in DAY_FIELDS}
    key = np.char.add(np.char.add(symbols, "|"), days.astype(str))
    index = {k: i for i, k in enumerate(key.tolist())}

    source_rows = np.flatnonzero(np.isin(days, list(next_of)))
    after_rows = source_rows[v["after_bars"][source_rows] >= 1]
    target_idx = np.array([index.get(f"{symbols[i]}|{next_of[int(days[i])]}", -1) for i in after_rows])
    has_target = target_idx >= 0
    target_idx_ok = target_idx[has_target]
    paired_mask = has_target.copy()
    paired_mask[has_target] = v["pre_bars"][target_idx_ok] >= 1
    src = after_rows[paired_mask]
    tgt = target_idx[paired_mask]

    grid_sources = sorted(next_of)
    first_day, last_day = grid_sources[0], grid_sources[-1]
    report: dict[str, Any] = {
        "source_sessions_on_grid": len(grid_sources),
        "symbol_days_on_tape": int(days.size),
        "unique_symbols_on_tape": int(np.unique(symbols).size),
        "source_symbol_sessions_with_tape": int(source_rows.size),
        "after_symbol_sessions": int(after_rows.size),
        "after_sessions_available": int(np.unique(days[after_rows]).size),
        "next_premarket_sessions_available": int(np.unique(days[tgt]).size) if tgt.size else 0,
        "after_with_next_tape_day": int(has_target.sum()),
        "paired_symbol_sessions": int(src.size),
        "paired_sessions": int(np.unique(days[src]).size),
        "paired_unique_symbols": int(np.unique(symbols[src]).size),
        "paired_ratio_of_after": float(src.size / after_rows.size) if after_rows.size else float("nan"),
        "early_close_source_rows": int(np.isin(days[src], list(early)).sum()),
    }
    per_session = np.bincount(np.searchsorted(np.array(grid_sources), days[src]),
                              minlength=len(grid_sources))
    active = per_session[per_session > 0]
    report["symbols_per_paired_session"] = _pct(active.astype(float))
    report["kinds"] = {kind: int(sum(1 for d in days[src] if kind_of[int(d)] == kind))
                       for kind in ("normal_overnight", "weekend_gap", "holiday_gap")}
    report["source_of_rows"] = {s: int((table["source"][src] == s).sum())
                                for s in np.unique(table["source"][src]).tolist()} if src.size else {}

    def anchor_block(rows_t: np.ndarray) -> dict[str, Any]:
        n = max(rows_t.size, 1)
        out: dict[str, Any] = {"rows": int(rows_t.size)}
        for tag in ANCHORS:
            out[tag] = {kind: float(v[f"{kind}_{tag}"][rows_t].sum() / n)
                        for kind in ("exact", "within5", "within15", "prior")}
            has = v[f"within15_{tag}"][rows_t] > 0
            out[tag]["first_bar_dollar"] = _pct(v[f"first_bar_dollar_{tag}"][rows_t][has])
            out[tag]["first_bar_volume"] = _pct(v[f"first_bar_volume_{tag}"][rows_t][has])
            out[tag]["prior30_bars"] = _pct(v[f"prior30_bars_{tag}"][rows_t][has])
            out[tag]["prior30_zero_share"] = float((v[f"prior30_bars_{tag}"][rows_t][has] == 0).mean()) if has.any() else float("nan")
        for tag in EXIT_LAST_BAR:
            out[f"exit_{tag}_print_in_0800_window"] = float(v[f"exit_{tag}_after_0800"][rows_t].sum() / n)
        out["exit_0924_exact"] = float(v["exit_0924_exact"][rows_t].sum() / n)
        both = (v["within15_0800"][rows_t] > 0) & (v["exit_0925_after_0800"][rows_t] > 0)
        out["entry0800w15_and_exit0925"] = float(both.sum() / n)
        return out

    report["premarket_anchor_coverage"] = anchor_block(tgt)
    report["after_activity"] = {
        "after_bars": _pct(v["after_bars"][src]),
        "after_dollar": _pct(v["after_dollar"][src]),
        "after_bars_ge": {str(k): float((v["after_bars"][src] >= k).mean()) for k in (1, 3, 5, 10, 30)},
        "after_bars_ex1600": _pct(v["after_bars_ex1600"][src]),
        "after_dollar_ex1600": _pct(v["after_dollar_ex1600"][src]),
        "bar_1600_dollar": _pct(v["bar_1600_dollar"][src]),
        "after_bars_ex1600_ge": {str(k): float((v["after_bars_ex1600"][src] >= k).mean())
                                 for k in (1, 3, 5, 10, 30)},
        "after_last30_any": float((v["after_bars_last30"][src] >= 1).mean()) if src.size else float("nan"),
        "pre_bars_next": _pct(v["pre_bars"][tgt]),
        "pre_dollar_next": _pct(v["pre_dollar"][tgt]),
    }
    report["duplicate_state"] = {
        "source_identical_dropped": int((v["duplicate_state"][src] == 1).sum()),
        "source_conflicting": int((v["duplicate_state"][src] == 2).sum()),
        "target_identical_dropped": int((v["duplicate_state"][tgt] == 1).sum()),
        "target_conflicting": int((v["duplicate_state"][tgt] == 2).sum()),
    }
    if eligible_keys is not None:
        from datetime import timedelta
        src_keys = [f"{(epoch + timedelta(days=int(d))).isoformat()}|{s}"
                    for d, s in zip(days[src], symbols[src])]
        elig = np.array([k in eligible_keys for k in src_keys], dtype=bool)
        report["daily_pit_eligible"] = {"rows": int(elig.sum()),
                                        "sessions": int(np.unique(days[src][elig]).size),
                                        "symbols": int(np.unique(symbols[src][elig]).size)}
        report["daily_pit_eligible_anchor_coverage"] = anchor_block(tgt[elig])
        # after-activity thresholds on the eligible set, counts only
        eb = v["after_bars"][src][elig]
        report["daily_pit_eligible_after_bars_ge"] = {str(k): int((eb >= k).sum()) for k in (1, 3, 5, 10)}
        ex = v["after_bars_ex1600"][src][elig]
        ed = v["after_dollar_ex1600"][src][elig]
        entry = (v["within15_0800"][tgt][elig] > 0) & (v["exit_0925_after_0800"][tgt][elig] > 0)
        report["daily_pit_eligible_ex1600"] = {
            "after_bars_ex1600_ge": {str(k): int((ex >= k).sum()) for k in (1, 3, 5, 10)},
            "after_bars_ex1600_ge_and_entry0800": {str(k): int(((ex >= k) & entry).sum())
                                                   for k in (1, 3, 5, 10)},
            "after_dollar_ex1600": _pct(ed),
            "after_dollar_ex1600_ge_and_bars3": {str(k): int(((ex >= 3) & (ed >= k)).sum())
                                                 for k in (10_000, 50_000, 100_000)},
        }
        quarters: dict[str, int] = {}
        for d in days[src][elig]:
            day = epoch + timedelta(days=int(d))
            q = f"{day.year}Q{(day.month - 1) // 3 + 1}"
            quarters[q] = quarters.get(q, 0) + 1
        report["daily_pit_eligible_by_quarter"] = dict(sorted(quarters.items()))
        months: dict[str, int] = {}
        for d in days[src][elig]:
            day = epoch + timedelta(days=int(d))
            m = f"{day.year}-{day.month:02d}"
            months[m] = months.get(m, 0) + 1
        report["daily_pit_eligible_by_month"] = dict(sorted(months.items()))
    report["first_source_day"] = str(epoch.fromordinal(first_day + epoch.toordinal()))
    report["last_source_day"] = str(epoch.fromordinal(last_day + epoch.toordinal()))
    return report
