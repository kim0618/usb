"""Exact forward outcomes for one H issuer, or PENDING. Never an approximation.

A horizon matures on one specific session: the nth regular trading session after the launch baseline
session, as ``app.market.MarketCalendar`` numbers them. Until the store holds that session's bar the
horizon is ``PENDING``; if the store is missing a session *inside* the window the horizon is
``INCOMPLETE`` and names which. Substituting the last available price - the single easiest way to
turn a forward observation into a flattering backtest - is not implemented here at all, so it cannot
be reached by accident.

Returns are measured from the launch baseline close, not from the decision-session close. The two
differ by whatever the issuer did between the thesis being settled (2026-09-16) and the shadow being
launched, and that interval is reported separately as pre-launch drift: it is context, never a
forward result. The thesis's own upside figures stay anchored where D5 computed them.

Path metrics read the daily high and low of the sessions inside the window, so a target touched
intraday counts as touched. A target the issuer never traded at does not count because the close
happened to be near it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from app.market.calendar import MarketCalendar
from app.strategies.h_forward import contract as C
from app.strategies.h_forward import prices as PR

MATURED, PENDING, INCOMPLETE = "MATURED", C.PENDING, "INCOMPLETE"
NO_BASELINE = "NO_BASELINE"


@dataclass(frozen=True)
class Levels:
    """The thesis levels a forward path is checked against, as D5 published them."""

    tp1: float | None
    tp2: float | None
    bear: float | None
    bear_unavailable_reason: str | None = None


def horizon_sessions(baseline: str, horizon: int, *, calendar: MarketCalendar | None = None) -> list[str]:
    """Sessions 1..n after the baseline, by calendar. The nth is where the horizon matures."""
    cal = calendar or MarketCalendar()
    out: list[str] = []
    day = cal.next_trading_day(date.fromisoformat(baseline))
    for _ in range(horizon):
        out.append(day.isoformat())
        day = cal.next_trading_day(day)
    return out


def _ret(now: float | None, then: float) -> float | None:
    return None if now is None or then == 0 else now / then - 1.0


def outcome(*, ticker: str, baseline: str, horizon: int, levels: Levels,
            security: Mapping[str, PR.Bar], benchmark: Mapping[str, PR.Bar],
            calendar: MarketCalendar | None = None) -> dict[str, Any]:
    """One (issuer, horizon) forward observation."""
    base_bar = security.get(baseline)
    base_bench = benchmark.get(baseline)
    head = {"ticker": ticker, "horizon_sessions": horizon, "baseline_session": baseline,
            "benchmark": C.benchmark()}
    if base_bar is None or base_bench is None:
        return head | {"state": NO_BASELINE, "maturity_session": None,
                       "reason": "the launch baseline session is not in the price store"}
    window = horizon_sessions(baseline, horizon, calendar=calendar)
    maturity = window[-1]
    missing_inside = [s for s in window if s not in security or s not in benchmark]
    head |= {"maturity_session": maturity}
    observed = [s for s in window if s in security and s in benchmark]
    if maturity not in security or maturity not in benchmark:
        return head | {"state": PENDING, "sessions_observed": len(observed),
                       "sessions_required": horizon,
                       "reason": f"session {maturity} has not been stored"}
    if missing_inside:
        return head | {"state": INCOMPLETE, "missing_sessions": missing_inside,
                       "reason": "a session inside the window is absent from the store; "
                                 "the last available price is never substituted for it"}

    base, bench_base = base_bar.close, base_bench.close
    end = security[maturity].close
    security_return = _ret(end, base)
    benchmark_return = _ret(benchmark[maturity].close, bench_base)
    highs = [security[s].high for s in window if security[s].high is not None]
    lows = [security[s].low for s in window if security[s].low is not None]
    mfe = _ret(max(highs), base) if highs else None
    mae = _ret(min(lows), base) if lows else None
    return head | {
        "state": MATURED,
        "sessions_observed": len(observed), "sessions_required": horizon,
        "baseline_price": base, "maturity_price": end,
        "security_return": security_return,
        "benchmark_return": benchmark_return,
        "excess_return": (None if security_return is None or benchmark_return is None
                          else security_return - benchmark_return),
        "mfe": mfe, "mae": mae,
        "tp1_hit": _touched(highs, levels.tp1, above=True),
        "tp2_hit": _touched(highs, levels.tp2, above=True),
        "bear_breach": _touched(lows, levels.bear, above=False),
        "bear_na_reason": levels.bear_unavailable_reason if levels.bear is None else None,
        "path_fields": None if highs and lows else "DAILY_HIGH_LOW_NOT_STORED",
    }


def _touched(extremes: Sequence[float], level: float | None, *, above: bool) -> bool | None:
    """Did the path reach ``level``? ``None`` when there is no level or no path to read."""
    if level is None or not extremes:
        return None
    return any(value >= level for value in extremes) if above else any(value <= level for value in extremes)


def all_horizons(*, ticker: str, baseline: str, levels: Levels,
                 security: Mapping[str, PR.Bar], benchmark: Mapping[str, PR.Bar],
                 calendar: MarketCalendar | None = None) -> dict[str, dict[str, Any]]:
    cal = calendar or MarketCalendar()
    return {f"{h}D": outcome(ticker=ticker, baseline=baseline, horizon=h, levels=levels,
                             security=security, benchmark=benchmark, calendar=cal)
            for h in C.horizons()}


def pre_launch_drift(*, decision_session: str, decision_close: float | None, baseline: str,
                     security: Mapping[str, PR.Bar]) -> dict[str, Any]:
    """What the issuer did between the thesis and the launch. Context only, never an outcome.

    It is reported because a reader comparing a frozen TP1 upside against today's price deserves to
    know the thesis price is three weeks old, and it is labelled because this interval is already in
    the past at launch and so can never be forward evidence.
    """
    base_bar = security.get(baseline)
    return {"decision_session": decision_session, "decision_close": decision_close,
            "baseline_session": baseline,
            "baseline_close": None if base_bar is None else base_bar.close,
            "return_since_decision": (None if base_bar is None or not decision_close
                                      else base_bar.close / decision_close - 1.0),
            "is_forward_evidence": False,
            "note": "이 구간은 launch 시점에 이미 과거다. forward 성과는 baseline 세션 이후만 센다"}
