"""Trade economics, the sleeve portfolio, streaks, concentration and the loss tail.

Every function takes a trade frame with at least ``date_idx``, ``ticker``, ``exit_idx``,
``state``, ``net`` (and ``gross``, ``stop_exit``, ``gap``, ``delisted`` where used). The frame is
never mutated.
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

from app.backtest.strategy_c_attack.simulate import AMBIGUOUS, NEITHER, SL_FIRST, TP_FIRST


def _f(x) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def profit_factor(net: pd.Series) -> float | None:
    gains, losses = net[net > 0].sum(), -net[net < 0].sum()
    if losses == 0:
        return None if gains == 0 else float("inf")
    return float(gains / losses)


def ordered(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(["exit_idx", "date_idx", "ticker"], kind="mergesort")


def streaks(net: Sequence[float]) -> tuple[int, int]:
    """(longest losing streak, longest winning streak); a win is net > 0."""
    lose = win = best_lose = best_win = 0
    for value in net:
        if value > 0:
            win, lose = win + 1, 0
        else:
            lose, win = lose + 1, 0
        best_lose, best_win = max(best_lose, lose), max(best_win, win)
    return best_lose, best_win


def sleeve_equity(frame: pd.DataFrame, sleeves: int) -> pd.Series:
    """Non-compounding: each signal date's trades share 1/sleeves of capital 1.0, P&L on exit."""
    if frame.empty:
        return pd.Series(dtype=float)
    per_date = frame.groupby("date_idx")["net"].transform("size")
    pnl = frame["net"] / per_date / sleeves
    booked = pnl.groupby(frame["exit_idx"]).sum()
    start, end = int(frame["date_idx"].min()) + 1, int(frame["exit_idx"].max())
    booked = booked.reindex(range(start, end + 1), fill_value=0.0)
    return 1.0 + booked.cumsum()


def max_drawdown(equity: pd.Series) -> float | None:
    if equity.empty:
        return None
    path = np.concatenate([[1.0], equity.to_numpy()])
    peak = np.maximum.accumulate(path)
    return float(-(path / peak - 1.0).min())


def top_contribution(net: pd.Series, k: int) -> float | None:
    total = net.sum()
    if total <= 0:
        return None
    return float(net.sort_values(ascending=False).head(k).sum() / total)


def summarize(frame: pd.DataFrame, sleeves: int) -> dict:
    n = len(frame)
    if n == 0:
        return {"n": 0}
    net, state = frame["net"], frame["state"]
    wins, losses = net[net > 0], net[net <= 0]
    lose_streak, win_streak = streaks(ordered(frame)["net"].tolist())
    equity = sleeve_equity(frame, sleeves)
    trimmed = net.sort_values().iloc[int(0.05 * n): n - int(0.05 * n)]
    out = {
        "n": int(n), "unique_tickers": int(frame["ticker"].nunique()),
        "win_rate": float((net > 0).mean()),
        "tp_first_rate": float((state == TP_FIRST).mean()),
        "sl_first_rate": float((state == SL_FIRST).mean()),
        "neither_rate": float((state == NEITHER).mean()),
        "ambiguous_rate": float((state == AMBIGUOUS).mean()),
        "gap_through_sl_rate": float(((state == SL_FIRST) & frame["gap"]).mean()),
        "gap_through_tp_rate": float(((state == TP_FIRST) & frame["gap"]).mean()),
        "delisted_rate": float(frame["delisted"].mean()),
        "avg_winner": _f(wins.mean()) if len(wins) else None,
        "avg_loser": _f(losses.mean()) if len(losses) else None,
        "mean": float(net.mean()), "median": float(net.median()), "expectancy": float(net.mean()),
        "trimmed_mean_5": float(trimmed.mean()) if len(trimmed) else None,
        "gross_mean": float(frame["gross"].mean()),
        "profit_factor": profit_factor(net),
        "total_net_pnl_units": float(net.sum()),
        "sleeve_cumulative_return": float(equity.iloc[-1] - 1.0),
        "sleeve_max_drawdown": max_drawdown(equity),
        "longest_losing_streak": lose_streak, "longest_winning_streak": win_streak,
        "top1_contribution": top_contribution(net, 1), "top5_contribution": top_contribution(net, 5),
        "top10_contribution": top_contribution(net, 10),
    }
    return out


