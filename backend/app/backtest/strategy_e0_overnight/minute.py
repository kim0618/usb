"""Closing-strength features and next-open labels from the minute tape.

The minute store holds two forms of the same authority (``MASSIVE_TICKER_AGGREGATE``): the
provider's own pages under ``market_data/raw/massive/minute`` and the collector's normalized
Parquet under ``market_data/normalized/minute/massive``. E0 reads both, keeps them per session,
and records every session a symbol has in both forms rather than silently preferring one.

Timestamps are the documented ``t`` field: UTC milliseconds at the **start** of the bar. The
conversion to Eastern wall clock is done once per run against the zone's real transition
instants, so a bar is never shifted by an hour across a DST boundary. A bar whose start is at
or after 16:00 ET never enters a feature; only the labels read D+1.

Early-close sessions (13:00 ET) have no bars in the declared 15:30-15:59 window. They are
counted and dropped rather than measured against a window that does not exist.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
RAW_DIR = "market_data/raw/massive/minute"
LEGACY_DIR = "market_data/normalized/minute/massive"
PAGE_RE = re.compile(r"^(?P<sym>.+)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.p\d+\.json\.gz$")

REGULAR_OPEN_MIN = 9 * 60 + 30      # 09:30 ET
REGULAR_CLOSE_MIN = 16 * 60         # 16:00 ET, exclusive
LAST30_MIN = 15 * 60 + 30
LAST15_MIN = 15 * 60 + 45
FIRST_1M_END = REGULAR_OPEN_MIN + 1
FIRST_5M_END = REGULAR_OPEN_MIN + 5
FIRST_15M_END = REGULAR_OPEN_MIN + 15


def et_offsets(start: datetime, end: datetime) -> tuple[np.ndarray, np.ndarray]:
    """Transition instants (epoch seconds) and the UTC offset in seconds that follows each.

    Built by walking the zone hour by hour once per run over the study range; a few thousand
    steps, and it removes per-bar timezone arithmetic from the hot path entirely.
    """
    edges: list[float] = [start.timestamp()]
    offsets: list[float] = [start.astimezone(ET).utcoffset().total_seconds()]
    cursor = start
    step = timedelta(hours=1)
    while cursor < end:
        cursor += step
        offset = cursor.astimezone(ET).utcoffset().total_seconds()
        if offset != offsets[-1]:
            edges.append(cursor.timestamp())
            offsets.append(offset)
    return np.array(edges), np.array(offsets)


def to_et_fields(epoch_ms: np.ndarray, edges: np.ndarray, offsets: np.ndarray,
                 ) -> tuple[np.ndarray, np.ndarray]:
    """``(ET ordinal date, minute of the ET day)`` for each bar start."""
    seconds = epoch_ms / 1000.0
    idx = np.searchsorted(edges, seconds, side="right") - 1
    idx = np.clip(idx, 0, len(offsets) - 1)
    local = seconds + offsets[idx]
    days = np.floor(local / 86400.0)
    minute = np.floor((local - days * 86400.0) / 60.0)
    return days.astype(np.int64), minute.astype(np.int32)


@dataclass(frozen=True)
class SymbolTape:
    """One symbol's bars, already reduced to the fields the study reads."""

    symbol: str
    et_day: np.ndarray       # ET ordinal (days since epoch)
    minute: np.ndarray       # minute of the ET day
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    vwap: np.ndarray
    sources: Mapping[str, int]
    overlap_sessions: int


