"""Trade, fold, year, regime and concentration metrics, and the bootstrap interval.

Definitions come from the frozen D6-C contract section 9. The bootstrap is the same one D5, D5.1
and D5.2 used: daily (sum, count) pairs resampled in 7 day moving blocks, seed 20260928, so the
interval here is comparable with the numbers those studies reported.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from .execution import STOP_EXIT, TIME_EXIT, Trade

DAY_MS = 86_400_000
NOT_MEANINGFUL = "NOT_MEANINGFUL"


def to_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00"))
               .astimezone(timezone.utc).timestamp() * 1000)


def _year(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).year


def _month(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def profit_factor(net: Sequence[float]) -> float | str:
    wins = sum(v for v in net if v > 0)
    losses = -sum(v for v in net if v < 0)
    if losses == 0:
        return "INF" if wins > 0 else NOT_MEANINGFUL
    return wins / losses


def max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough fall of the trade-close equity curve, as a fraction."""
    peak = -float("inf")
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def bootstrap_ci(trades: Sequence[Trade], *, block_days: int, iterations: int,
                 seed: int) -> dict[str, float | None]:
    """95% interval for the mean net_bp, resampling whole 7 day blocks of days.

    Trades inside one day move together, and days inside one block move together, which is how
    the interval stays honest about a strategy that clusters its entries.
    """
    if not trades:
        return {"mean": None, "lo": None, "hi": None, "se": None}
    day_of = {}
    for trade in trades:
        day = trade.entry_ts_ms // DAY_MS
        total, count = day_of.get(day, (0.0, 0))
        day_of[day] = (total + trade.net_bp, count + 1)
    days = sorted(day_of)
    sums = np.array([day_of[d][0] for d in days])
    counts = np.array([day_of[d][1] for d in days], dtype=float)
    n_days = len(days)
    mean = float(sums.sum() / counts.sum())
    if n_days < block_days:
        return {"mean": mean, "lo": None, "hi": None, "se": None}

    rng = np.random.default_rng(seed)
    blocks = n_days - block_days + 1
    draws = int(np.ceil(n_days / block_days))
    samples = np.empty(iterations)
    offsets = np.arange(block_days)
    for i in range(iterations):
        starts = rng.integers(0, blocks, size=draws)
        idx = (starts[:, None] + offsets[None, :]).ravel()[:n_days]
        total = counts[idx].sum()
        samples[i] = sums[idx].sum() / total if total else np.nan
    finite = samples[np.isfinite(samples)]
    return {"mean": mean, "lo": float(np.percentile(finite, 2.5)),
            "hi": float(np.percentile(finite, 97.5)), "se": float(finite.std(ddof=0))}


def summarise(trades: Sequence[Trade], *, starting_equity: float) -> dict[str, Any]:
    """The primary metric block for one set of trades."""
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "gross_pnl_usdt": 0.0,
                "net_pnl_usdt": 0.0, "fees_usdt": 0.0, "funding_usdt": 0.0,
                "profit_factor": NOT_MEANINGFUL, "payoff_ratio": None, "average_trade_bp": None,
                "median_trade_bp": None, "expectancy_bp": None, "mdd": None, "return_pct": 0.0,
                "average_hold_min": None, "stop_exits": 0, "time_exits": 0,
                "gross_bp_mean": None, "edge_to_cost_ratio": None}
    net_usdt = [t.net_usdt for t in trades]
    net_bp = np.array([t.net_bp for t in trades])
    gross_bp = np.array([t.gross_bp for t in trades])
    wins = [v for v in net_usdt if v > 0]
    losses = [v for v in net_usdt if v < 0]
    equity = [t.equity_after for t in trades]
    gross_mean, net_mean = float(gross_bp.mean()), float(net_bp.mean())
    cost = gross_mean - net_mean
    return {
        "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "gross_pnl_usdt": float(sum(t.gross_pnl_usdt for t in trades)),
        "net_pnl_usdt": float(sum(net_usdt)),
        "fees_usdt": float(sum(t.fees_usdt for t in trades)),
        "funding_usdt": float(sum(t.funding_usdt for t in trades)),
        "profit_factor": profit_factor(net_usdt),
        "payoff_ratio": (float(np.mean(wins)) / abs(float(np.mean(losses)))
                         if wins and losses else None),
        "average_trade_bp": net_mean,
        "median_trade_bp": float(np.median(net_bp)),
        "expectancy_bp": net_mean,
        "mdd": max_drawdown([starting_equity] + equity),
        "return_pct": (equity[-1] / starting_equity - 1) * 100 if starting_equity else None,
        "average_hold_min": float(np.mean([t.hold_minutes for t in trades])),
        "stop_exits": sum(1 for t in trades if t.exit_reason == STOP_EXIT),
        "time_exits": sum(1 for t in trades if t.exit_reason == TIME_EXIT),
        "gross_bp_mean": gross_mean,
        "edge_to_cost_ratio": (gross_mean / cost) if cost != 0 else NOT_MEANINGFUL,
    }


