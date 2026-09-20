"""Premarket features as of 09:25 ET, and the opening blocks the labels are built from.

Two boundaries do all the point-in-time work here and neither is a runtime check:

* every feature reads bars whose **start is at or before 09:24 ET**. A bar starting at 09:25 has
  not closed at the decision time, so it is outside the slice before any arithmetic happens;
* every label reads bars from 09:30 onward and is never an input to a feature or a filter.

Premarket minute bars exist only where a trade printed, so a session's premarket is a handful of
scattered bars rather than 330 of them (the coverage audit measured a median of 4). Two
consequences are handled explicitly rather than averaged over: a window return needs two bars in
that window or it stays undefined, and a horizon label is computed both from the exact declared
bar and from the last print at or before it, because the exact bar is missing on about a quarter
of sessions and missing in a way that correlates with liquidity.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal

#: ET minute-of-day boundaries. 570 = 09:30.
PREMARKET_START = 4 * 60             # 04:00
DECISION_LAST_BAR = 9 * 60 + 24      # 09:24, the last bar closed at 09:25
OPEN_MIN = 9 * 60 + 30               # 09:30
WINDOW_STARTS = {"return_0800_0925": 8 * 60, "return_0855_0925": 8 * 60 + 55,
                 "return_0900_0925": 9 * 60, "return_0915_0925": 9 * 60 + 15}
#: Horizon name -> the last bar the label may read (inclusive).
HORIZONS = {"1m": OPEN_MIN, "5m": OPEN_MIN + 4, "15m": OPEN_MIN + 14}
#: Decision times kept for robustness; the primary is the first.
DECISION_TIMES = {"0925": DECISION_LAST_BAR, "0915": 9 * 60 + 14, "0900": 8 * 60 + 59}

PREMARKET_FIELDS = ("pm_bars", "pm_first_price", "pm_last_price", "pm_high", "pm_low",
                    "pm_volume", "pm_dollar_volume", "pm_last30m_dollar_volume",
                    "return_0800_0925", "return_0855_0925", "return_0900_0925",
                    "return_0915_0925", "pm_return")
OPENING_FIELDS = tuple(
    [f"open_{OPEN_MIN}"]
    + [f"{kind}_{name}" for name in HORIZONS for kind in ("last", "strict", "high", "low")]
    + ["open_bars_15m"])


def _window_return(minute: np.ndarray, open_: np.ndarray, close: np.ndarray,
                   start: int, end: int) -> float:
    """Last close over first open inside ``[start, end]``; undefined below two bars."""
    sel = np.flatnonzero((minute >= start) & (minute <= end))
    if sel.size < 2:
        return float("nan")
    first, last = float(open_[sel[0]]), float(close[sel[-1]])
    if not (np.isfinite(first) and np.isfinite(last)) or first <= 0:
        return float("nan")
    return last / first - 1.0


def premarket_block(minute: np.ndarray, open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                    close: np.ndarray, volume: np.ndarray, vwap: np.ndarray,
                    decision_last: int = DECISION_LAST_BAR) -> dict[str, float] | None:
    """One session's premarket, as known at the declared decision time."""
    sel = np.flatnonzero((minute >= PREMARKET_START) & (minute <= decision_last))
    if sel.size == 0:
        return None
    m = minute[sel]
    o, h, l, c = open_[sel], high[sel], low[sel], close[sel]
    v, w = volume[sel], vwap[sel]
    price = np.where(np.isfinite(w), w, c)
    dollar = np.where(np.isfinite(v), v, 0.0) * np.where(np.isfinite(price), price, 0.0)
    block: dict[str, float] = {
        "pm_bars": float(m.size),
        "pm_first_price": float(o[0]),
        "pm_last_price": float(c[-1]),
        "pm_high": float(np.nanmax(h)) if np.isfinite(h).any() else float("nan"),
        "pm_low": float(np.nanmin(l)) if np.isfinite(l).any() else float("nan"),
        "pm_volume": float(np.nansum(v)),
        "pm_dollar_volume": float(dollar.sum()),
        "pm_last30m_dollar_volume": float(dollar[m >= WINDOW_STARTS["return_0855_0925"]].sum()),
    }
    for name, start in WINDOW_STARTS.items():
        block[name] = _window_return(m, o, c, start, decision_last)
    first, last = block["pm_first_price"], block["pm_last_price"]
    block["pm_return"] = last / first - 1.0 if np.isfinite(first) and first > 0 else float("nan")
    return block


