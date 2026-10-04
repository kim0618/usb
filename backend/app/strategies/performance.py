"""Per-strategy performance, the A+E combined column and the first portfolio simulation baseline.

Everything here is a pure function of canonical ledger rows (``app.strategies.ledger``) and daily
equity points, so it is tested without a database or runtime files.

Definitions, fixed here so a screen never recomputes them its own way:

* **Trades** = closed trades whose ``net_pnl`` is known. An E trade whose session does not reconcile
  with its book has no per-trade net and is counted in ``unreconciled_trades`` instead.
* **Gross / Costs / Net** = sums over those trades, as the ledger recorded them. ``accounting``
  names the convention (see ledger.py); rows under different conventions are never summed silently:
  the mix is reported in ``accounting_mix``.
* **Return** = Net / initial equity (realised, ledger-based). The equity-based change is reported
  beside it as ``equity_change`` with the gap, because the two disagree under the V0 convention.
* **Win rate** = trades with net > 0 / trades. **PF** = sum of winning net / |sum of losing net|,
  ``None`` with a reason when there is no losing trade. **Expectancy** = Net / Trades.
* **MDD** = the largest peak-to-trough fall of the daily equity series (starting from the initial
  equity), as a fraction of the peak and in currency.
* **Avg MFE / MAE** = ``None``: neither ledger records the intratrade path.
* A metric without a sample is ``None`` with a reason in ``na``; it is never shown as 0.

Combined (A+E) pools the trades of both books and adds their daily PnL on the sum of their initial
equity. It is an accounting sum of two separate books, not an allocation.

The portfolio baseline is a simulation only: a 50/50 *risk budget* blend of the two strategies'
daily returns, on the dates both were operating. It is not used to size or route anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from math import sqrt
from typing import Any, Iterable, Mapping, Sequence

ZERO = Decimal(0)
MIN_CORRELATION_DAYS = 5


@dataclass(frozen=True)
class DailyPoint:
    day: str                  # ISO session date
    pnl: Decimal              # the day's change in the book's equity
    equity: Decimal           # the book's equity after the day


@dataclass
class Book:
    strategy_id: str
    initial_equity: Decimal | None
    trades: list[Mapping[str, Any]]
    daily: list[DailyPoint]
    operating_sessions: list[str] = field(default_factory=list)
    open_positions: list[Mapping[str, Any]] = field(default_factory=list)


def _d(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


MONEY_PLACES, RATIO_PLACES = 4, 6


def _s(value: Decimal | float | None, places: int = RATIO_PLACES) -> str | None:
    """Fixed-point text (never ``0E+8``). Money is shown to 4 places and ratios to 6; the full
    precision stays in the ledgers, and this rounding is display-only."""
    if value is None:
        return None
    return f"{Decimal(str(value)).quantize(Decimal(1).scaleb(-places)):f}"


def _m(value: Decimal | None) -> str | None:
    return _s(value, MONEY_PLACES)


def money(value: Decimal | None) -> str | None:
    """The shared display rounding for a money figure, so no screen invents its own.

    It also keeps a Decimal zero out of exponent form: ``Decimal("0E-24")`` is a legitimate zero but
    reads as a parse accident on a screen, and `0.0000` says the same thing without the question.
    """
    return _m(value)


def closed_with_net(trades: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [t for t in trades if t.get("status") == "CLOSED" and t.get("net_pnl") is not None]


def drawdown(initial: Decimal | None, daily: Sequence[DailyPoint]) -> tuple[Decimal | None, Decimal | None]:
    """(MDD as a fraction of the peak, MDD in currency), both <= 0; ``None`` without a series."""
    if initial is None or not daily:
        return None, None
    peak = initial
    worst_fraction, worst_amount = ZERO, ZERO
    for point in daily:
        peak = max(peak, point.equity)
        fall = point.equity - peak
        if fall < worst_amount:
            worst_amount = fall
        if peak > 0 and fall / peak < worst_fraction:
            worst_fraction = fall / peak
    return worst_fraction, worst_amount


def strategy_metrics(book: Book) -> dict[str, Any]:
    all_closed = [t for t in book.trades if t.get("status") == "CLOSED"]
    closed = closed_with_net(book.trades)
    na: dict[str, str] = {}
    nets = [_d(t["net_pnl"]) for t in closed]
    gross = [_d(t.get("gross_pnl")) for t in closed]
    costs = [_d(t.get("costs")) for t in closed]
    n = len(closed)
    net_total = sum(nets, ZERO) if n else None
    gross_total = sum((g for g in gross if g is not None), ZERO) if n else None
    cost_total = sum((c for c in costs if c is not None), ZERO) if n else None
    if not n:
        for key in ("gross_pnl", "costs", "net_pnl", "return", "win_rate", "pf", "expectancy", "avg_holding_seconds"):
            na[key] = "NO_CLOSED_TRADE"
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    pf = None
    if n and losses:
        pf = sum(wins, ZERO) / -sum(losses, ZERO)
    elif n:
        na["pf"] = "NO_LOSING_TRADE"
    initial = book.initial_equity
    ret = None
    if n and initial:
        ret = net_total / initial
    elif n:
        na["return"] = "NO_INITIAL_EQUITY"
    mdd, mdd_amount = drawdown(initial, book.daily)
    if mdd is None:
        na["mdd"] = "NO_DAILY_SERIES"
    holdings = [t["holding_seconds"] for t in closed if t.get("holding_seconds") is not None]
    equity_change = (book.daily[-1].equity - initial) if (book.daily and initial is not None) else None
    na["avg_mfe"] = na["avg_mae"] = "NOT_RECORDED_BY_LEDGER"
    return {
        "strategy_id": book.strategy_id,
        "trades": n,
        "unreconciled_trades": len(all_closed) - n,
        "operating_sessions": len(book.operating_sessions) or len(book.daily),
        "gross_pnl": _m(gross_total), "costs": _m(cost_total), "net_pnl": _m(net_total),
        "return": _s(ret),
        "win_rate": _s(Decimal(len(wins)) / n) if n else None,
        "pf": _s(pf), "expectancy": _m(net_total / n) if n else None,
        "mdd": _s(mdd), "mdd_amount": _m(mdd_amount),
        "avg_mfe": None, "avg_mae": None,
        "avg_holding_seconds": (sum(holdings) / len(holdings)) if holdings else None,
        "initial_equity": _m(initial),
        "equity_change": _m(equity_change),
        "ledger_vs_equity_gap": _m(equity_change - net_total)
            if (equity_change is not None and net_total is not None and not book.open_positions) else None,
        "accounting_mix": sorted({t.get("accounting") or "UNKNOWN" for t in closed}),
        "na": na,
    }


def combined_daily(books: Sequence[Book]) -> tuple[Decimal | None, list[DailyPoint]]:
    """Two books' daily PnL added on the sum of their initial equity, from the earliest start.

    A book with no row on a date contributes 0 that day: it held cash, including before its own
    paper start. The base is therefore the full sum of both initial equities from the first day.
    """
    if not books or any(b.initial_equity is None for b in books):
        return None, []
    by_day: dict[str, Decimal] = {}
    for book in books:
        for point in book.daily:
            by_day[point.day] = by_day.get(point.day, ZERO) + point.pnl
    base = sum((b.initial_equity for b in books), ZERO)
    points: list[DailyPoint] = []
    equity = base
    for day in sorted(by_day):
        equity += by_day[day]
        points.append(DailyPoint(day, by_day[day], equity))
    return base, points


def combined_metrics(books: Sequence[Book]) -> dict[str, Any]:
    base, daily = combined_daily(books)
    pooled = Book("COMBINED", base, [t for b in books for t in b.trades], daily,
                  sorted({s for b in books for s in b.operating_sessions}),
                  [p for b in books for p in b.open_positions])
    out = strategy_metrics(pooled)
    out["definition"] = "A+E 거래를 합치고 일별 손익을 두 장부 초기자산 합 위에 더한 회계 합계(배분 아님)"
    return out


# -- portfolio simulation baseline -----------------------------------------------------------------

def daily_returns(book: Book) -> dict[str, Decimal]:
    """Each day's return on the equity the book started that day with."""
    out: dict[str, Decimal] = {}
    previous = book.initial_equity
    for point in book.daily:
        if previous:
            out[point.day] = point.pnl / previous
        previous = point.equity
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < MIN_CORRELATION_DAYS:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sqrt(sxx * syy)


