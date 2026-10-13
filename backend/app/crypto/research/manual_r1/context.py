"""Reconstructing what the market looked like just before each manual entry.

D2's canonical 1m series stops at 2026-09-23, before the manual trading began, so the paper
terminal's own input tape is the only record of these minutes. That turns out to be an upgrade:
the tape carries roughly one book and ticker snapshot per second, which is the resolution a human
watching a screen actually reacts to.

Nothing in this module reads past the entry instant. The features describe the approach to the
entry and stop there; what happened afterwards is measured separately, in the outcome path, and
is never allowed to leak back into an entry feature.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

#: Offsets before the entry, in seconds. The short end matters because the terminal shows a
#: 15 second candle, so a human can see structure the 1m grid cannot express.
LOOKBACKS_SEC = (15, 30, 60, 180, 300, 900, 1800, 3600)


@dataclass
class Tape:
    """A merged, time-ordered view of the market snapshots the terminal recorded."""

    ts_ms: np.ndarray
    mark: np.ndarray
    last: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    index: np.ndarray
    funding: np.ndarray

    def __len__(self) -> int:
        return len(self.ts_ms)

    @property
    def span_ms(self) -> tuple[int, int]:
        return int(self.ts_ms[0]), int(self.ts_ms[-1])

    def covers(self, ts_ms: int, lookback_sec: int) -> bool:
        return self.ts_ms[0] <= ts_ms - lookback_sec * 1000 and ts_ms <= self.ts_ms[-1]

    def at_or_before(self, ts_ms: int) -> int | None:
        """Index of the last snapshot at or before `ts_ms`, or None when there is none."""
        position = int(np.searchsorted(self.ts_ms, ts_ms, side="right")) - 1
        return None if position < 0 else position


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue          # a torn final line in a live-appended tape
            if record.get("kind") != "MARKET":
                continue
            yield record.get("payload", record)


def _best(levels: Any, side: str) -> float:
    if not levels:
        return float("nan")
    try:
        return float(levels[0][0])
    except (TypeError, IndexError, ValueError):
        return float("nan")


def load_tape(paths: Sequence[Path]) -> Tape:
    """Merge tape files into one ascending series, dropping exact duplicate timestamps."""
    ts: list[int] = []
    mark: list[float] = []
    last: list[float] = []
    bid: list[float] = []
    ask: list[float] = []
    index: list[float] = []
    funding: list[float] = []

    for path in paths:
        for payload in _rows(path):
            stamp = payload.get("ts_ms")
            if stamp is None:
                continue
            ts.append(int(stamp))
            mark.append(float(payload.get("mark_price") or "nan"))
            last.append(float(payload.get("last_price") or "nan"))
            bid.append(_best(payload.get("bids"), "bid"))
            ask.append(_best(payload.get("asks"), "ask"))
            index.append(float(payload.get("index_price") or "nan"))
            funding.append(float(payload.get("funding_rate") or "nan"))

    order = np.argsort(np.array(ts, dtype=np.int64), kind="stable")
    stamps = np.array(ts, dtype=np.int64)[order]
    keep = np.concatenate([[True], np.diff(stamps) > 0])
    pick = order[keep]
    return Tape(ts_ms=np.array(ts, dtype=np.int64)[pick],
                mark=np.array(mark)[pick], last=np.array(last)[pick],
                bid=np.array(bid)[pick], ask=np.array(ask)[pick],
                index=np.array(index)[pick], funding=np.array(funding)[pick])


def entry_features(tape: Tape, ts_ms: int) -> dict[str, Any] | None:
    """What the approach to `ts_ms` looked like. Reads nothing at or after the entry instant.

    The emphasis is on path and shape rather than level: returns over several horizons, whether
    those returns are still accelerating or already fading, where the price sits inside its recent
    range, and whether the recent extremes are stepping up or down. Those are the things a person
    watching a chart can actually see.
    """
    end = tape.at_or_before(ts_ms)
    if end is None or end < 2:
        return None

    price = tape.mark
    now = float(price[end])
    if not np.isfinite(now):
        return None

    out: dict[str, Any] = {"ts_ms": ts_ms, "snapshots_before": end + 1, "mark": now}

    # --- returns over several horizons, in basis points
    returns: dict[int, float] = {}
    for seconds in LOOKBACKS_SEC:
        position = tape.at_or_before(ts_ms - seconds * 1000)
        if position is None or not np.isfinite(price[position]) or price[position] <= 0:
            out[f"ret_{seconds}s_bp"] = None
            continue
        value = (now / float(price[position]) - 1) * 1e4
        returns[seconds] = value
        out[f"ret_{seconds}s_bp"] = value

    # --- is the move still building or already fading
    if 60 in returns and 15 in returns:
        # the last quarter minute against the average quarter minute of the last full minute
        out["accel_15s_vs_60s"] = returns[15] - returns[60] / 4
    else:
        out["accel_15s_vs_60s"] = None
    if 300 in returns and 60 in returns:
        out["accel_60s_vs_300s"] = returns[60] - returns[300] / 5
    else:
        out["accel_60s_vs_300s"] = None

    # --- where the price sits inside its recent range, and how wide that range is
    for seconds in (300, 900, 3600):
        start = tape.at_or_before(ts_ms - seconds * 1000)
        if start is None or start >= end:
            out[f"pos_in_range_{seconds}s"] = None
            out[f"range_{seconds}s_bp"] = None
            out[f"drawdown_from_high_{seconds}s_bp"] = None
            continue
        window = price[start:end + 1]
        window = window[np.isfinite(window)]
        if len(window) < 2:
            out[f"pos_in_range_{seconds}s"] = None
            out[f"range_{seconds}s_bp"] = None
            out[f"drawdown_from_high_{seconds}s_bp"] = None
            continue
        high, low = float(window.max()), float(window.min())
        out[f"range_{seconds}s_bp"] = (high - low) / ((high + low) / 2) * 1e4
        out[f"pos_in_range_{seconds}s"] = ((now - low) / (high - low)) if high > low else None
        out[f"drawdown_from_high_{seconds}s_bp"] = (now / high - 1) * 1e4

    # --- are the recent swing extremes stepping up or down
    out.update(_swing_structure(tape, ts_ms, end))

    # --- volatility now against volatility a little earlier
    out.update(_volatility_transition(tape, ts_ms, end))

    # --- microstructure the operator can see on screen
    spread = float(tape.ask[end]) - float(tape.bid[end])
    mid = (float(tape.ask[end]) + float(tape.bid[end])) / 2
    out["spread_bp"] = (spread / mid * 1e4) if mid > 0 and np.isfinite(spread) else None
    basis = float(tape.index[end])
    out["basis_bp"] = ((now / basis - 1) * 1e4) if np.isfinite(basis) and basis > 0 else None
    out["funding_rate"] = float(tape.funding[end]) if np.isfinite(tape.funding[end]) else None
    return out


def _swing_structure(tape: Tape, ts_ms: int, end: int) -> dict[str, Any]:
    """Higher highs or lower highs over the last few minutes, in the crudest honest form."""
    out: dict[str, Any] = {}
    halves = []
    start = tape.at_or_before(ts_ms - 900 * 1000)
    if start is None or end - start < 4:
        return {"higher_high_15m": None, "higher_low_15m": None,
                "failed_high_15m_bp": None}
    middle = start + (end - start) // 2
    for lo, hi in ((start, middle), (middle, end + 1)):
        window = tape.mark[lo:hi]
        window = window[np.isfinite(window)]
        halves.append((float(window.max()), float(window.min())) if len(window) else (np.nan, np.nan))
    (first_high, first_low), (second_high, second_low) = halves
    out["higher_high_15m"] = bool(second_high > first_high)
    out["higher_low_15m"] = bool(second_low > first_low)
    # how far the latest half fell short of the earlier half's high, when it did
    out["failed_high_15m_bp"] = ((second_high / first_high - 1) * 1e4
                                 if np.isfinite(first_high) and first_high > 0 else None)
    return out


def _volatility_transition(tape: Tape, ts_ms: int, end: int) -> dict[str, Any]:
    """Not "is volatility high" but "is it rising or falling", which is what a person notices."""
    def realised(seconds: int) -> float | None:
        start = tape.at_or_before(ts_ms - seconds * 1000)
        if start is None or end - start < 3:
            return None
        window = tape.mark[start:end + 1]
        window = window[np.isfinite(window) & (window > 0)]
        if len(window) < 3:
            return None
        steps = np.diff(np.log(window))
        return float(np.std(steps, ddof=0) * 1e4)

    short = realised(300)
    long = realised(1800)
    return {"rv_300s_bp": short, "rv_1800s_bp": long,
            "vol_ratio_300_1800": (short / long) if (short and long and long > 0) else None}


def outcome_path(tape: Tape, entry_ts_ms: int, side: str, entry_price: float,
                 horizons_sec: Sequence[int] = (15, 30, 60, 180, 300, 900, 1800, 3600)
                 ) -> dict[str, Any]:
    """What happened *after* the entry. Diagnostic only; never an entry feature."""
    out: dict[str, Any] = {}
    direction = 1.0 if side == "LONG" else -1.0
    for seconds in horizons_sec:
        position = tape.at_or_before(entry_ts_ms + seconds * 1000)
        if position is None or tape.ts_ms[position] < entry_ts_ms:
            out[f"fwd_{seconds}s_bp"] = None
            continue
        price = float(tape.mark[position])
        out[f"fwd_{seconds}s_bp"] = (direction * (price / entry_price - 1) * 1e4
                                     if np.isfinite(price) else None)
    return out
