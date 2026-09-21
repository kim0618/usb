"""Daily-bar TP/SL trade simulation from open(D+1), with gap-through and same-bar ambiguity.

All prices are split-normalized, ``x(t) / F(t)``: ``F(t)`` holds splits executed on or before
session ``t``, which a trader at ``t`` knows, so the path of one position is on one basis even
across a split. The entry is ``open(D+1)``; nothing at or before D is read. ``simulate_one`` is
the scalar reference written straight from the declaration; ``simulate`` is the vectorised
version, and the run requires the two to agree on a sample (the differential PIT check).

Per session D+1..D+H (D+1 is the entry session, so it has no gap rule):

1. no bar -> no exit possible, continue;
2. D+2 onward: open <= SL exits at the open (SL gap-through), else open >= TP exits at the open;
3. intraday: high >= TP and low <= SL is AMBIGUOUS_SAME_BAR (exit at SL when conservative, at
   TP when optimistic); low <= SL exits at SL; high >= TP exits at TP;
4. still open at D+H: exit at close(D+H) (NEITHER).

Still open without a bar on D+H: the first later bar's open if the ticker trades again (gap rules
apply), otherwise the last available close times ``1 - haircut`` (delisted / suspended).
"""

from dataclasses import dataclass

import numpy as np

OPEN, TP_FIRST, SL_FIRST, NEITHER, AMBIGUOUS = 0, 1, 2, 3, 4
STATE_NAMES = {TP_FIRST: "TP_FIRST", SL_FIRST: "SL_FIRST", NEITHER: "NEITHER", AMBIGUOUS: "AMBIGUOUS_SAME_BAR"}