def _overlap(left: set[str], right: set[str]) -> dict[str, Any]:
    union = left | right
    return {"both": len(left & right), "either": len(union), "a_only": len(left - right),
            "e_only": len(right - left),
            "share_of_either": _s(Decimal(len(left & right)) / len(union)) if union else None}


def portfolio_baseline(a: Book, e: Book, *, weight_a: Decimal = Decimal("0.5"),
                       weight_e: Decimal = Decimal("0.5"),
                       sessions: Sequence[str] | None = None) -> dict[str, Any]:
    """A 50/50 risk-budget blend of A and E daily returns, simulation only.

    The window is the dates on which *both* strategies were operating: from the later of the two
    first operating sessions through the earlier of the two last ones. Inside it a strategy with no
    PnL row on a date returned 0 that day. Outside it the blend is undefined and not computed.
    """
    ra, re_ = daily_returns(a), daily_returns(e)
    a_sessions = sorted(set(a.operating_sessions) | {p.day for p in a.daily})
    e_sessions = sorted(set(e.operating_sessions) | {p.day for p in e.daily})
    na: dict[str, str] = {"sector_overlap": "SECTOR_NOT_RECORDED_IN_LEDGER"}
    result: dict[str, Any] = {
        "mode": "SIMULATION_BASELINE_ONLY",
        "risk_budget": {a.strategy_id: str(weight_a), e.strategy_id: str(weight_e)},
        "allocation_rule": "NOT_IN_USE",
        "definition": ("combined_return_t = w_A * r_A,t + w_E * r_E,t, r = 그날 손익 / 그날 시작 자산, "
                       "두 전략이 모두 운영된 기간만, 기간 안에서 행이 없는 날은 0"),
    }
    if not a_sessions or not e_sessions:
        na["window"] = "A_AND_E_NOT_BOTH_OPERATING"
        return result | {"window": None, "days": 0, "equity_curve": [], "na": na} | _overlaps(a, e)
    start, end = max(a_sessions[0], e_sessions[0]), min(a_sessions[-1], e_sessions[-1])
    days = sorted(d for d in set(sessions or []) | set(a_sessions) | set(e_sessions) if start <= d <= end)
    if not days:
        na["window"] = "NO_COMMON_OPERATING_WINDOW"
        return result | {"window": {"start": start, "end": end}, "days": 0, "equity_curve": [], "na": na} | _overlaps(a, e)
    curve, index, peak, mdd = [], Decimal(1), Decimal(1), ZERO
    xs, ys = [], []
    for day in days:
        r_a, r_e = ra.get(day, ZERO), re_.get(day, ZERO)
        combined = weight_a * r_a + weight_e * r_e
        index *= 1 + combined
        peak = max(peak, index)
        mdd = min(mdd, index / peak - 1)
        curve.append({"date": day, "r_a": _s(r_a), "r_e": _s(r_e), "combined": _s(combined), "index": _s(index)})
        xs.append(float(r_a)); ys.append(float(r_e))
    correlation = _pearson(xs, ys)
    if correlation is None:
        na["correlation"] = f"NEEDS_{MIN_CORRELATION_DAYS}_DAYS_WITH_VARIANCE"
    lose_a = {c["date"] for c in curve if Decimal(c["r_a"]) < 0}
    lose_e = {c["date"] for c in curve if Decimal(c["r_e"]) < 0}
    win_a = {c["date"] for c in curve if Decimal(c["r_a"]) > 0}
    win_e = {c["date"] for c in curve if Decimal(c["r_e"]) > 0}
    return result | {
        "window": {"start": days[0], "end": days[-1]}, "days": len(days),
        "combined_return": _s(index - 1), "combined_mdd": _s(mdd),
        "correlation": None if correlation is None else f"{correlation:.4f}",
        "losing_day_overlap": _overlap(lose_a, lose_e),
        "winning_day_overlap": _overlap(win_a, win_e),
        "equity_curve": curve, "na": na,
    } | _overlaps(a, e)