def loss_tail(frame: pd.DataFrame, sl: float) -> dict:
    net, gross = frame["net"], frame["gross"]
    stops = frame[frame["stop_exit"]]
    beyond = stops["gross"] + sl  # < 0 means the realized loss exceeded the nominal stop
    gap_sl = frame[(frame["state"] == SL_FIRST) & frame["gap"]]
    return {
        "worst_net": float(net.min()), "worst_gross": float(gross.min()),
        "p1_net": float(net.quantile(0.01)), "p5_net": float(net.quantile(0.05)),
        "stop_type_exits": int(len(stops)),
        "gap_through_sl_count": int(len(gap_sl)),
        "gap_through_sl_mean_gross": _f(gap_sl["gross"].mean()) if len(gap_sl) else None,
        "loss_beyond_nominal_sl_count": int((beyond < -1e-12).sum()),
        "loss_beyond_nominal_sl_mean": _f(beyond[beyond < -1e-12].mean()) if (beyond < -1e-12).any() else 0.0,
        "loss_beyond_nominal_sl_total_units": float(beyond[beyond < -1e-12].sum()),
        "delisted_count": int(frame["delisted"].sum()),
    }


def concurrency(frame: pd.DataFrame, n_sessions: int) -> dict:
    signals = frame.groupby("date_idx").size()
    delta = np.zeros(n_sessions + 2)
    np.add.at(delta, frame["date_idx"].to_numpy() + 1, 1)
    np.add.at(delta, frame["exit_idx"].to_numpy() + 1, -1)
    open_positions = np.cumsum(delta)[:n_sessions]
    first, last = int(frame["date_idx"].min()) + 1, int(frame["exit_idx"].max())
    span = open_positions[first:last + 1]
    return {"signals_per_day_mean": float(signals.mean()), "signals_per_day_median": float(signals.median()),
            "signals_per_day_max": int(signals.max()), "signal_days": int(len(signals)),
            "open_positions_max": int(span.max()), "open_positions_median": float(np.median(span)),
            "open_positions_p95": float(np.percentile(span, 95))}


def attack_portfolio(frame: pd.DataFrame, sl: float, risk: float, capital: float = 100.0) -> dict:
    """Descriptive compounding simulation: notional = risk * equity / sl, gross exposure <= equity."""
    entries = frame.assign(entry_idx=frame["date_idx"] + 1).sort_values(["entry_idx", "date_idx", "ticker"],
                                                                        kind="mergesort")
    by_entry = {k: g for k, g in entries.groupby("entry_idx")}
    exits: dict[int, list[tuple[float, float]]] = {}
    equity, exposure, taken, skipped = capital, 0.0, 0, 0
    curve = []
    for t in range(int(entries["entry_idx"].min()), int(entries["exit_idx"].max()) + 1):
        for _, row in (by_entry.get(t, pd.DataFrame()).iterrows()):
            notional = risk * equity / sl
            if exposure + notional > equity:
                skipped += 1
                continue
            exposure += notional
            taken += 1
            exits.setdefault(int(row["exit_idx"]), []).append((notional, float(row["net"])))
        for notional, net in exits.pop(t, []):
            exposure -= notional
            equity += notional * net
        curve.append(equity)
    path = pd.Series(curve)
    return {"capital_start": capital, "capital_end": float(equity), "return": float(equity / capital - 1.0),
            "max_drawdown": max_drawdown(path / capital), "trades_taken": taken, "trades_skipped_exposure": skipped,
            "risk_per_trade": risk, "note": "DESCRIPTIVE, never a gate input"}
