"""The Trading V1.1 09:25 decision universe, built only from information known at 09:25 ET.

Two V1 filters are removed and nothing else is changed:

* ``requires_open_bar_0930`` (E1 premarket universe; ``premarket.session_rows`` dropped a session
  without a 09:30 bar, and ``dataset.build`` required a finite 09:30 open);
* ``open(D+1) > 0`` of the E0 daily row for D-1, i.e. that session D's grouped-daily open exists.

Both are moved to the entry layer, where E-D2 already rejects a missing 09:30 bar as
``NO_TRADE_MISSING_ENTRY_BAR`` without replacement.

Everything else reuses Research code or reproduces a Research line exactly, and the tests pin the
identity: for every row V1 produced, V1.1 produces bit-identical feature values.

Structural cutoffs rather than runtime promises:

* ``premarket_row`` truncates the tape to bars that start at or before 09:24 ET of D (and drops
  every later day) before it computes anything;
* ``daily_eligibility`` refuses arrays that carry a row for session D or later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import warnings

import numpy as np

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e1_premarket import premarket as P
from app.market.calendar import MarketCalendar

UNIVERSE_VERSION = "STRATEGY_E_TRADING_UNIVERSE_V1_1"
#: Research (E1) premarket requirements, unchanged; the 09:30 requirement is gone.
MIN_PREMARKET_BARS = 3
MIN_PREMARKET_DOLLAR_VOLUME = 50_000.0
#: Research RVOL semantics (``premarket.session_rows`` defaults), unchanged.
RVOL_WINDOW = 20
RVOL_MINIMUM = 5
#: Research (E0) daily universe, evaluated at D-1, unchanged apart from the removed next-open test.
DAILY_WINDOW = 20
MIN_CLOSE = 5.0
MIN_MEDIAN_DOLLAR_VOLUME = 5_000_000.0
MIN_PRESENT_SESSIONS = 15


class UniverseContractError(ValueError):
    """Input that would let post-cutoff information reach the decision universe."""


@dataclass(frozen=True)
class PremarketRow:
    session: date
    premarket: dict[str, float]
    derived: dict[str, float]
    pm_rvol: float
    history_sessions: int


def truncate_at_cutoff(tape: SymbolTape, session: date,
                       decision_last: int = P.DECISION_LAST_BAR) -> SymbolTape:
    """Every bar of an earlier day, and bars of D starting at or before 09:24 ET. Nothing else."""
    day = ordinal(session)
    keep = (tape.et_day < day) | ((tape.et_day == day) & (tape.minute <= decision_last))
    return SymbolTape(symbol=tape.symbol, et_day=tape.et_day[keep], minute=tape.minute[keep],
                      open=tape.open[keep], high=tape.high[keep], low=tape.low[keep],
                      close=tape.close[keep], volume=tape.volume[keep], vwap=tape.vwap[keep],
                      sources=tape.sources, overlap_sessions=tape.overlap_sessions)


def premarket_row(tape: SymbolTape, session: date, *,
                  decision_last: int = P.DECISION_LAST_BAR) -> PremarketRow | None:
    """Session D's premarket row as known at 09:25, or ``None`` when D has no premarket bar.

    RVOL denominator, exactly as ``premarket.session_rows``: the median of the last 20 finite,
    positive premarket dollar volumes over *prior* sessions that had both a premarket bar and a
    09:30 bar, undefined below 5 such sessions. Whether a prior session had a 09:30 bar is known
    at D; whether D itself has one is not read, because the tape no longer contains it.
    """
    cut = truncate_at_cutoff(tape, session, decision_last)
    order = np.lexsort((cut.minute, cut.et_day))
    day, minute = cut.et_day[order], cut.minute[order]
    o, h, l, c = cut.open[order], cut.high[order], cut.low[order], cut.close[order]
    v, w = cut.volume[order], cut.vwap[order]
    target = ordinal(session)

    boundaries = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], boundaries]) if day.size else np.array([], dtype=int)
    stops = np.concatenate([boundaries, [day.size]]) if day.size else np.array([], dtype=int)

    history: list[float] = []
    current: dict[str, float] | None = None
    for lo, hi in zip(starts, stops):
        pre = P.premarket_block(minute[lo:hi], o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi],
                                v[lo:hi], w[lo:hi], decision_last)
        if int(day[lo]) == target:
            current = pre
            break
        if pre is None:
            continue
        if P.opening_block(minute[lo:hi], o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi]) is None:
            continue
        value = pre["pm_dollar_volume"]
        if np.isfinite(value) and value > 0:
            history.append(float(value))
    if current is None:
        return None
    if len(history) >= RVOL_MINIMUM:
        median = float(np.median(history[-RVOL_WINDOW:]))
        rvol = (current["pm_dollar_volume"] / median
                if np.isfinite(median) and median > 0 else float("nan"))
    else:
        rvol = float("nan")
    return PremarketRow(session, current, P.derived_features(current), rvol, len(history))


def daily_eligibility(session: date, sessions: Sequence[date], *, close: np.ndarray,
                      volume: np.ndarray, factor: np.ndarray, membership: np.ndarray,
                      split_in_window: np.ndarray,
                      calendar: MarketCalendar | None = None) -> np.ndarray:
    """E0's universe for row D-1, without ``open(D) > 0``. Arrays end at D-1, shape ``(t+1, N)``.

    ``sessions`` labels the rows. Its last entry must be the XNYS session before ``session``: a
    row dated D or later is refused, and so is a panel that stops short of D-1.

    ``split_in_window[j]`` is True when a split of column ``j`` executes in (D-1, D]. It comes
    from a split list published before 09:25 of D (execution dates are announced in advance),
    not from session D's daily bar.
    """
    close = np.asarray(close, dtype=np.float64)
    if close.ndim != 2:
        raise UniverseContractError("daily arrays must be (sessions through D-1, symbols)")
    rows, columns = close.shape
    for name, array in (("volume", volume), ("factor", factor)):
        if np.shape(array) != close.shape:
            raise UniverseContractError(f"{name} must have the shape of close, ending at D-1")
    if np.shape(membership) != (columns,) or np.shape(split_in_window) != (columns,):
        raise UniverseContractError("membership and split flags are one value per symbol at D-1")
    if len(sessions) != rows:
        raise UniverseContractError("one session label per daily row is required")
    previous = (calendar or MarketCalendar("America/New_York")).previous_trading_day(session)
    if any(day >= session for day in sessions):
        raise UniverseContractError(
            f"daily rows dated on or after {session.isoformat()} reached the 09:25 decision")
    if sessions[-1] != previous:
        raise UniverseContractError(
            f"daily context ends at {sessions[-1].isoformat()}, not D-1 = {previous.isoformat()}")
    t = rows - 1
    if t < DAILY_WINDOW:
        return np.zeros(columns, dtype=bool)
    volume = np.asarray(volume, dtype=np.float64)
    factor = np.asarray(factor, dtype=np.float64)
    dollar_volume = close * volume
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)    # all-NaN column -> NaN, as in E0
        median_dv = np.nanmedian(dollar_volume[t - DAILY_WINDOW:t], axis=0)
    present = np.isfinite(dollar_volume[t - DAILY_WINDOW:t]).sum(axis=0)
    prev_price = close[t - 1] / factor[t - 1]
    ok = np.asarray(membership, dtype=bool) & np.isfinite(close[t]) & (close[t] >= MIN_CLOSE)
    ok &= np.isfinite(median_dv) & (median_dv >= MIN_MEDIAN_DOLLAR_VOLUME)
    ok &= present >= MIN_PRESENT_SESSIONS
    ok &= np.isfinite(prev_price) & (prev_price > 0)
    ok &= ~np.asarray(split_in_window, dtype=bool)
    return ok


@dataclass(frozen=True)
class DailyContext:
    """One symbol's D-1 daily inputs, already evaluated by ``daily_eligibility``."""

    eligible: bool
    close_price: float                    # raw close(D-1)
    previous_day_dollar_volume: float     # close(D-1) * volume(D-1)