def _overlaps(a: Book, e: Book) -> dict[str, Any]:
    a_symbols = {t["symbol"] for t in a.trades}
    e_symbols = {t["symbol"] for t in e.trades}
    a_pairs = {(t.get("session"), t["symbol"]) for t in a.trades}
    e_pairs = {(t.get("session"), t["symbol"]) for t in e.trades}
    same_session = sorted(f"{s}:{sym}" for s, sym in a_pairs & e_pairs)
    return {"symbol_overlap": _overlap(a_symbols, e_symbols) | {"symbols": sorted(a_symbols & e_symbols)},
            "same_session_symbol_collisions": same_session,
            "sector_overlap": None}


def exposure(books: Sequence[Book]) -> dict[str, Any]:
    """Open positions per strategy, and the portfolio total per symbol. Each strategy's rows are
    kept as they are; the total only adds quantities and cost bases across them."""
    per_symbol: dict[str, dict[str, Any]] = {}
    per_strategy: dict[str, Any] = {}
    for book in books:
        notional = ZERO
        for position in book.open_positions:
            qty = _d(position.get("quantity")) or ZERO
            cost = _d(position.get("cost_basis")) or ZERO
            notional += cost
            row = per_symbol.setdefault(position["symbol"], {"symbol": position["symbol"], "quantity": ZERO,
                                                             "cost_basis": ZERO, "by_strategy": {}})
            row["quantity"] += qty
            row["cost_basis"] += cost
            row["by_strategy"][book.strategy_id] = {"quantity": str(qty), "cost_basis": _m(cost)}
        per_strategy[book.strategy_id] = {"open_positions": len(book.open_positions), "cost_basis": _m(notional)}
    total = sum((r["cost_basis"] for r in per_symbol.values()), ZERO)
    return {"per_strategy": per_strategy,
            "per_symbol": [r | {"quantity": str(r["quantity"]), "cost_basis": _m(r["cost_basis"])}
                           for r in sorted(per_symbol.values(), key=lambda r: r["symbol"])],
            "total_cost_basis": _m(total)}


def session_dates(values: Iterable[str | date]) -> list[str]:
    return sorted({v.isoformat() if isinstance(v, date) else str(v) for v in values})