@dataclass(frozen=True)
class Prices:
    """Split-normalized (T, N) arrays and the bar-presence helpers the exits need."""

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    has_bar: np.ndarray
    next_bar: np.ndarray  # (T, N): smallest t' >= t with a bar, T when none
    prev_bar: np.ndarray  # (T, N): largest t' <= t with a bar, -1 when none

    @classmethod
    def from_panel(cls, panel) -> "Prices":
        factor, _, _ = panel.split_arrays()
        return cls.build(panel.open / factor, panel.high / factor, panel.low / factor, panel.close / factor)

    @classmethod
    def build(cls, o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> "Prices":
        has_bar = ~(np.isnan(o) | np.isnan(h) | np.isnan(lo) | np.isnan(c))
        t, n = c.shape
        nxt = np.full((t, n), t, dtype=np.int32)
        prv = np.full((t, n), -1, dtype=np.int32)
        running = np.full(n, t, dtype=np.int32)
        for i in range(t - 1, -1, -1):
            running = np.where(has_bar[i], i, running)
            nxt[i] = running
        running = np.full(n, -1, dtype=np.int32)
        for i in range(t):
            running = np.where(has_bar[i], i, running)
            prv[i] = running
        return cls(o, h, lo, c, has_bar, nxt, prv)


@dataclass(frozen=True)
class Trades:
    """One row per simulated position (entry-valid rows only)."""

    entry: np.ndarray
    exit_price: np.ndarray
    exit_idx: np.ndarray
    state: np.ndarray
    gap: np.ndarray        # exit at an open through a level
    stop_exit: np.ndarray  # SL_FIRST, or AMBIGUOUS resolved as SL
    late_exit: np.ndarray
    delisted: np.ndarray
    missing_sessions: np.ndarray

    @property
    def gross(self) -> np.ndarray:
        return self.exit_price / self.entry - 1.0


def simulate(prices: Prices, date_idx: np.ndarray, ticker_idx: np.ndarray, tp: float, sl: float,
             horizon: int, *, conservative: bool = True, haircut: float = 0.30) -> Trades:
    """Rows must have an entry bar (open(D+1) > 0) and D + horizon <= T - 1."""
    t_len = prices.close.shape[0]
    ii, jj = np.asarray(date_idx, dtype=np.int64), np.asarray(ticker_idx, dtype=np.int64)
    if len(ii) and (ii + horizon).max() > t_len - 1:
        raise ValueError("a row's holding window runs past the panel")
    entry = prices.open[ii + 1, jj]
    if len(ii) and not (np.isfinite(entry).all() and (entry > 0).all()):
        raise ValueError("simulate() needs a valid entry bar on D+1 for every row")
    tp_px, sl_px = entry * (1.0 + tp), entry * (1.0 - sl)
    n = len(ii)
    state = np.zeros(n, dtype=np.int8)
    exit_px = np.full(n, np.nan)
    exit_idx = np.full(n, -1, dtype=np.int64)
    gap = np.zeros(n, dtype=bool)
    missing = np.zeros(n, dtype=np.int16)

    def close_out(mask, px, st, idx, is_gap=False):
        state[mask] = st
        exit_px[mask] = px[mask]
        exit_idx[mask] = idx[mask]
        if is_gap:
            gap[mask] = True

    for j in range(1, horizon + 1):
        t = ii + j
        o, h, lo, c = (prices.open[t, jj], prices.high[t, jj], prices.low[t, jj], prices.close[t, jj])
        bar = prices.has_bar[t, jj]
        live = state == OPEN
        missing += (live & ~bar).astype(np.int16)
        active = live & bar
        if j >= 2:
            gap_sl = active & (o <= sl_px)
            close_out(gap_sl, o, SL_FIRST, t, True)
            gap_tp = active & ~gap_sl & (o >= tp_px)
            close_out(gap_tp, o, TP_FIRST, t, True)
            active = active & ~gap_sl & ~gap_tp
        hit_tp, hit_sl = active & (h >= tp_px), active & (lo <= sl_px)
        both = hit_tp & hit_sl
        close_out(both, sl_px if conservative else tp_px, AMBIGUOUS, t)
        close_out(hit_sl & ~both, sl_px, SL_FIRST, t)
        close_out(hit_tp & ~both, tp_px, TP_FIRST, t)
        if j == horizon:
            close_out(active & ~hit_tp & ~hit_sl, c, NEITHER, t)

    late = np.zeros(n, dtype=bool)
    delisted = np.zeros(n, dtype=bool)
    rest = np.nonzero(state == OPEN)[0]
    if len(rest):
        after = ii[rest] + horizon + 1
        nxt = np.where(after <= t_len - 1, prices.next_bar[np.minimum(after, t_len - 1), jj[rest]], t_len)
        trades_again = nxt < t_len
        for k, row in enumerate(rest):
            if trades_again[k]:
                ti = int(nxt[k])
                o = prices.open[ti, jj[row]]
                exit_idx[row] = ti
                exit_px[row] = o
                if o <= sl_px[row]:
                    state[row], gap[row] = SL_FIRST, True
                elif o >= tp_px[row]:
                    state[row], gap[row] = TP_FIRST, True
                else:
                    state[row], late[row] = NEITHER, True
            else:
                last = int(prices.prev_bar[ii[row] + horizon, jj[row]])
                exit_px[row] = prices.close[last, jj[row]] * (1.0 - haircut)
                exit_idx[row] = ii[row] + horizon
                state[row], delisted[row] = NEITHER, True
    stop_exit = (state == SL_FIRST) | ((state == AMBIGUOUS) & conservative)
    return Trades(entry, exit_px, exit_idx, state, gap, stop_exit, late, delisted, missing)


def simulate_one(o, h, lo, c, tp: float, sl: float, horizon: int, *, conservative: bool = True,
                 haircut: float = 0.30) -> tuple[float, int, int, bool]:
    """Scalar reference. Arrays start at session D+1 and run to the end of the panel.

    Returns (exit price, state, exit offset from D+1 (0-based), gap flag).
    """
    entry = o[0]
    tp_px, sl_px = entry * (1.0 + tp), entry * (1.0 - sl)
    last_seen = None
    for k in range(len(c)):
        bar = not (np.isnan(o[k]) or np.isnan(h[k]) or np.isnan(lo[k]) or np.isnan(c[k]))
        if not bar:
            continue
        last_seen = k
        if k >= horizon:  # first bar after the holding window: late exit at its open
            if o[k] <= sl_px:
                return o[k], SL_FIRST, k, True
            if o[k] >= tp_px:
                return o[k], TP_FIRST, k, True
            return o[k], NEITHER, k, False
        if k >= 1:
            if o[k] <= sl_px:
                return o[k], SL_FIRST, k, True
            if o[k] >= tp_px:
                return o[k], TP_FIRST, k, True
        up, down = h[k] >= tp_px, lo[k] <= sl_px
        if up and down:
            return (sl_px if conservative else tp_px), AMBIGUOUS, k, False
        if down:
            return sl_px, SL_FIRST, k, False
        if up:
            return tp_px, TP_FIRST, k, False
        if k == horizon - 1:
            return c[k], NEITHER, k, False
    last = max(k for k in range(min(len(c), horizon)) if not np.isnan(c[k])) if last_seen is not None else 0
    return c[last] * (1.0 - haircut), NEITHER, horizon - 1, False
