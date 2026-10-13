"""Phase 1: what the ledger says about the manual trading, before any behaviour is read into it.

Two numbers are kept apart throughout. Lifetime covers every completed trade since RUN_START.
Current segment covers only the trades after the last `ACCOUNT_RESET`, and the money a reset adds
is never counted as profit. Mixing them is the easiest way to mistake a top-up for a win.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Sequence

from .ledger import ManualTrade, Reconstruction

NOT_MEANINGFUL = "NOT_MEANINGFUL"


def _hour(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).hour


def profit_factor(net: Sequence[float]) -> float | str:
    wins = sum(v for v in net if v > 0)
    losses = -sum(v for v in net if v < 0)
    if losses == 0:
        return "INF" if wins > 0 else NOT_MEANINGFUL
    return wins / losses


def max_drawdown(equity: Sequence[float]) -> float | None:
    if not equity:
        return None
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def streaks(trades: Sequence[ManualTrade]) -> dict[str, int]:
    best_win = best_loss = win = loss = 0
    for trade in trades:
        if trade.is_win:
            win, loss = win + 1, 0
        else:
            loss, win = loss + 1, 0
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return {"max_consecutive_wins": best_win, "max_consecutive_losses": best_loss}


def summarise(trades: Sequence[ManualTrade], *, starting_equity: float | None = None
              ) -> dict[str, Any]:
    """Every Phase 1 figure for one set of trades. Costs are never netted twice."""
    if not trades:
        return {"trades": 0, "note": "no completed trades in this set"}

    net = [t.net_pnl for t in trades]
    gross = [t.gross_pnl for t in trades]
    wins = [v for v in net if v > 0]
    losses = [v for v in net if v < 0]
    holds = [t.hold_ms / 60_000 for t in trades]
    equity = ([starting_equity] if starting_equity is not None else []) + \
        [t.equity_after for t in trades]

    return {
        "trades": len(trades),
        "long": sum(1 for t in trades if t.side == "LONG"),
        "short": sum(1 for t in trades if t.side == "SHORT"),
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "gross_pnl": sum(gross),
        "entry_fees": sum(t.entry_fee for t in trades),
        "exit_fees": sum(t.exit_fee for t in trades),
        "total_fees": sum(t.fees for t in trades),
        "funding": sum(t.funding for t in trades),
        "net_pnl": sum(net),
        "average_winner": (sum(wins) / len(wins)) if wins else None,
        "average_loser": (sum(losses) / len(losses)) if losses else None,
        "payoff_ratio": (abs((sum(wins) / len(wins)) / (sum(losses) / len(losses)))
                         if wins and losses else None),
        "profit_factor": profit_factor(net),
        "expectancy_per_trade": sum(net) / len(trades),
        "median_trade": sorted(net)[len(net) // 2],
        "best_trade": max(net), "worst_trade": min(net),
        "gross_bp_mean": sum(t.gross_bp for t in trades) / len(trades),
        "net_bp_mean": sum(t.net_bp for t in trades) / len(trades),
        "mdd": max_drawdown(equity),
        "average_hold_min": sum(holds) / len(holds),
        "median_hold_min": sorted(holds)[len(holds) // 2],
        "total_notional": sum(t.entry_notional for t in trades),
        "liquidations": sum(1 for t in trades if t.liquidated),
        **streaks(trades),
    }


def by_side(trades: Sequence[ManualTrade]) -> dict[str, Any]:
    return {side: summarise([t for t in trades if t.side == side])
            for side in ("LONG", "SHORT")}


def by_leverage(trades: Sequence[ManualTrade]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for leverage in sorted({t.leverage for t in trades}):
        out[f"{leverage:g}x"] = summarise([t for t in trades if t.leverage == leverage])
    return out


def by_hour(trades: Sequence[ManualTrade]) -> dict[str, Any]:
    counts = Counter(_hour(t.opened_ts_ms) for t in trades)
    nets: dict[int, float] = {}
    for trade in trades:
        hour = _hour(trade.opened_ts_ms)
        nets[hour] = nets.get(hour, 0.0) + trade.net_pnl
    return {str(hour): {"trades": counts[hour], "net_pnl": nets[hour]}
            for hour in sorted(counts)}


def concentration(trades: Sequence[ManualTrade]) -> dict[str, Any]:
    """How much of the result rests on a few trades.

    Shares are only reported when the total is positive; a ratio against a negative total flips
    sign for the wrong reason and reads as the opposite of what it means.
    """
    if not trades:
        return {"trades": 0}
    net = sorted((t.net_pnl for t in trades), reverse=True)
    total = sum(net)
    meaningful = total > 0
    share = (lambda value: value / total if meaningful else NOT_MEANINGFUL)
    top_n = {f"top_{k}": sum(net[:k]) for k in (1, 2, 3, 5)}
    decile = max(1, round(len(net) * 0.10))
    return {
        "total_net": total, "meaningful": meaningful,
        **top_n,
        **{f"top_{k}_share": share(top_n[f"top_{k}"]) for k in (1, 2, 3, 5)},
        "top_10_percent": sum(net[:decile]), "top_10_percent_count": decile,
        "top_10_percent_share": share(sum(net[:decile])),
        "net_excluding_top_1": total - net[0],
        "net_excluding_top_2": total - sum(net[:2]),
        "net_excluding_top_3": total - sum(net[:3]),
        "net_excluding_top_5": total - sum(net[:5]),
        "worst_trade": net[-1],
        "net_excluding_worst_1": total - net[-1],
    }


def fee_burden(trades: Sequence[ManualTrade]) -> dict[str, Any]:
    """Was the edge big enough to clear the round trip, or did the fee eat it?"""
    if not trades:
        return {"trades": 0}
    gross_bp = sum(t.gross_bp for t in trades) / len(trades)
    net_bp = sum(t.net_bp for t in trades) / len(trades)
    cost_bp = gross_bp - net_bp
    return {
        "gross_bp_per_trade": gross_bp,
        "cost_bp_per_trade": cost_bp,
        "net_bp_per_trade": net_bp,
        "gross_over_cost": (gross_bp / cost_bp) if cost_bp else NOT_MEANINGFUL,
        "trades_where_gross_cleared_cost": sum(1 for t in trades if t.gross_bp > (t.gross_bp - t.net_bp)),
        "trades_where_gross_positive": sum(1 for t in trades if t.gross_bp > 0),
        "total_fees": sum(t.fees for t in trades),
        "total_gross": sum(t.gross_pnl for t in trades),
        "fees_over_gross": (sum(t.fees for t in trades) / sum(t.gross_pnl for t in trades)
                            if sum(t.gross_pnl for t in trades) > 0 else NOT_MEANINGFUL),
    }


def leverage_vs_direction(trades: Sequence[ManualTrade]) -> dict[str, Any]:
    """Separate "picked the direction" from "used a big multiplier".

    `gross_bp` is the price move the trade captured and does not depend on leverage at all.
    Account impact does. Reporting both is what tells the two apart.
    """
    if not trades:
        return {"trades": 0}
    rows = []
    for trade in trades:
        margin = trade.entry_notional / trade.leverage if trade.leverage else None
        rows.append({
            "index": trade.index, "side": trade.side, "leverage": trade.leverage,
            "price_move_bp": trade.gross_bp,
            "net_bp_on_notional": trade.net_bp,
            "net_over_margin_pct": (trade.net_pnl / margin * 100) if margin else None,
            "one_x_equivalent_net": trade.net_pnl / trade.leverage if trade.leverage else None,
        })
    return {
        "per_trade": rows,
        "mean_price_move_bp": sum(t.gross_bp for t in trades) / len(trades),
        "one_x_equivalent_net_total": sum(t.net_pnl / t.leverage for t in trades if t.leverage),
        "note": "price_move_bp is leverage free; leverage scales the account impact and the fee",
    }


def exposure(trades: Sequence[ManualTrade], reconstruction: Reconstruction,
             snapshot_ts_ms: int) -> dict[str, Any]:
    """How selective the human is: how much of the wall clock carried a position."""
    if reconstruction.run_start_ms is None:
        return {}
    span_ms = snapshot_ts_ms - reconstruction.run_start_ms
    in_position = sum(t.hold_ms for t in trades)
    open_ms = 0
    if reconstruction.open_position:
        open_ms = snapshot_ts_ms - reconstruction.open_position["opened_ts_ms"]
    days = span_ms / 86_400_000
    return {
        "run_start_utc": datetime.fromtimestamp(reconstruction.run_start_ms / 1000,
                                                tz=timezone.utc).isoformat(),
        "span_days": days,
        "completed_trades": len(trades),
        "trades_per_day": len(trades) / days if days else None,
        "time_in_position_ms": in_position,
        "open_position_ms": open_ms,
        "exposure_fraction_closed_only": in_position / span_ms if span_ms else None,
        "exposure_fraction_including_open": (in_position + open_ms) / span_ms if span_ms else None,
        "position_free_fraction": 1 - ((in_position + open_ms) / span_ms) if span_ms else None,
    }