def opening_block(minute: np.ndarray, open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                  close: np.ndarray) -> dict[str, float] | None:
    """The 09:30 anchor and, for each declared horizon, the strict bar and the last print."""
    anchor = np.flatnonzero(minute == OPEN_MIN)
    if anchor.size == 0:
        return None
    block: dict[str, float] = {f"open_{OPEN_MIN}": float(open_[anchor[0]])}
    for name, end in HORIZONS.items():
        window = np.flatnonzero((minute >= OPEN_MIN) & (minute <= end))
        exact = np.flatnonzero(minute == end)
        block[f"last_{name}"] = float(close[window[-1]]) if window.size else float("nan")
        block[f"strict_{name}"] = float(close[exact[0]]) if exact.size else float("nan")
        block[f"high_{name}"] = float(np.nanmax(high[window])) if window.size else float("nan")
        block[f"low_{name}"] = float(np.nanmin(low[window])) if window.size else float("nan")
    block["open_bars_15m"] = float(((minute >= OPEN_MIN) & (minute <= HORIZONS["15m"])).sum())
    return block


@dataclass(frozen=True)
class SessionRow:
    session: date
    premarket: dict[str, float]
    opening: dict[str, float]
    pm_rvol: float


def _rolling_prior_median(values: Sequence[float], window: int, minimum: int) -> list[float]:
    """Median of the previous ``window`` entries, strictly before each position."""
    out: list[float] = []
    history: list[float] = []
    for value in values:
        if len(history) >= minimum:
            out.append(float(np.median(history[-window:])))
        else:
            out.append(float("nan"))
        if np.isfinite(value) and value > 0:
            history.append(float(value))
    return out


def session_rows(tape: SymbolTape, *, decision_last: int = DECISION_LAST_BAR,
                 rvol_window: int = 20, rvol_minimum: int = 5) -> dict[int, SessionRow]:
    """Every ET session of one symbol that has both a premarket and a 09:30 open bar.

    ``pm_rvol`` divides the session's premarket dollar volume by the median of the previous
    sessions that had premarket activity. The history is strictly prior, so the ratio is known
    at the decision time.
    """
    day, minute = tape.et_day, tape.minute
    order = np.lexsort((minute, day))
    day, minute = day[order], minute[order]
    o, h = tape.open[order], tape.high[order]
    l, c = tape.low[order], tape.close[order]
    v, w = tape.volume[order], tape.vwap[order]

    boundaries = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], boundaries])
    stops = np.concatenate([boundaries, [day.size]])

    staged: list[tuple[int, dict[str, float], dict[str, float]]] = []
    for lo, hi in zip(starts, stops):
        pre = premarket_block(minute[lo:hi], o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi],
                              v[lo:hi], w[lo:hi], decision_last)
        if pre is None:
            continue
        opening = opening_block(minute[lo:hi], o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi])
        if opening is None:
            continue
        staged.append((int(day[lo]), pre, opening))

    medians = _rolling_prior_median([p["pm_dollar_volume"] for _, p, _ in staged],
                                    rvol_window, rvol_minimum)
    out: dict[int, SessionRow] = {}
    for (et_day, pre, opening), median in zip(staged, medians):
        rvol = (pre["pm_dollar_volume"] / median
                if np.isfinite(median) and median > 0 else float("nan"))
        out[et_day] = SessionRow(date.fromordinal(et_day + date(1970, 1, 1).toordinal()),
                                 pre, opening, rvol)
    return out


def derived_features(pre: Mapping[str, float]) -> dict[str, float]:
    """The declared shape features that need no daily input."""
    high, low, last = pre["pm_high"], pre["pm_low"], pre["pm_last_price"]
    span = high - low
    return {
        "distance_to_premarket_high": last / high - 1.0 if np.isfinite(high) and high > 0
        else float("nan"),
        "position_in_premarket_range": (last - low) / span if np.isfinite(span) and span > 0
        else float("nan"),
        "premarket_volume_last30m_ratio": (pre["pm_last30m_dollar_volume"] / pre["pm_dollar_volume"]
                                           if pre["pm_dollar_volume"] > 0 else float("nan")),
    }


def labels(opening: Mapping[str, float]) -> dict[str, float]:
    """R, MFE and MAE against the 09:30 open, for every declared horizon and both readings."""
    anchor = opening[f"open_{OPEN_MIN}"]
    out: dict[str, float] = {}
    if not np.isfinite(anchor) or anchor <= 0:
        for name in HORIZONS:
            for key in (f"R_{name}", f"R_{name}_strict", f"MFE_{name}", f"MAE_{name}"):
                out[key] = float("nan")
        return out
    for name in HORIZONS:
        out[f"R_{name}"] = opening[f"last_{name}"] / anchor - 1.0
        out[f"R_{name}_strict"] = opening[f"strict_{name}"] / anchor - 1.0
        out[f"MFE_{name}"] = opening[f"high_{name}"] / anchor - 1.0
        out[f"MAE_{name}"] = opening[f"low_{name}"] / anchor - 1.0
    return out
