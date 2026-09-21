"""Research execution of ``D_AGG_BRACKET_10_10_H5_V1`` (d_agg_trading_rules_v1.json), vectorised.

One long trade per ``(session D, ticker)`` row whose D-AGG-1 excursion is valid:

    P0      = O(D+1)/F(D+1); no TP/SL test at the entry print
    D+1     intraday: high TP and low SL both touched -> SL (SL FIRST); low only -> SL; high only -> TP
    D+2..5  in order, only on sessions with a bar: open touches SL -> exit at that open (GAP_SL);
            open touches TP -> exit at that open (GAP_TP); otherwise the D+1 intraday rule
    end     no level touched -> C(D+5)/F(D+5) (TIME)
    touch   price/P0 - 1 >= +0.10 - 1e-12 (TP), <= -0.10 + 1e-12 (SL)
    net     gross - round-trip cost (STRATEGY_E_COST_RULES_V1 TOTAL_ROUND_TRIP_SUBTRACTION_V1)

A row without a valid excursion is NO_TRADE; no exit price is invented. Sessions without a bar
inside the window are skipped and never replaced by D+6. This module knows no signal, no
selection and no control: it prices rows it is given.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_agg.config import THRESHOLD_EPS
from app.backtest.strategy_d_agg.models import HardFail

RULE_ID = "D_AGG_BRACKET_10_10_H5_V1"
TP_RETURN = 0.10
SL_RETURN = -0.10
HORIZON = 5
PRIMARY_COST = 0.0010
EXIT_REASONS = ("NO_TRADE", "TP", "SL", "SAME_BAR_SL", "GAP_TP", "GAP_SL", "TIME", "SAME_BAR_TP")


@dataclass(frozen=True)
class Trades:
    entry_price: np.ndarray    # P0, NaN on NO_TRADE
    exit_price: np.ndarray     # adjusted exit price, NaN on NO_TRADE
    exit_offset: np.ndarray    # k in 1..5 (exit session = D+k), 0 on NO_TRADE
    reason: np.ndarray         # int8 index into EXIT_REASONS
    gross: np.ndarray          # NaN on NO_TRADE

    def net(self, cost: float) -> np.ndarray:
        return self.gross - cost


def adjusted(panel: Panel) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    factor = panel.split_arrays()[0]
    return panel.open / factor, panel.high / factor, panel.low / factor, panel.close / factor


def execute(session_idx: np.ndarray, ticker_col: np.ndarray, valid: np.ndarray,
            prices: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], *,
            same_bar: str = "SL_FIRST", brackets: bool = True) -> Trades:
    """Trades for the given rows. ``same_bar`` is 'SL_FIRST' (primary) or 'TP_FIRST' (S2);
    ``brackets=False`` is the time-exit-only secondary S1."""
    if same_bar not in ("SL_FIRST", "TP_FIRST"):
        raise HardFail("R1", f"same_bar {same_bar!r}")
    o_all, h_all, l_all, c_all = prices
    if int(session_idx.max(initial=0)) + HORIZON >= o_all.shape[0]:
        raise HardFail("R5", "a trade window reaches past the panel")
    s, j = session_idx, ticker_col
    p0 = o_all[s + 1, j]
    n = s.size
    gross = np.full(n, np.nan)
    exit_price = np.full(n, np.nan)
    offset = np.zeros(n, dtype=np.int8)
    reason = np.zeros(n, dtype=np.int8)

    def close_out(mask, price, k, code):
        exit_price[mask] = price[mask]
        gross[mask] = price[mask] / p0[mask] - 1.0
        offset[mask] = k
        reason[mask] = code

    for k in range(1, HORIZON + 1):
        o, h, l, c = o_all[s + k, j], h_all[s + k, j], l_all[s + k, j], c_all[s + k, j]
        live = np.isnan(gross) & valid & ~np.isnan(c)
        if not brackets:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            ro, rh, rl = o / p0 - 1.0, h / p0 - 1.0, l / p0 - 1.0
        tp_level, sl_level = p0 * (1.0 + TP_RETURN), p0 * (1.0 + SL_RETURN)
        if k >= 2:
            gap_sl = live & (ro <= SL_RETURN + THRESHOLD_EPS)
            close_out(gap_sl, o, k, 5)
            live &= ~gap_sl
            gap_tp = live & (ro >= TP_RETURN - THRESHOLD_EPS)
            close_out(gap_tp, o, k, 4)
            live &= ~gap_tp
        hit_sl = live & (rl <= SL_RETURN + THRESHOLD_EPS)
        hit_tp = live & (rh >= TP_RETURN - THRESHOLD_EPS)
        both = hit_sl & hit_tp
        if same_bar == "SL_FIRST":
            close_out(both, sl_level, k, 3)
            close_out(hit_sl & ~both, sl_level, k, 2)
            close_out(hit_tp & ~both, tp_level, k, 1)
        else:
            close_out(both, tp_level, k, 7)
            close_out(hit_tp & ~both, tp_level, k, 1)
            close_out(hit_sl & ~both, sl_level, k, 2)
    c5 = c_all[s + HORIZON, j]
    time_exit = np.isnan(gross) & valid
    close_out(time_exit, c5, HORIZON, 6)
    # level exits are priced exactly at the level: gross = +/-0.10 by construction
    level = np.isin(reason, (1, 2, 3, 7))
    gross[level & np.isin(reason, (1, 7))] = TP_RETURN
    gross[level & np.isin(reason, (2, 3))] = SL_RETURN
    if not np.isfinite(gross[valid]).all():
        raise HardFail("R5", "a valid row produced no finite trade return")
    if np.isfinite(gross[~valid]).any():
        raise HardFail("R5", "an invalid row produced a trade")
    return Trades(np.where(valid, p0, np.nan), exit_price, offset, reason, gross)