def _arrays_from_results(results: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    n = len(results)
    out = {k: np.full(n, np.nan) for k in ("o", "h", "l", "c", "v", "vw")}
    stamps = np.zeros(n, dtype=np.int64)
    keep = np.zeros(n, dtype=bool)
    for i, bar in enumerate(results):
        t = bar.get("t")
        if not isinstance(t, int):
            continue
        stamps[i] = t
        keep[i] = True
        for key in ("o", "h", "l", "c", "v", "vw"):
            value = bar.get(key)
            if value is not None:
                out[key][i] = float(value)
    out["t"] = stamps
    out["keep"] = keep
    return out


def load_symbol_tape(symbol: str, workspace_root: Path, edges: np.ndarray, offsets: np.ndarray,
                     *, use_legacy: bool = True) -> SymbolTape | None:
    """Every bar E0 can see for one symbol, from both storage forms of the minute authority."""
    chunks: list[dict[str, np.ndarray]] = []
    sources = {"raw_pages": 0, "raw_bars": 0, "legacy_files": 0, "legacy_bars": 0}
    raw_days: set[int] = set()
    legacy_days: set[int] = set()

    raw_dir = workspace_root / RAW_DIR / symbol
    if raw_dir.is_dir():
        for path in sorted(raw_dir.iterdir()):
            if not PAGE_RE.match(path.name):
                continue
            payload = json.loads(gzip.decompress(path.read_bytes()))
            results = payload.get("results") or ()
            if not results:
                continue
            block = _arrays_from_results(results)
            day, minute = to_et_fields(block["t"][block["keep"]], edges, offsets)
            chunk = {k: block[k][block["keep"]] for k in ("o", "h", "l", "c", "v", "vw")}
            chunk["day"], chunk["minute"] = day, minute
            chunks.append(chunk)
            sources["raw_pages"] += 1
            sources["raw_bars"] += int(day.size)
            raw_days.update(np.unique(day).tolist())

    if use_legacy:
        legacy_dir = workspace_root / LEGACY_DIR / symbol
        if legacy_dir.is_dir():
            import pyarrow.parquet as pq
            for path in sorted(legacy_dir.glob("*.parquet")):
                table = pq.read_table(path, columns=["timestamp_utc", "open", "high", "low",
                                                     "close", "volume", "vwap"])
                if table.num_rows == 0:
                    continue
                stamps = table.column("timestamp_utc").to_numpy(zero_copy_only=False)
                epoch_ms = stamps.astype("datetime64[ms]").astype(np.int64)
                day, minute = to_et_fields(epoch_ms, edges, offsets)
                chunk = {"o": table.column("open").to_numpy(zero_copy_only=False),
                         "h": table.column("high").to_numpy(zero_copy_only=False),
                         "l": table.column("low").to_numpy(zero_copy_only=False),
                         "c": table.column("close").to_numpy(zero_copy_only=False),
                         "v": table.column("volume").to_numpy(zero_copy_only=False),
                         "vw": table.column("vwap").to_numpy(zero_copy_only=False),
                         "day": day, "minute": minute}
                chunks.append(chunk)
                sources["legacy_files"] += 1
                sources["legacy_bars"] += int(day.size)
                legacy_days.update(np.unique(day).tolist())

    if not chunks:
        return None
    overlap = raw_days & legacy_days
    merged = {k: np.concatenate([c[k] for c in chunks]) for k in ("o", "h", "l", "c", "v", "vw",
                                                                  "day", "minute")}
    # A session carried by both storage forms would double-count volume, so the legacy rows win
    # and the raw rows of that session are dropped; the count is reported, never hidden.
    if overlap:
        is_raw = np.concatenate([np.full(c["day"].size, i < sources["raw_pages"])
                                 for i, c in enumerate(chunks)])
        drop = is_raw & np.isin(merged["day"], list(overlap))
        merged = {k: v[~drop] for k, v in merged.items()}
    order = np.lexsort((merged["minute"], merged["day"]))
    return SymbolTape(symbol=symbol, et_day=merged["day"][order], minute=merged["minute"][order],
                      open=merged["o"][order], high=merged["h"][order], low=merged["l"][order],
                      close=merged["c"][order], volume=merged["v"][order],
                      vwap=merged["vw"][order], sources=sources, overlap_sessions=len(overlap))


CLOSING_FIELDS = ("return_1530_to_close", "return_1545_to_close", "last30m_volume",
                  "last15m_volume", "last30m_volume_share", "close_vs_session_vwap",
                  "close_to_day_high", "last30m_high_break", "last15m_high_break",
                  "minute_close", "regular_volume", "regular_bars")
NEXT_FIELDS = ("next_open", "close_to_next_1m", "close_to_next_5m", "close_to_next_15m",
               "next_5m_mfe", "next_5m_mae", "next_15m_mfe", "next_15m_mae", "next_bars_15m")


def session_features(tape: SymbolTape) -> dict[int, dict[str, float]]:
    """Per ET session: the declared closing-strength block plus the first 15 minutes of it.

    The first-15-minutes block of session D is what the label of session D-1 reads, so it is
    computed here once and joined by the caller. Nothing in the closing block of D touches a
    bar at or after 16:00 ET of D.
    """
    out: dict[int, dict[str, float]] = {}
    regular = (tape.minute >= REGULAR_OPEN_MIN) & (tape.minute < REGULAR_CLOSE_MIN)
    if not regular.any():
        return out
    day = tape.et_day[regular]
    minute = tape.minute[regular]
    o, h, l = tape.open[regular], tape.high[regular], tape.low[regular]
    c, v, vw = tape.close[regular], tape.volume[regular], tape.vwap[regular]
    boundaries = np.flatnonzero(np.diff(day)) + 1
    for lo, hi in zip(np.concatenate([[0], boundaries]), np.concatenate([boundaries, [day.size]])):
        m = minute[lo:hi]
        if m.size == 0:
            continue
        block: dict[str, float] = {"regular_bars": float(m.size)}
        so, sh, sl, sc = o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi]
        sv, svw = v[lo:hi], vw[lo:hi]
        volume_total = float(np.nansum(sv))
        last_close = float(sc[-1]) if np.isfinite(sc[-1]) else float("nan")
        block["minute_close"] = last_close
        block["regular_volume"] = volume_total
        weights = np.where(np.isfinite(svw) & np.isfinite(sv), sv, 0.0)
        vwap_num = float(np.nansum(np.where(weights > 0, svw * weights, 0.0)))
        vwap = vwap_num / float(weights.sum()) if weights.sum() > 0 else float("nan")
        day_high = float(np.nanmax(sh)) if np.isfinite(sh).any() else float("nan")
        block["close_vs_session_vwap"] = last_close / vwap - 1.0 if vwap and np.isfinite(vwap) else float("nan")
        block["close_to_day_high"] = last_close / day_high - 1.0 if day_high else float("nan")

        for tag, edge in (("last30m", LAST30_MIN), ("last15m", LAST15_MIN)):
            window = m >= edge
            before = m < edge
            block[f"{tag}_volume"] = float(np.nansum(sv[window])) if window.any() else float("nan")
            if tag == "last30m":
                block["last30m_volume_share"] = (block["last30m_volume"] / volume_total
                                                 if volume_total > 0 and window.any() else float("nan"))
            if window.any() and before.any():
                after_high = float(np.nanmax(sh[window]))
                prior_high = float(np.nanmax(sh[before]))
                block[f"{tag}_high_break"] = float(after_high >= prior_high)
            else:
                block[f"{tag}_high_break"] = float("nan")
            first = np.flatnonzero(window)
            if first.size and np.isfinite(so[first[0]]) and np.isfinite(last_close):
                start_price = float(so[first[0]])
                key = "return_1530_to_close" if tag == "last30m" else "return_1545_to_close"
                block[key] = last_close / start_price - 1.0 if start_price else float("nan")
            else:
                block["return_1530_to_close" if tag == "last30m" else "return_1545_to_close"] = float("nan")

        open_bar = np.flatnonzero(m == REGULAR_OPEN_MIN)
        block["next_open"] = float(so[open_bar[0]]) if open_bar.size else float("nan")
        for name, end in (("1m", FIRST_1M_END), ("5m", FIRST_5M_END), ("15m", FIRST_15M_END)):
            window = (m >= REGULAR_OPEN_MIN) & (m < end)
            if window.any():
                block[f"first_{name}_close"] = float(sc[np.flatnonzero(window)[-1]])
                block[f"first_{name}_high"] = float(np.nanmax(sh[window]))
                block[f"first_{name}_low"] = float(np.nanmin(sl[window]))
            else:
                block[f"first_{name}_close"] = float("nan")
                block[f"first_{name}_high"] = float("nan")
                block[f"first_{name}_low"] = float("nan")
        block["first_15m_bars"] = float(((m >= REGULAR_OPEN_MIN) & (m < FIRST_15M_END)).sum())
        out[int(day[lo])] = block
    return out