@dataclass(frozen=True)
class DecisionFrame:
    session: date
    symbols: tuple[str, ...]
    features: dict[str, np.ndarray]
    counters: dict[str, int]


def build_frame(session: date, premarket: Mapping[str, PremarketRow | None],
                daily: Mapping[str, DailyContext], *, spy_premarket_last: float,
                spy_close_previous: float) -> DecisionFrame:
    """Join premarket rows to D-1 daily context, as ``strategy_e1_premarket.dataset.build`` does.

    Only symbols present in ``daily`` can enter. SPY's premarket price is its last trade at or
    before 09:24 (NaN when SPY printed nothing, which is Research's own undefined case); SPY's
    09:30 bar is not read.
    """
    counters = {"daily_context_rows": len(daily), "rejected_daily_eligibility": 0,
                "rejected_no_premarket": 0, "rejected_premarket_bars": 0,
                "rejected_premarket_dollar_volume": 0, "rows": 0}
    kept: list[tuple[str, PremarketRow, DailyContext]] = []
    for symbol in sorted(daily):
        context = daily[symbol]
        if not context.eligible:
            counters["rejected_daily_eligibility"] += 1
            continue
        row = premarket.get(symbol)
        if row is None:
            counters["rejected_no_premarket"] += 1
            continue
        if row.premarket["pm_bars"] < MIN_PREMARKET_BARS:
            counters["rejected_premarket_bars"] += 1
            continue
        if row.premarket["pm_dollar_volume"] < MIN_PREMARKET_DOLLAR_VOLUME:
            counters["rejected_premarket_dollar_volume"] += 1
            continue
        kept.append((symbol, row, context))
    counters["rows"] = len(kept)
    spy_gap = (spy_premarket_last / spy_close_previous - 1.0
               if np.isfinite(spy_premarket_last) and np.isfinite(spy_close_previous)
               and spy_close_previous > 0 else float("nan"))
    columns: dict[str, list[float]] = {name: [] for name in SEALED_FEATURES}
    for _, row, context in kept:
        gap = row.premarket["pm_last_price"] / context.close_price - 1.0
        values = {
            "premarket_gap": gap,
            "premarket_rvol": row.pm_rvol,
            "position_in_premarket_range": row.derived["position_in_premarket_range"],
            "return_0900_0925": row.premarket["return_0900_0925"],
            "return_last30m": row.premarket["return_0855_0925"],
            "relative_strength_vs_spy": gap - spy_gap,
            "premarket_dollar_volume": row.premarket["pm_dollar_volume"],
            "previous_day_dollar_volume": context.previous_day_dollar_volume,
            "close_price": context.close_price,
            "spy_premarket_return": spy_gap,
            "pm_bars": row.premarket["pm_bars"],
        }
        for name in SEALED_FEATURES:
            columns[name].append(float(values[name]))
    return DecisionFrame(session, tuple(symbol for symbol, _, _ in kept),
                         {name: np.array(series, dtype=np.float64)
                          for name, series in columns.items()}, counters)