def by_fold(trades: Sequence[Trade], boundaries_utc: Sequence[str],
            starting_equity: float) -> list[dict[str, Any]]:
    """Trades attributed to the fold their entry bar falls in."""
    edges = [to_ms(text) for text in boundaries_utc]
    out = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        inside = [t for t in trades if lo <= t.entry_ts_ms < hi]
        days = len({t.entry_ts_ms // DAY_MS for t in inside})
        block = summarise(inside, starting_equity=starting_equity)
        out.append({"fold": f"F{i + 1}", "start_utc": boundaries_utc[i],
                    "end_utc": boundaries_utc[i + 1], "days": days,
                    "judgeable": len(inside) >= 20 and days >= 20,
                    "gross_bp_mean": block["gross_bp_mean"],
                    "net_bp_mean": block["average_trade_bp"], **block})
    return out


def by_year(trades: Sequence[Trade], starting_equity: float) -> list[dict[str, Any]]:
    out = []
    for year in sorted({_year(t.entry_ts_ms) for t in trades}):
        inside = [t for t in trades if _year(t.entry_ts_ms) == year]
        block = summarise(inside, starting_equity=starting_equity)
        out.append({"year": year, **block})
    return out


def trend_label(grid, index: int) -> str:
    """D5 contract section 10: 7 day log return, plus or minus 5%."""
    prior = index - 7 * 1440
    if prior < 0:
        return "UNKNOWN"
    move = float(np.log(grid["close"][index] / grid["close"][prior]))
    if move > 0.05:
        return "BULL"
    if move < -0.05:
        return "BEAR"
    return "SIDEWAYS"


def session_label(ts_ms: int) -> str:
    hour = (ts_ms // 3_600_000) % 24
    return ["00-06", "06-12", "12-18", "18-24"][int(hour // 6)]


def by_regime(trades: Sequence[Trade], grid, starting_equity: float) -> dict[str, Any]:
    vol_names = {0: "LOW", 1: "MID", 2: "HIGH"}
    groups: dict[str, dict[str, list[Trade]]] = {"volatility": {}, "trend": {}, "session": {}}
    for trade in trades:
        groups["volatility"].setdefault(vol_names.get(trade.vol_label, "UNKNOWN"), []).append(trade)
        groups["trend"].setdefault(trend_label(grid, trade.entry_index), []).append(trade)
        groups["session"].setdefault(session_label(trade.entry_ts_ms), []).append(trade)
    return {axis: {label: summarise(rows, starting_equity=starting_equity)
                   for label, rows in sorted(members.items())}
            for axis, members in groups.items()}


def concentration(trades: Sequence[Trade]) -> dict[str, Any]:
    """How much of the result rests on a few trades, a month, or a year.

    Shares are only meaningful when the total is positive; when it is not, the contract says to
    report the absolute figures and mark the share NOT_MEANINGFUL rather than print a ratio whose
    sign flips for the wrong reason.
    """
    if not trades:
        return {"total_net_usdt": 0.0, "meaningful": False}
    net = np.array([t.net_usdt for t in trades])
    total = float(net.sum())
    order = np.argsort(net)[::-1]
    top1 = float(net[order[0]])
    top5 = float(net[order[:5]].sum())
    k = max(1, int(round(len(net) * 0.01)))
    top1pct = float(net[order[:k]].sum())

    months: dict[str, float] = {}
    years: dict[int, float] = {}
    for trade in trades:
        months[_month(trade.entry_ts_ms)] = months.get(_month(trade.entry_ts_ms), 0.0) + trade.net_usdt
        years[_year(trade.entry_ts_ms)] = years.get(_year(trade.entry_ts_ms), 0.0) + trade.net_usdt
    top_month = max(months.items(), key=lambda kv: kv[1])
    top_year = max(years.items(), key=lambda kv: kv[1])

    meaningful = total > 0
    share = (lambda value: value / total if meaningful else NOT_MEANINGFUL)
    return {
        "total_net_usdt": total, "meaningful": meaningful,
        "top_1_trade_usdt": top1, "top_1_trade_share": share(top1),
        "top_5_trades_usdt": top5, "top_5_trades_share": share(top5),
        "top_1_percent_usdt": top1pct, "top_1_percent_share": share(top1pct),
        "top_1_percent_count": k,
        "top_month": top_month[0], "top_month_usdt": top_month[1],
        "top_month_share": share(top_month[1]),
        "top_year": top_year[0], "top_year_usdt": top_year[1],
        "top_year_share": share(top_year[1]),
        "net_excluding_top_1_usdt": total - top1,
        "net_excluding_top_5_usdt": total - top5,
        "year_net_usdt": {str(y): v for y, v in sorted(years.items())},
    }


def leave_one_year_out(trades: Sequence[Trade]) -> dict[str, float]:
    """Mean net_bp with each calendar year removed in turn."""
    years = sorted({_year(t.entry_ts_ms) for t in trades})
    out = {}
    for year in years:
        rest = [t.net_bp for t in trades if _year(t.entry_ts_ms) != year]
        out[str(year)] = float(np.mean(rest)) if rest else float("nan")
    return out


def cost_scenarios(trades: Sequence[Trade], costs: dict[str, Any]) -> dict[str, Any]:
    """The contracted scenarios, applied on top of the engine's own result."""
    if not trades:
        return {}
    stress_extra = 0.0002537159730588128 - 0.0000011841571614073798
    taker = costs["taker_rate"]
    rows = {}
    rows["ZERO"] = float(np.mean([t.gross_bp for t in trades]))
    rows["VIP0_BASE"] = float(np.mean([t.net_bp for t in trades]))
    rows["VIP0_STRESS"] = float(np.mean([
        (t.net_usdt - stress_extra * t.entry_notional) / t.entry_notional * 1e4
        for t in trades]))
    rows["TAKER_PLUS_20PCT"] = float(np.mean([
        (t.net_usdt - 0.2 * taker * (t.entry_notional + t.exit_notional)) / t.entry_notional * 1e4
        for t in trades]))
    return rows