def ordinal(session: date) -> int:
    return int((session - date(1970, 1, 1)).days)


def pair_sessions(features: Mapping[int, Mapping[str, float]], sessions: Sequence[date],
                  ) -> list[dict[str, Any]]:
    """Join the closing block of D with the opening block of the next grid session.

    The pairing walks the XNYS grid, not the tape, so a missing session cannot silently turn a
    two-day gap into an overnight return.
    """
    rows = []
    for i in range(len(sessions) - 1):
        d, nxt = ordinal(sessions[i]), ordinal(sessions[i + 1])
        today, tomorrow = features.get(d), features.get(nxt)
        if today is None or tomorrow is None:
            continue
        close = today.get("minute_close", float("nan"))
        if not np.isfinite(close) or close <= 0:
            continue
        row: dict[str, Any] = {"session": sessions[i], "next_session": sessions[i + 1]}
        for key in CLOSING_FIELDS:
            row[key] = today.get(key, float("nan"))
        row["next_open"] = tomorrow.get("next_open", float("nan"))
        row["minute_overnight"] = (row["next_open"] / close - 1.0
                                   if np.isfinite(row["next_open"]) else float("nan"))
        for name in ("1m", "5m", "15m"):
            row[f"close_to_next_{name}"] = tomorrow.get(f"first_{name}_close", float("nan")) / close - 1.0
        for name in ("5m", "15m"):
            row[f"next_{name}_mfe"] = tomorrow.get(f"first_{name}_high", float("nan")) / close - 1.0
            row[f"next_{name}_mae"] = tomorrow.get(f"first_{name}_low", float("nan")) / close - 1.0
        row["next_bars_15m"] = tomorrow.get("first_15m_bars", float("nan"))
        rows.append(row)
    return rows
