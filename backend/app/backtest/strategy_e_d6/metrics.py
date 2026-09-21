"""E-D6 metrics, bootstrap, chronological blocks, diagnostics and the frozen verdict.

Every formula is the one written into ``strategy_e_backtest_rules_v1.json`` before any result
existed. Undefined values are ``None``; nothing is manufactured to fill them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import math
from typing import Any

import numpy as np


ANNUALIZATION = 252
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20260921
BLOCKS = 4
MIN_COVERAGE = 0.95
PRICE_BUCKETS = ((5.0, 10.0), (10.0, 20.0), (20.0, 50.0), (50.0, 100.0), (100.0, 200.0),
                 (200.0, None))
#: Research's reported previous-day dollar-volume buckets (e1_h5_confirmation_rules_v1.json).
LIQUIDITY_BUCKETS = ((5e6, 2e7), (2e7, 1e8), (1e8, 5e8), (5e8, None))

PASS = "E-D6 PASS — READY FOR PAPER / FORWARD"
FAIL = "E-D6 FAIL"
INCONCLUSIVE_STATISTICAL = "E-D6 INCONCLUSIVE — STATISTICAL"
INCONCLUSIVE_DATA = "E-D6 INCONCLUSIVE — DATA QUALITY"
INTEGRITY_BLOCK = "E-D6 BLOCKED — INTEGRITY"


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def profit_factor(returns: np.ndarray) -> float | None:
    gains = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    return _finite(gains / losses) if losses > 0 else None


def trade_stats(returns: np.ndarray) -> dict[str, Any]:
    if returns.size == 0:
        return {"trades": 0, "mean": None, "median": None, "win_rate": None,
                "profit_factor": None}
    return {"trades": int(returns.size), "mean": float(returns.mean()),
            "median": float(np.median(returns)), "win_rate": float((returns > 0).mean()),
            "profit_factor": profit_factor(returns)}


def equity_stats(session_returns: np.ndarray) -> dict[str, Any]:
    """Compounded equity from 1 over every session, no-trade sessions included as 0."""
    n = session_returns.size
    if n == 0:
        return {"cumulative_return": None, "maximum_drawdown": None, "sharpe": None,
                "sortino": None}
    equity = np.cumprod(1.0 + session_returns)
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    mean = float(session_returns.mean())
    std = float(session_returns.std(ddof=1)) if n >= 2 else float("nan")
    downside = math.sqrt(float(np.mean(np.minimum(session_returns, 0.0) ** 2)))
    return {
        "cumulative_return": float(equity[-1] - 1.0),
        "maximum_drawdown": float(np.min(equity / peak - 1.0)),
        "sharpe": _finite(math.sqrt(ANNUALIZATION) * mean / std) if n >= 2 and std > 0 else None,
        "sortino": _finite(math.sqrt(ANNUALIZATION) * mean / downside) if downside > 0 else None,
    }


def scenario_summary(trade_returns: np.ndarray, session_returns: np.ndarray,
                     active: np.ndarray) -> dict[str, Any]:
    return {
        **trade_stats(trade_returns),
        "sessions": int(session_returns.size),
        "active_sessions": int(active.sum()),
        "all_session_mean": float(session_returns.mean()) if session_returns.size else None,
        "active_session_mean": float(session_returns[active].mean()) if active.any() else None,
        **equity_stats(session_returns),
    }


def bootstrap_mean_ci(session_returns: np.ndarray, *, replicates: int = BOOTSTRAP_REPLICATES,
                      seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """IID resampling of whole session portfolio returns; 2.5/97.5 percentile interval."""
    rng = np.random.default_rng(seed)
    n = session_returns.size
    means = np.empty(replicates)
    for start in range(0, replicates, 1000):
        stop = min(start + 1000, replicates)
        means[start:stop] = session_returns[rng.integers(0, n, size=(stop - start, n))].mean(axis=1)
    return {"method": "IID session bootstrap", "replicates": replicates, "seed": seed,
            "mean": float(session_returns.mean()),
            "ci_low": float(np.percentile(means, 2.5)),
            "ci_high": float(np.percentile(means, 97.5)),
            "p_mean_le_zero": float((means <= 0).mean())}


def chronological_blocks(sessions: Sequence[str]) -> list[list[str]]:
    """Four contiguous blocks of the ordered session list, counts differing by at most one."""
    ordered = sorted(sessions)
    return [list(block) for block in np.array_split(np.array(ordered, dtype=object), BLOCKS)]


def block_table(sessions: Sequence[str], returns: Mapping[str, float],
                trades_by_session: Mapping[str, int]) -> list[dict[str, Any]]:
    table = []
    for i, block in enumerate(chronological_blocks(sessions), start=1):
        values = np.array([returns[s] for s in block])
        mean = float(values.mean()) if values.size else None
        table.append({"block": i, "first": block[0] if block else None,
                      "last": block[-1] if block else None, "sessions": len(block),
                      "active_sessions": int(sum(trades_by_session.get(s, 0) > 0 for s in block)),
                      "trades": int(sum(trades_by_session.get(s, 0) for s in block)),
                      "mean": mean, "positive": mean is not None and mean > 0})
    return table


def period_table(sessions: Sequence[str], returns: Mapping[str, Mapping[str, float]],
                 trades_by_session: Mapping[str, int], period: str) -> list[dict[str, Any]]:
    """Per month or quarter: activity, compounded 0bp and 10bp, running 10bp cumulative."""
    def key(session: str) -> str:
        if period == "month":
            return session[:7]
        return f"{session[:4]}Q{(int(session[5:7]) - 1) // 3 + 1}"

    groups: dict[str, list[str]] = defaultdict(list)
    for session in sorted(sessions):
        groups[key(session)].append(session)
    running = 1.0
    table = []
    for name in sorted(groups):
        members = groups[name]
        gross = float(np.prod([1.0 + returns["GROSS_0BP"][s] for s in members]) - 1.0)
        primary = float(np.prod([1.0 + returns["COST_10BP"][s] for s in members]) - 1.0)
        running *= 1.0 + primary
        table.append({period: name, "sessions": len(members),
                      "active_sessions": int(sum(trades_by_session.get(s, 0) > 0 for s in members)),
                      "trades": int(sum(trades_by_session.get(s, 0) for s in members)),
                      "return_0bp": gross, "return_10bp": primary,
                      "cumulative_10bp": running - 1.0})
    return table


def _bucket_label(low: float, high: float | None) -> str:
    return f"{low:g}+" if high is None else f"{low:g}-{high:g}"


def bucket_table(values: np.ndarray, gross: np.ndarray, net: np.ndarray,
                 buckets: Sequence[tuple[float, float | None]]) -> list[dict[str, Any]]:
    """Left-inclusive/right-exclusive buckets; rows outside every bucket are reported, not dropped."""
    assigned = np.zeros(values.size, dtype=bool)
    table = []
    for low, high in buckets:
        member = np.isfinite(values) & (values >= low) & (True if high is None else values < high)
        member = np.asarray(member, dtype=bool)
        assigned |= member
        table.append({"bucket": _bucket_label(low, high), **_bucket_row(gross[member], net[member])})
    if (~assigned).any():
        table.append({"bucket": "outside declared buckets", **_bucket_row(gross[~assigned],
                                                                          net[~assigned])})
    return table


def _bucket_row(gross: np.ndarray, net: np.ndarray) -> dict[str, Any]:
    return {"trades": int(gross.size),
            "gross_mean": float(gross.mean()) if gross.size else None,
            "net_10bp_mean": float(net.mean()) if net.size else None,
            "win_rate_10bp": float((net > 0).mean()) if net.size else None,
            "profit_factor_10bp": profit_factor(net) if net.size else None}


def concentration(symbols: Sequence[str], contributions: np.ndarray) -> dict[str, Any]:
    """Symbol shares of the summed weighted session contributions, plus trade-count HHI."""
    by_symbol: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for symbol, value in zip(symbols, contributions):
        by_symbol[symbol] += float(value)
        counts[symbol] += 1
    total = float(sum(by_symbol.values()))
    ranked = sorted(by_symbol.items(), key=lambda item: (-item[1], item[0]))
    trades = sum(counts.values())

    def top(k: int) -> dict[str, Any]:
        value = float(sum(v for _, v in ranked[:k]))
        return {"symbols": [s for s, _ in ranked[:k]], "contribution": value,
                "share_of_total": (value / total) if total > 0 else None}

    return {
        "unique_symbols": len(by_symbol), "trades": trades,
        "total_contribution": total,
        "top1": top(1), "top5": top(5), "top10": top(10),
        "hhi_trade_count": float(sum((c / trades) ** 2 for c in counts.values())) if trades else None,
        "trades_per_symbol": {"mean": trades / len(counts) if counts else None,
                              "max": max(counts.values()) if counts else None,
                              "median": float(np.median(list(counts.values()))) if counts else None},
        "share_of_total_note": "null when the total contribution is not positive",
    }


def verdict(*, integrity: bool, coverage: float | None, mean_10bp: float,
            profit_factor_10bp: float | None, ci_low: float, positive_blocks: int) -> dict[str, Any]:
    """The frozen precedence: integrity, data quality, economics, statistics, pass."""
    gates = {
        "integrity": integrity,
        "data_quality": coverage is not None and coverage >= MIN_COVERAGE,
        "net_economics": mean_10bp > 0 and profit_factor_10bp is not None and profit_factor_10bp > 1.0,
        "statistical_support": ci_low > 0,
        "chronological_robustness": positive_blocks >= 3,
    }
    if not gates["integrity"]:
        final = INTEGRITY_BLOCK
    elif not gates["data_quality"]:
        final = INCONCLUSIVE_DATA
    elif not gates["net_economics"]:
        final = FAIL
    elif not (gates["statistical_support"] and gates["chronological_robustness"]):
        final = INCONCLUSIVE_STATISTICAL
    else:
        final = PASS
    return {"gates": gates, "verdict": final}
